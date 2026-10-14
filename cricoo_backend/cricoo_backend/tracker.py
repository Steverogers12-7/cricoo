"""Turns normalised match data into the exact `state` object that ipl-live.js renders (see its STATE SHAPE)."""
from dataclasses import dataclass, field
from typing import List, Optional
import time


@dataclass
class Innings:
    started: bool = False
    finished: bool = False
    entries: List[tuple] = field(default_factory=list)   # (legal_balls, cum_runs, cum_wkts) after every completed over
    balls: Optional[List[list]] = None                   # optional ball tokens per entry: ["1","W","Wd",...]


@dataclass
class MatchData:
    match_id: str
    date: str                      # YYYY-MM-DD (match day)
    venue: str
    team_a: dict                   # {"code","name"}  bats first
    team_b: dict                   # chases
    xi_a: List[str] = field(default_factory=list)   # API names
    xi_b: List[str] = field(default_factory=list)
    toss: str = ""
    done: bool = False
    result: str = ""
    inn1: Innings = field(default_factory=Innings)
    inn2: Innings = field(default_factory=Innings)


def _split(entries):
    runs, wk, pr, pw = [], [], 0, 0
    for _, r, w in entries:
        runs.append(r - pr); wk.append(w - pw); pr, pw = r, w
    return runs, wk


def status_of(m: MatchData):
    if m.done:
        return "done"
    if m.inn2.started:
        return "inn2"
    if m.inn1.finished:
        return "break"
    if m.inn1.started:
        return "inn1"
    return "pre"


def with_role(name, role):
    return name + (" (WK)" if str(role).lower().startswith("wicket") else "")


class Tracker:
    def __init__(self, engine, namemap):
        self.e, self.nm = engine, namemap
        self._pre = {}            # match_id -> model1 result (computed once: XI + venue never change)

    def _model1(self, m: MatchData):
        if m.match_id in self._pre:
            return self._pre[m.match_id]
        if not (m.xi_a and m.xi_b):
            return None
        ven = self.e.venue(m.venue)
        names, bad = self.nm.resolve_xi(m.xi_a, m.team_a["code"])
        r = self.e.pre_match(names, m.team_a["code"], ven, m.date)
        r["unresolved_players"] = bad
        r["venue_used"] = ven
        self._pre[m.match_id] = r
        return r

    def build(self, m: MatchData, now=None):
        st = status_of(m)
        pre = self._model1(m)
        s = {
            "demo": False, "status": st, "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now or time.time())),
            "venue": m.venue, "toss": m.toss, "result": m.result,
            "teams": {"a": m.team_a, "b": m.team_b},
            "model1": {"total": pre["total"], "low": pre["low"], "high": pre["high"],
                       "xiA": m.xi_a, "xiB": m.xi_b} if pre else {"total": 0, "low": 0, "high": 0, "xiA": [], "xiB": []},
            "inn1": {"runsByOver": [], "wktsByOver": [], "proj": [], "ballsByOver": []},
            "inn2": {"target": 0, "runsByOver": [], "wktsByOver": [], "win": [], "ballsByOver": []},
        }
        if not pre:
            return s
        b2 = pre["b2"]

        e1 = m.inn1.entries
        if e1:
            runs, wk = _split(e1)
            proj = [round(p["point"]) for p in self.e.inn1_series(e1, b2)]
            if m.inn1.finished:
                proj[-1] = e1[-1][1]                    # innings over: projection = actual total
            s["inn1"] = {"runsByOver": runs, "wktsByOver": wk, "proj": proj,
                         "ballsByOver": m.inn1.balls or []}
            if not m.inn1.balls:
                s["inn1"].pop("ballsByOver")

        if m.inn1.finished and e1:
            target = e1[-1][1] + 1
            s["inn2"]["target"] = target
            e2 = m.inn2.entries
            if e2:
                runs, wk = _split(e2)
                win = [round(100 * p, 1) for p in self.e.chase_series(e2, target, b2)]
                s["inn2"].update({"runsByOver": runs, "wktsByOver": wk, "win": win})
                if m.inn2.balls:
                    s["inn2"]["ballsByOver"] = m.inn2.balls
        return s
