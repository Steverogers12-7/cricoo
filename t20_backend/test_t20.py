"""T20 backend tests.  T20_MODEL=<path to inplay_t20_v2.pkl> python test_t20.py
 A engine == training-style pandas prep   B +EARLY_ADD rule   C venue/team mapping   D whole match through the REAL ApiClient + poller
 + tracker (only HTTP is faked)   E Flask routes"""
import os, sys, json, tempfile, warnings
warnings.filterwarnings("ignore")
os.environ.update(SERIES_FILTER="", MATCH_TYPE="T20", EXCLUDE_SERIES="women")      # same defaults as t20_app.py
from datetime import datetime, timedelta
import numpy as np, pandas as pd
import api_client as A
from quota import QuotaGuard, IST
from poller import Poller
from t20_engine import T20Engine
from t20_tracker import T20Tracker
from mock_client import D, XI_A, XI_B

E = T20Engine()
print("model:", os.path.basename(E.model_path), "| early_add:", E.early_add, "| early_balls:", E.early_balls, "| bands:", E.bands is not None)

def entries(runs, wks):
    out, cr, cw = [], 0, 0
    for k, (r, w) in enumerate(zip(runs, wks), 1):
        cr += r; cw += w; out.append((6 * k, cr, cw))
    return out
ent = entries(D["runs1"], D["wk1"])
BAT, BOWL, VEN = "India", "Australia", "Melbourne Cricket Ground"

# ---------- A: parity with the training-style prep (ball level, groupby shift(30)) ----------
rows = []
cr = cw = 0
for k, (r, w) in enumerate(zip(D["runs1"], D["wk1"])):
    for i in range(6):
        cr += r // 6 + (1 if i < r % 6 else 0); cw += 1 if i < w else 0
        rows.append({"match_id": 1, "batting_team": BAT, "bowling_team": BOWL, "venue": VEN, "ball_no": 6 * k + i + 1, "wicket": cw, "current_score": cr})
df = pd.DataFrame(rows)
g = df.groupby("match_id")
df["runs_last_30"] = df["current_score"] - g["current_score"].shift(30).fillna(0)
df["wkts_last_30"] = df["wicket"] - g["wicket"].shift(30).fillna(0)
df["balls_remaining"] = 120 - df["ball_no"]; df["crr"] = df["current_score"] / df["ball_no"] * 6
v_used, _ = E.venue(VEN); b_used, _ = E.team(BAT, E.keep_bat); o_used, _ = E.team(BOWL, E.keep_bowl)
for c, v in (("batting_team", b_used), ("bowling_team", o_used), ("venue", v_used)):
    df[c] = pd.Categorical([v] * len(df), categories=E.cats[c])
ref = (np.maximum(E.model.predict(df[E.F]), 0) + df["current_score"].values)[5::6]          # rows at ball 6,12,...,120
ser = E.inn1_series(ent, b_used, o_used, v_used)
d = np.abs(np.array([s["raw"] for s in ser]) - ref).max()
print(f"A parity: max |engine - training-style prep| = {d:.2e}  (venue->{v_used}, bat->{b_used}, bowl->{o_used})")
assert d < 1e-3

# ---------- B: +EARLY_ADD only before 5 overs ----------
for k, s in enumerate(ser):
    expect = k < 4
    assert s["early"] == expect, (k, s["early"])
    assert abs(s["point"] - s["raw"] - (E.early_add if expect else 0)) < 1e-9
print("B early rule OK: overs 1-4 get +%g, over 5 onwards unchanged" % E.early_add)
print("  raw  :", [round(s["raw"]) for s in ser[:7]])
print("  shown:", [round(s["point"]) for s in ser[:7]])
base = E.baseline(b_used, o_used, v_used)
print("  pre-match baseline:", base)
assert base["low"] <= base["total"] <= base["high"] and abs(base["total"] - round(base["raw"] + E.early_add)) <= 1

# ---------- C: names ----------
print("C venues (API string -> canonical -> model value):")
for n in ["Rawalpindi Cricket Stadium, Rawalpindi", "Melbourne Cricket Ground, Melbourne", "Dubai International Cricket Stadium, Dubai",
          "Eden Gardens, Kolkata", "M.Chinnaswamy Stadium, Bengaluru", "Narendra Modi Stadium, Ahmedabad", "Sheikh Zayed Stadium, Abu Dhabi",
          "Gaddafi Stadium, Lahore", "Some Brand New Ground, Pune"]:
    used, can = E.venue(n); print(f"   {n:45s} -> {str(can):40s} -> {used}")
