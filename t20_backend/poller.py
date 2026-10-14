
"""Quota-aware scheduler. One background thread; visitors never trigger upstream calls.

Day plan (all times IST):
  06:00+   Fetch today's schedule.
           If empty, retry every 10 minutes.
  start-28 Prepare: playing XI and match info, retry every 4 minutes.
  live     Fetch over history and update score/predictions.
  done     Keep final match state visible until the next match is prepared.
"""

import threading
import time
import traceback
from datetime import datetime, timedelta

from api_client import QuotaExceeded, NoData
from quota import IST
from tracker import MatchData, Innings


def compute_result(md):
    """Calculate a result if matchInfo has no official result text."""
    if not (md.inn1.entries and md.inn2.entries):
        return ""

    r1 = md.inn1.entries[-1][1]
    r2 = md.inn2.entries[-1][1]
    w2 = md.inn2.entries[-1][2]

    if r2 > r1:
        return f"{md.team_b['name']} won by {10 - w2} wickets"
    if r2 == r1:
        return "Match tied"

    return f"{md.team_a['name']} won by {r1 - r2} runs"


PRE_MIN = 28
SCHEDULE_RETRY_MIN = 10

NONE_STATE = {
    "demo": False,
    "status": "none",
    "next": "Live scores and predictions appear here when the next T20 match starts.",
}


