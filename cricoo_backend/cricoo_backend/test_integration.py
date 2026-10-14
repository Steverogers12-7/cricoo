"""End-to-end: REAL ApiClient (RapidAPI URLs/headers/parsers) + poller + tracker + models. Only the HTTP layer is faked,
with responses shaped exactly like the real ones (upcomingMatches, playingXI, match info, overHistory incl. 'No Data Found',
partial in-progress over, wide tokens)."""
import warnings; warnings.filterwarnings("ignore")
import json, os, re, tempfile
from datetime import datetime, timedelta
import api_client as A
from engine import Engine
from name_map import NameMapper
from tracker import Tracker
from quota import QuotaGuard, IST
from poller import Poller
from mock_client import D, XI_A, XI_B

A.RAPID_KEY = "TEST-KEY"
START = datetime(2027, 4, 10, 19, 30, tzinfo=IST)
OVER_S = 200

class Clock:
    def __init__(s, t): s.t = t
    def __call__(s): return s.t
clk = Clock(START.replace(hour=6))

def plain(n): return n.replace(" (WK)", "")
def players(names): return [{"name": plain(n), "play_role": "Wicketkeeper" if "(WK)" in n else "Batsman", "player_id": i} for i, n in enumerate(names)]

def tokens(r, w, wide=False):
    t = ["0"] * 6
    for i in range(w): t[i] = "W"
    rem, i = r, w
    while rem > 0 and i < 6:
        v = 6 if rem >= 6 else 4 if rem >= 4 else 1
        t[i] = str(v); rem -= v; i += 1
    if wide: t.insert(2, "WB")
    return t

def innings_items(runs, wks, n_done, partial_balls):
    """newest over first, like the real API. partial_balls>0 adds the over that is in progress."""
    cr = cw = 0; items = []
    for k in range(1, n_done + 1):
        cr += runs[k-1]; cw += wks[k-1]
        items.append({"team": {"score": f"{cr}-{cw}", "team": "X", "bowler": "b", "cr_rate": "1.0", "over": k, "runs": runs[k-1], "wkts": wks[k-1]},
                      "overs": tokens(runs[k-1], wks[k-1], wide=(k == 3))})
    if partial_balls and n_done < 20:
        k = n_done + 1
        items.append({"team": {"score": f"{cr + 2}-{cw}", "team": "X", "bowler": "b", "cr_rate": "1.0", "over": k, "runs": 2, "wkts": 0},
                      "overs": ["1", "1", "0"][:partial_balls]})
    return list(reversed(items))