print("C teams:", {n: E.team(n, E.keep_bat) for n in ["India", "Pakistan", "Sri Lanka", "West Indies", "United Arab Emirates", "USA", "Royal Challengers Bengaluru"]})
assert E.venue("Rawalpindi Cricket Stadium, Rawalpindi")[1] == "Rawalpindi Cricket Stadium"
assert E.team("India", E.keep_bat) == ("India", True) and E.team("Royal Challengers Bengaluru", E.keep_bat) == ("Other", False)

# ---------- D: whole match through the real ApiClient / poller / tracker ----------
START = datetime(2027, 4, 10, 19, 30, tzinfo=IST); OVER_S = 200
class Clock:
    def __init__(s, t): s.t = t
    def __call__(s): return s.t
clk = Clock(START.replace(hour=6))
def players(names): return [{"name": n.replace(" (WK)", ""), "play_role": "Wicketkeeper" if "(WK)" in n else "Batsman", "player_id": i} for i, n in enumerate(names)]
def tokens(r, w, wide=False):
    t = ["0"] * 6
    for i in range(w): t[i] = "W"
    rem, i = r, w
    while rem > 0 and i < 6:
        v = 6 if rem >= 6 else 4 if rem >= 4 else 1
        t[i] = str(v); rem -= v; i += 1
    if wide: t.insert(2, "WB")
    return t
def innings_items(runs, wks, n_done):
    cr = cw = 0; items = []
    for k in range(1, n_done + 1):
        cr += runs[k-1]; cw += wks[k-1]
        items.append({"team": {"score": f"{cr}-{cw}", "team": "X", "bowler": "b", "over": k, "runs": runs[k-1], "wkts": wks[k-1]}, "overs": tokens(runs[k-1], wks[k-1], wide=(k == 3))})
    return list(reversed(items))
