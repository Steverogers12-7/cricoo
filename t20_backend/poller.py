"""Quota-aware scheduler. One background thread; visitors never trigger upstream calls.

Day plan (all times IST):
  06:00+   1 call  upcomingMatches            -> today's matches + start times
  start-28 prepare(): playing XI (+ match info once XI is out), retry every 4 min  (~2-6 calls)
  live     progress() = matchOverHistory (full history each time, so a late/missed poll loses nothing).
           Next poll is timed for when the next over should finish (learned from the match itself),
           with 1 over per poll normally, 2-3 overs per poll if the day's quota is getting tight.
  done     state stays visible until the next match is being prepared.
"""
import threading
import time
import traceback
from datetime import datetime, timedelta

from api_client import QuotaExceeded
from quota import IST
from tracker import MatchData, Innings
from api_client import NoData


def compute_result(md):
    """used only if matchInfo has no result text yet"""
    if not (md.inn1.entries and md.inn2.entries):
        return ""
    r1, r2, w2 = md.inn1.entries[-1][1], md.inn2.entries[-1][1], md.inn2.entries[-1][2]
    if r2 > r1:
        return f"{md.team_b['name']} won by {10 - w2} wickets"
    if r2 == r1:
        return "Match tied"
    return f"{md.team_a['name']} won by {r1 - r2} runs"

PRE_MIN = 28          # start looking for toss/XI this many minutes before the first ball
NONE_STATE = {"demo": False, "status": "none",
              "next": "Live scores and predictions appear here when the next IPL match starts."}