def over_history():
    el = (clk() - START).total_seconds()
    if el < OVER_S: raise A.NoData
    d = {}
    n1, p1 = min(20, int(el // OVER_S)), (el % OVER_S) > 60 and el < 20 * OVER_S
    d["1"] = innings_items(D["runs1"], D["wk1"], n1, 3 if p1 else 0)
    t2 = el - 20 * OVER_S - 900
    if t2 >= 0 or el >= 20 * OVER_S + 900:
        n2 = min(20, int(t2 // OVER_S)); p2 = (t2 % OVER_S) > 60 and n2 < 20
        if n2 or p2: d["2"] = innings_items(D["runs2"], D["wk2"], n2, 3 if p2 else 0)
    return {"msg": "Data found.", "status": True, "data": d}

calls = []
class Resp:
    def __init__(s, js): s.js = js
    def raise_for_status(s): pass
    def json(s): return s.js
class FakeSession:
    def request(self, method, url, data=None, headers=None, timeout=None):
        assert headers["x-rapidapi-key"] == "TEST-KEY" and headers["x-rapidapi-host"] == "cricket-live-line1.p.rapidapi.com", headers
        path = url.split("rapidapi.com", 1)[1]; calls.append(path)
        xi_out = clk() >= START - timedelta(minutes=25)
        if path == "/upcomingMatches":
            return Resp({"status": True, "msg": "ok", "data": [
                {"match_id": 99999, "team_a_short": "SRH", "team_b_short": "RCB", "series": "Indian Premier League 2027", "date_wise": "10 Apr 2027, Saturday", "match_time": "07:30 PM", "venue": "M.Chinnaswamy Stadium, Bengaluru", "match_type": "T20"},
                {"match_id": 5, "team_a_short": "PAK", "team_b_short": "SL", "series": "Other", "date_wise": "10 Apr 2027, Saturday", "match_time": "06:30 PM", "venue": "x", "match_type": "T20"}]})
        if path == "/match/99999/playingXI":
            if not xi_out: return Resp({"status": False, "msg": "No Data Found.", "data": {}})
            return Resp({"status": True, "msg": "Data found.", "data": {
                "team_a": {"name": "Sunrisers Hyderabad", "short_name": "SRH", "player": players(XI_B)},
                "team_b": {"name": "Royal Challengers Bengaluru", "short_name": "RCB", "player": players(XI_A)}}})
        if path == "/match/99999":
            done = clk() >= START + timedelta(seconds=20 * OVER_S * 2 + 900)
            return Resp({"status": True, "msg": "ok", "data": {"team_a_short": "SRH", "team_a": "Sunrisers Hyderabad", "team_b_short": "RCB", "team_b": "Royal Challengers Bengaluru",
                "venue": "M.Chinnaswamy Stadium, Bengaluru", "series": "Indian Premier League 2027",
                "toss": "Royal Challengers Bangalore have won the toss and have opted to bat" if xi_out else "",
                "result": "Royal Challengers Bangalore won by 4 runs" if done else ""}})
        if path == "/match/99999/overHistory":
            try: return Resp(over_history())
            except A.NoData: return Resp({"status": False, "msg": "No Data Found.", "data": {}})
        raise AssertionError("unexpected URL " + path)

eng = Engine("models"); trk = Tracker(eng, NameMapper(eng.m1))
q = QuotaGuard(os.path.join(tempfile.mkdtemp(), "q.json"), 95)
cl = A.ApiClient(q); cl.s = FakeSession()
p = Poller(cl, trk, q, clock=clk)

seen, max_over_gap = {}, 0
while clk.t < START + timedelta(hours=7):
    wake = p.tick(); s = p.get_state()
    seen.setdefault(s["status"], s)
    el = (clk.t - START).total_seconds()
    if s["status"] == "inn1":
        done_overs = int(el // OVER_S)
        assert len(s["inn1"]["runsByOver"]) <= done_overs, "published an over that is not finished yet"
        max_over_gap = max(max_over_gap, done_overs - len(s["inn1"]["runsByOver"]))
    if s["status"] == "done": break
    clk.t = max(wake, clk.t + timedelta(seconds=1))

f = p.get_state()
print("statuses:", list(seen), "| API calls:", 95 - q.remaining(), "| endpoints hit:", {k: calls.count(k) for k in sorted(set(calls))})
print("result:", f["result"], "| target:", f["inn2"]["target"], "| overs:", len(f["inn1"]["runsByOver"]), len(f["inn2"]["runsByOver"]), "| max lag (overs):", max_over_gap)
print("balls[2] (over 3, has a wide):", f["inn1"]["ballsByOver"][2])
print("health:", json.dumps(p.health()["model1_checks"]), "| last_error:", p.last_error)
assert f["status"] == "done" and f["result"].startswith("Royal Challengers Bangalore won by 4 runs")
assert len(f["inn1"]["runsByOver"]) == 20 and len(f["inn2"]["runsByOver"]) == 20 and sum(f["inn1"]["runsByOver"]) + 1 == f["inn2"]["target"]
assert f["inn1"]["ballsByOver"][2][2] == "Wd" and f["model1"]["xiA"][5].endswith("(WK)")
assert p.last_error is None

# ---- parser edge cases (real tokens from your SA vs AUS response)
assert A._legal(["0","0","0","NB","W","0","1"]) == 6 and A._legal(["1","2","0","0","0","0","WB"]) == 6 and A._legal(["0","0","1","3","0"]) == 5
assert [A._ball_token(t) for t in ["4LB","1NB","NB","WB","4BYE","W","6","1LB","4NB"]] == ["Lb4","Nb1","Nb","Wd","B4","W","6","Lb1","Nb4"]
def item(o, score, toks): return {"team": {"score": score, "over": o}, "overs": toks}
r = A.parse_over_history({"data": {"1": [item(2, "20-1", ["W", "4"]), item(1, "9-0", ["1", "4", "4", "0", "0", "0"])]}})   # all out? no: over in progress
assert r["inn1"]["entries"] == [(6, 9, 0)] and not r["inn1"]["finished"]
r = A.parse_over_history({"data": {"1": [item(2, "30-10", ["W", "W"]), item(1, "29-8", ["4"] * 6)]}})                      # all out mid-over
assert r["inn1"]["finished"] and r["inn1"]["entries"] == [(6, 29, 8), (8, 30, 10)], r
r = A.parse_over_history({"data": {"1": [item(1, "120-3", ["4"] * 6)], "2": [item(1, "125-1", ["4", "1"])]}})               # target passed mid-over
assert r["done"] and r["inn2"]["entries"] == [(2, 125, 1)] and r["inn1"]["finished"], r
print("OK: end-to-end + parser edge cases")