def over_history():
    el = (clk() - START).total_seconds()
    if el < OVER_S: return {"status": False, "msg": "No Data Found.", "data": {}}
    d = {"1": innings_items(D["runs1"], D["wk1"], min(20, int(el // OVER_S)))}
    t2 = el - 20 * OVER_S - 900
    if t2 >= OVER_S: d["2"] = innings_items(D["runs2"], D["wk2"], min(20, int(t2 // OVER_S)))
    return {"msg": "Data found.", "status": True, "data": d}
class Resp:
    def __init__(s, js): s.js = js
    def raise_for_status(s): pass
    def json(s): return s.js
calls = []
class FakeSession:
    def request(self, method, url, data=None, headers=None, timeout=None):
        path = url.split("rapidapi.com", 1)[1]; calls.append(path)
        xi_out = clk() >= START - timedelta(minutes=25)
        base = {"date_wise": "10 Apr 2027, Saturday", "match_time": "07:30 PM"}
        if path == "/upcomingMatches":
            return Resp({"status": True, "msg": "ok", "data": [
                {**base, "match_id": 99999, "team_a_short": "SL", "team_b_short": "PAK", "series": "Sri Lanka tour of Pakistan, 2027", "venue": "Rawalpindi Cricket Stadium, Rawalpindi", "match_type": "T20"},
                {**base, "match_id": 1, "team_a_short": "INDW", "team_b_short": "AUSW", "series": "Australia Women tour of India", "venue": "x", "match_type": "T20"},
                {**base, "match_id": 2, "team_a_short": "NSW", "team_b_short": "TAS", "series": "Domestic One-Day Cup", "venue": "x", "match_type": "ODI"}]})
        if path == "/match/99999/playingXI":
            if not xi_out: return Resp({"status": False, "msg": "No Data Found.", "data": {}})
            return Resp({"status": True, "msg": "Data found.", "data": {"team_a": {"name": "Sri Lanka", "short_name": "SL", "player": players(XI_B)},
                                                                       "team_b": {"name": "Pakistan", "short_name": "PAK", "player": players(XI_A)}}})
        if path == "/match/99999":
            done = clk() >= START + timedelta(seconds=20 * OVER_S * 2 + 900)
            return Resp({"status": True, "msg": "ok", "data": {"team_a_short": "SL", "team_a": "Sri Lanka", "team_b_short": "PAK", "team_b": "Pakistan",
                "venue": "Rawalpindi Cricket Stadium, Rawalpindi", "series": "Sri Lanka tour of Pakistan, 2027",
                "toss": "Pakistan have won the toss and have opted to bat" if xi_out else "", "result": "Pakistan won by 4 runs" if done else ""}})
        if path == "/match/99999/overHistory": return Resp(over_history())
        raise AssertionError("unexpected URL " + path)

q = QuotaGuard(os.path.join(tempfile.mkdtemp(), "q.json"), 95)
cl = A.ApiClient(q); cl.s = FakeSession(); A.RAPID_KEY = "K"
p = Poller(cl, T20Tracker(E), q, clock=clk)
seen = {}
while clk.t < START + timedelta(hours=7):
    wake = p.tick(); s = p.get_state(); seen.setdefault(s["status"], s)
    if s["status"] == "done": break
    clk.t = max(wake, clk.t + timedelta(seconds=1))
f = p.get_state()
print("D statuses:", list(seen), "| API calls:", 95 - q.remaining(), "| schedule kept:", [(m["team_a"], m["team_b"]) for m in p.sched], "| last_error:", p.last_error)
assert p.last_error is None and f["status"] == "done" and f["result"] == "Pakistan won by 4 runs"
assert [(m["team_a"], m["team_b"]) for m in p.sched] == [("SL", "PAK")], "women's / ODI rows must be filtered out"
assert f["teams"]["a"]["code"] == "PAK", "Pakistan batted first"
pre = seen["pre"]; assert pre["model1"]["low"] < pre["model1"]["total"] <= pre["model1"]["high"], pre["model1"]
raw = [s["raw"] for s in E.inn1_series(ent, "Pakistan", "Sri Lanka", E.venue("Rawalpindi Cricket Stadium, Rawalpindi")[0])]
proj = f["inn1"]["proj"]
print("   raw  :", [round(x) for x in raw[:7]], "...", round(raw[-1]))
print("   proj :", proj[:7], "...", proj[-1], "| actual total", sum(D["runs1"]))
assert all(abs(proj[k] - round(raw[k] + E.early_add)) <= 1 for k in range(4)), "overs 1-4 must be raw + EARLY_ADD"
assert all(proj[k] == round(raw[k]) or abs(proj[k] - raw[k]) <= 0.51 for k in range(4, 19)), "from over 5 on: no addition"
assert proj[19] == sum(D["runs1"]) and f["inn1"]["range80"][19] == [proj[19], proj[19]]
assert f["inn2"]["win"] == [] and f["inn2"]["target"] == sum(D["runs1"]) + 1 and len(f["inn2"]["runsByOver"]) == 20
assert f["note"] == "Best accuracy after 5 overs" and f["scope"] == "innings1" and f["earlyAdd"] == E.early_add
need = {"demo", "status", "updatedAt", "venue", "toss", "result", "teams", "model1", "inn1", "inn2"}
assert need <= set(f) and {"total", "low", "high"} <= set(f["model1"]) and {"runsByOver", "wktsByOver", "proj"} <= set(f["inn1"]) and {"target", "runsByOver", "wktsByOver", "win"} <= set(f["inn2"])
json.dumps(f)
print("   model checks:", {k: p.t._pre["99999"][k] for k in ("venue_used", "venue_model", "bat", "bowl", "unresolved_players", "warnings")})

# ---------- E: Flask ----------
os.environ.update(MODE="mock", QUOTA_FILE=os.path.join(tempfile.mkdtemp(), "q.json"))
import t20_app
c = t20_app.app.test_client()
r1, r2 = c.get("/api/t20/live"), c.get("/health")
assert r1.status_code == 200 and "status" in r1.get_json() and r2.status_code == 200 and r2.get_json()["early_add"] == E.early_add
print("E flask: /api/t20/live ->", r1.get_json()["status"], "| /health keys:", sorted(r2.get_json())[:5], "...")
print("ALL T20 TESTS PASSED")