class Poller:
    def __init__(self, client, tracker, quota, clock=None):
        self.c = client
        self.t = tracker
        self.q = quota
        self.clock = clock or (lambda: datetime.now(IST))

        self.sched = []
        self.sched_day = None
        self.done_ids = set()
        self.cur = None

        self.state = dict(NONE_STATE)
        self.lock = threading.Lock()
        self.wake = self.clock()

        self.last_error = None
        self.stale = False
        self.calls_log = []

    # ------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------

    def get_state(self):
        with self.lock:
            state = dict(self.state)

        state["stale"] = self.stale
        return state

    def health(self):
        return {
            "quota_remaining": self.q.remaining(),
            "phase": self.cur["phase"] if self.cur else "idle",
            "match": self.cur["id"] if self.cur else None,
            "last_error": self.last_error,
            "model1_checks": (
                {
                    k: self.t._pre[self.cur["id"]].get(k)
                    for k in (
                        "venue_used",
                        "unresolved_players",
                        "warnings",
                    )
                }
                if self.cur and self.cur["id"] in self.t._pre
                else {}
            ),
            "schedule_today": [
                (
                    m["team_a"],
                    m["team_b"],
                    m["start"].strftime("%H:%M"),
                )
                for m in self.sched
            ],
        }

    def run_forever(self):
        """Keep the poller alive even when an individual tick fails."""
        print("[poller] thread started", flush=True)

        try:
            while True:
                try:
                    wake = self.tick()
                    sleep_seconds = max(
                        1.0,
                        min(30.0, (wake - self.clock()).total_seconds()),
                    )
                    time.sleep(sleep_seconds)

                except Exception as e:
                    self.last_error = f"run_forever: {e!r}"
                    self.stale = True
                    traceback.print_exc()
                    time.sleep(5)

        finally:
            print("[poller] thread exiting", flush=True)

    # ------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------

    def tick(self):
        now = self.clock()

        if now < self.wake:
            return self.wake

        try:
            self._tick(now)
            self.last_error = None
            self.stale = False

        except QuotaExceeded as e:
            self.last_error = f"quota: {e}"
            self.stale = True
            self.wake = now + timedelta(minutes=10)

        except NotImplementedError as e:
            self.last_error = f"not implemented: {e}"
            self.stale = True
            self.wake = now + timedelta(minutes=5)

        except Exception as e:
            self.last_error = repr(e)
            self.stale = True
            traceback.print_exc()
            self.wake = now + timedelta(minutes=2)

        return self.wake

    def _set(self, state):
        with self.lock:
            self.state = state

    def _refresh_schedule(self, now, day):
        """Fetch the schedule for today and report what the API returned."""
        print(
            f"[poller] fetching schedule for {day}",
            flush=True,
        )

        matches = self.c.upcoming()

        self.sched = [
            m
            for m in matches
            if m["start"].astimezone(IST).strftime("%Y-%m-%d") == day
        ]

        self.sched.sort(key=lambda m: m["start"])
        self.sched_day = day

        print(
            f"[poller] schedule_today={len(self.sched)} "
            f"matches={[(m['match_id'], m['team_a'], m['team_b']) for m in self.sched]}",
            flush=True,
        )

    def _tick(self, now):
        day = now.strftime("%Y-%m-%d")

        # Refresh on a new day, or retry when today's schedule is empty.
        if now.hour >= 6 and (
            self.sched_day != day or not self.sched
        ):
            self._refresh_schedule(now, day)

        # --------------------------------------------------------
        # Find the next eligible match
        # --------------------------------------------------------

        if self.cur is None:
            nxt = next(
                (
                    m
                    for m in self.sched
                    if m["match_id"] not in self.done_ids
                    and now < m["start"] + timedelta(hours=5)
                ),
                None,
            )

            if nxt is None:
                if not self.sched and now.hour >= 6:
                    # No matches returned: try fetching again in 10 minutes.
                    self.wake = now + timedelta(
                        minutes=SCHEDULE_RETRY_MIN
                    )
                    print(
                        "[poller] schedule empty; retrying in "
                        f"{SCHEDULE_RETRY_MIN} minutes",
                        flush=True,
                    )
                else:
                    # Today's schedule exists, but no eligible match remains.
                    self.wake = (
                        now.replace(
                            hour=6,
                            minute=0,
                            second=0,
                            microsecond=0,
                        )
                        + timedelta(
                            days=1 if now.hour >= 6 else 0
                        )
                    )

                if self.state.get("status") != "done":
                    self._set(dict(NONE_STATE))

                return

            opens = nxt["start"] - timedelta(minutes=PRE_MIN)

            if now < opens:
                self.wake = opens

                if self.state.get("status") != "done":
                    self._set(
                        {
                            **NONE_STATE,
                            "next": (
                                f"Next: {nxt['team_a']} vs "
                                f"{nxt['team_b']} at "
                                f"{nxt['start'].astimezone(IST).strftime('%H:%M')} IST"
                            ),
                        }
                    )

                return

            print(
                f"[poller] preparing match {nxt['match_id']}: "
                f"{nxt['team_a']} vs {nxt['team_b']}",
                flush=True,
            )

            self.cur = {
                "id": nxt["match_id"],
                "start": nxt["start"],
                "phase": "pre",
                "tries": 0,
                "md": None,
                "overs": -1,
                "ema": 210.0,
                "last_new": None,
                "miss": 0,
            }

        # --------------------------------------------------------
        # Pre-match: wait for playing XI and toss
        # --------------------------------------------------------

        cur = self.cur

        if cur["phase"] == "pre":
            prep = self.c.prepare(cur["id"], cur["start"])

            if prep is None:
                cur["tries"] += 1

                print(
                    f"[poller] match {cur['id']} not ready; "
                    f"attempt {cur['tries']}",
                    flush=True,
                )

                if cur["tries"] > 10:
                    self.done_ids.add(cur["id"])
                    self.cur = None
                    self.wake = now + timedelta(minutes=1)
                    return

                self.wake = now + timedelta(minutes=4)
                return

            cur["md"] = MatchData(
                match_id=cur["id"],
                date=now.strftime("%Y-%m-%d"),
                venue=prep["venue"],
                team_a=prep["team_a"],
                team_b=prep["team_b"],
                xi_a=prep["xi_a"],
                xi_b=prep["xi_b"],
                toss=prep["toss"],
            )

            cur["phase"] = "live"

            print(
                f"[poller] match {cur['id']} prepared; "
                "starting score polling",
                flush=True,
            )

            self._set(self.t.build(cur["md"], now.timestamp()))

            self.wake = max(
                now + timedelta(minutes=1),
                cur["start"] + timedelta(minutes=3),
            )
            return

        # --------------------------------------------------------
        # Live: fetch full over history
        # --------------------------------------------------------

        md = cur["md"]
        progress = self.c.progress(cur["id"])

        md.inn1 = Innings(**progress["inn1"])
        md.inn2 = Innings(**progress["inn2"])
        md.done = progress["done"]
        md.result = progress.get("result", "")

        if md.done and not md.result:
            try:
                md.result = self.c.result_text(cur["id"])
            except Exception:
                md.result = ""

            md.result = md.result or compute_result(md)

        elif (
            not md.done
            and cur["last_new"] is not None
            and now - cur["last_new"] > timedelta(minutes=60)
            and not cur.get("checked")
        ):
            cur["checked"] = True

            result = self.c.result_text(cur["id"])

            if result:
                md.done = True
                md.result = result

        self._set(self.t.build(md, now.timestamp()))

        # --------------------------------------------------------
        # Match completed
        # --------------------------------------------------------

        if md.done:
            print(
                f"[poller] match {cur['id']} completed: {md.result}",
                flush=True,
            )

            self.done_ids.add(cur["id"])
            self.cur = None
            self.wake = now + timedelta(minutes=1)
            return

        # --------------------------------------------------------
        # Schedule the next score poll
        # --------------------------------------------------------

        overs = len(md.inn1.entries) + len(md.inn2.entries)

        if overs > cur["overs"]:
            if cur["last_new"] is not None and overs > cur["overs"] >= 0:
                gap = (
                    (now - cur["last_new"]).total_seconds()
                    / max(1, overs - cur["overs"])
                )

                cur["ema"] = min(
                    420.0,
                    max(
                        120.0,
                        0.6 * cur["ema"] + 0.4 * gap,
                    ),
                )

            cur["last_new"] = now
            cur["overs"] = overs
            cur["miss"] = 0

            if md.inn1.finished and not md.inn2.started:
                delay = 7 * 60
            else:
                delay = max(
                    60.0,
                    cur["ema"] * self._step() - 10,
                )

        else:
            cur["miss"] += 1
            delay = (
                30.0
                if cur["miss"] <= 3
                else cur["ema"] * 0.5
            )

        self.wake = now + timedelta(seconds=delay)

    def _step(self):
        """Choose how many overs to cover per poll based on remaining quota."""
        left = max(
            1,
            len(
                [
                    m
                    for m in self.sched
                    if m["match_id"] not in self.done_ids
                ]
            ),
        )

        per = self.q.remaining() / left

        if per >= 52:
            return 1
        if per >= 30:
            return 2
        if per >= 18:
            return 3

        return 6