class Poller:
    def __init__(self, client, tracker, quota, clock=None):
        self.c, self.t, self.q = client, tracker, quota
        self.clock = clock or (lambda: datetime.now(IST))
        self.sched, self.sched_day, self.done_ids, self.cur = [], None, set(), None
        self.state, self.lock, self.wake = dict(NONE_STATE), threading.Lock(), self.clock()
        self.last_error, self.stale = None, False
        self.calls_log = []

    # ---------------------------------------------------------------- public
    def get_state(self):
        with self.lock:
            s = dict(self.state)
        s["stale"] = self.stale
        return s

    def health(self):
        return {"quota_remaining": self.q.remaining(), "phase": self.cur["phase"] if self.cur else "idle",
                "match": self.cur["id"] if self.cur else None, "last_error": self.last_error,
                "model1_checks": (self.t._pre.get(self.cur["id"], {}) if self.cur else {}) and
                         {k: self.t._pre[self.cur["id"]].get(k) for k in ("venue_used", "unresolved_players", "warnings")},
                "schedule_today": [(m["team_a"], m["team_b"], m["start"].strftime("%H:%M")) for m in self.sched]}

    def run_forever(self):
        while True:
            wake = self.tick()
            time.sleep(max(1.0, min(30.0, (wake - self.clock()).total_seconds())))

    # ---------------------------------------------------------------- one scheduling step
    def tick(self):
        now = self.clock()
        if now < self.wake:
            return self.wake
        try:
            self._tick(now)
            self.last_error, self.stale = None, False
        except QuotaExceeded as e:
            self.last_error, self.stale = f"quota: {e}", True
            self.wake = now + timedelta(minutes=10)
        except NotImplementedError as e:                      # parser not written yet: keep serving, retry later
            self.last_error, self.stale = f"not implemented: {e}", True
            self.wake = now + timedelta(minutes=5)
        except Exception as e:                                # network / parse problem: keep last good state, retry soon
            self.last_error, self.stale = repr(e), True
            traceback.print_exc()
            self.wake = now + timedelta(minutes=2)
        return self.wake

    def _set(self, s):
        with self.lock:
            self.state = s

    def _tick(self, now):
        day = now.strftime("%Y-%m-%d")
        if self.sched_day != day and now.hour >= 6:
            self.sched = [m for m in self.c.upcoming() if m["start"].astimezone(IST).strftime("%Y-%m-%d") == day]
            self.sched.sort(key=lambda m: m["start"])
            self.sched_day = day

        if self.cur is None:
            nxt = next((m for m in self.sched if m["match_id"] not in self.done_ids and now < m["start"] + timedelta(hours=5)), None)
            if nxt is None:
                self.wake = now.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1 if now.hour >= 6 else 0)
                if self.state.get("status") != "done":
                    self._set(dict(NONE_STATE))
                return
            opens = nxt["start"] - timedelta(minutes=PRE_MIN)
            if now < opens:
                self.wake = opens
                if self.state.get("status") != "done":
                    self._set({**NONE_STATE, "next": f"Next: {nxt['team_a']} vs {nxt['team_b']} at {nxt['start'].astimezone(IST).strftime('%H:%M')} IST"})
                return
            self.cur = {"id": nxt["match_id"], "start": nxt["start"], "phase": "pre", "tries": 0, "md": None,
                        "overs": -1, "ema": 210.0, "last_new": None, "miss": 0}

        cur = self.cur
        if cur["phase"] == "pre":
            prep = self.c.prepare(cur["id"], cur["start"])
            if prep is None:
                cur["tries"] += 1
                if cur["tries"] > 10:                         # XI never came out: give up on this match
                    self.done_ids.add(cur["id"]); self.cur = None; self.wake = now; return
                self.wake = now + timedelta(minutes=4)
                return
            cur["md"] = MatchData(match_id=cur["id"], date=now.strftime("%Y-%m-%d"), venue=prep["venue"],
                                  team_a=prep["team_a"], team_b=prep["team_b"], xi_a=prep["xi_a"], xi_b=prep["xi_b"],
                                  toss=prep["toss"])
            cur["phase"] = "live"
            self._set(self.t.build(cur["md"], now.timestamp()))
            self.wake = max(now + timedelta(minutes=1), cur["start"] + timedelta(minutes=3))   # first over done ~4 min after first ball
            return

        md = cur["md"]                                        # ---- live
        p = self.c.progress(cur["id"])
        md.inn1 = Innings(**p["inn1"])
        md.inn2 = Innings(**p["inn2"])
        md.done, md.result = p["done"], p.get("result", "")
        if md.done and not md.result:                         # 1 extra call: the official result line
            try:
                md.result = self.c.result_text(cur["id"])
            except (NoData, Exception):
                md.result = ""
            md.result = md.result or compute_result(md)
        elif (not md.done and cur["last_new"] is not None and now - cur["last_new"] > timedelta(minutes=60)
              and not cur.get("checked")):                    # nothing new for an hour: abandoned / no result?
            cur["checked"] = True
            res = self.c.result_text(cur["id"])
            if res:
                md.done, md.result = True, res
        self._set(self.t.build(md, now.timestamp()))

        if md.done:
            self.done_ids.add(cur["id"]); self.cur = None; self.wake = now + timedelta(minutes=1)
            return
        overs = len(md.inn1.entries) + len(md.inn2.entries)
        if overs > cur["overs"]:
            if cur["last_new"] is not None and overs > cur["overs"] >= 0:
                gap = (now - cur["last_new"]).total_seconds() / max(1, overs - cur["overs"])
                cur["ema"] = min(420.0, max(120.0, 0.6 * cur["ema"] + 0.4 * gap))
            cur["last_new"], cur["overs"], cur["miss"] = now, overs, 0
            if md.inn1.finished and not md.inn2.started:
                delay = 7 * 60                                # innings break
            else:
                delay = max(60.0, cur["ema"] * self._step() - 10)
        else:
            cur["miss"] += 1
            delay = 30.0 if cur["miss"] <= 3 else cur["ema"] * 0.5
        self.wake = now + timedelta(seconds=delay)

    def _step(self):
        """overs covered per poll, chosen from what is left of today's quota."""
        left = max(1, len([m for m in self.sched if m["match_id"] not in self.done_ids]))
        per = self.q.remaining() / left
        return 1 if per >= 52 else 2 if per >= 30 else 3 if per >= 18 else 6
