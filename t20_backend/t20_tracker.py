"""Turns normalised match data into the `state` object that t20-live.js renders.
Same interface as tracker.Tracker (poller.py only needs .build(md, now) and ._pre), but uses the innings-1 T20 model.
Innings 2 has NO prediction (the model is innings-1 only): inn2.win stays [] and the page hides the win bar."""
import time

from tracker import MatchData, status_of, _split          # reuse the dataclasses / helpers of the IPL backend

NOTE = "Best accuracy after 5 overs"


class T20Tracker:
    def __init__(self, engine):
        self.e = engine
        self._pre = {}            # match_id -> {...} (venue + teams never change during a match)

    def _model1(self, m: MatchData):
        if m.match_id in self._pre:
            return self._pre[m.match_id]
        e = self.e
        bat, ok_b = e.team(m.team_a["name"], e.keep_bat)
        bowl, ok_o = e.team(m.team_b["name"], e.keep_bowl)
        ven, canon = e.venue(m.venue)
        base = e.baseline(bat, bowl, ven)
        warns, unresolved = [], []
        if not ok_b:
            unresolved.append(m.team_a["name"])
        if not ok_o:
            unresolved.append(m.team_b["name"])
        if unresolved:
            warns.append(f"team(s) {unresolved} not in training list -> 'Other' category (weaker prediction)")
        if canon is None:
            warns.append(f"venue '{m.venue}' not recognised -> 'Other'")
        elif ven == "Other":
            warns.append(f"venue '{canon}' has <10 training matches -> 'Other'")
        if any("women" in str(x).lower() for x in (m.team_a["name"], m.team_b["name"], m.venue)):
            warns.append("women's match: this model was trained on men's T20 only")
        r = {**base, "bat": bat, "bowl": bowl, "venue_used": ven if ven != "Other" else (canon or "Other"),
             "venue_model": ven, "unresolved_players": unresolved, "warnings": warns}
        self._pre[m.match_id] = r
        return r

    def build(self, m: MatchData, now=None):
        st = status_of(m)
        pre = self._model1(m)
        s = {
            "demo": False, "status": st, "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now or time.time())),
            "date": m.date,
            "venue": m.venue, "toss": m.toss, "result": m.result,
            "teams": {"a": m.team_a, "b": m.team_b},
            "model1": {"total": pre["total"], "low": pre["low"], "high": pre["high"], "xiA": m.xi_a, "xiB": m.xi_b},
            "inn1": {"runsByOver": [], "wktsByOver": [], "proj": [], "range80": [], "ballsByOver": []},
            "inn2": {"target": 0, "runsByOver": [], "wktsByOver": [], "win": [], "ballsByOver": []},
            "note": NOTE, "scope": "innings1", "earlyAdd": self.e.early_add, "earlyOvers": self.e.early_balls // 6,
        }
        e1 = m.inn1.entries
        if e1:
            runs, wk = _split(e1)
            ser = self.e.inn1_series(e1, pre["bat"], pre["bowl"], pre["venue_model"])
            proj = [int(round(p["point"])) for p in ser]
            rng = [[int(round(p["low80"])), int(round(p["high80"]))] if p["low80"] is not None else None for p in ser]
            if m.inn1.finished:
                proj[-1] = e1[-1][1]                              # innings over: projection = actual total
                rng[-1] = [e1[-1][1], e1[-1][1]]
            s["inn1"] = {"runsByOver": runs, "wktsByOver": wk, "proj": proj, "range80": rng, "ballsByOver": m.inn1.balls or []}
            if not m.inn1.balls:
                s["inn1"].pop("ballsByOver")

        if m.inn1.finished and e1:
            s["inn2"]["target"] = e1[-1][1] + 1
            e2 = m.inn2.entries
            if e2:
                runs, wk = _split(e2)
                s["inn2"].update({"runsByOver": runs, "wktsByOver": wk})
                if m.inn2.balls:
                    s["inn2"]["ballsByOver"] = m.inn2.balls
        return s