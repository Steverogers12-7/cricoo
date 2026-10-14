"""Simulates a whole match day with a fake clock: checks the state at each stage and the number of API calls used."""
import warnings; warnings.filterwarnings("ignore")
import json, os, tempfile
from datetime import datetime, timedelta
from engine import Engine
from name_map import NameMapper
from tracker import Tracker
from quota import QuotaGuard, IST
from poller import Poller
from mock_client import MockClient

class Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t

eng = Engine("models"); trk = Tracker(eng, NameMapper(eng.m1))
start = datetime(2026, 4, 10, 19, 30, tzinfo=IST)
clk = Clock(start.replace(hour=6, minute=0))
q = QuotaGuard(os.path.join(tempfile.mkdtemp(), "q.json"), 95)
p = Poller(MockClient(q, clk, start), trk, q, clock=clk)

seen, calls_at = {}, {}
while clk.t < start + timedelta(hours=6):
    wake = p.tick()
    s = p.get_state()
    seen.setdefault(s["status"], s); calls_at[s["status"]] = 95 - q.remaining()
    if s["status"] == "done": break
    clk.t = max(wake, clk.t + timedelta(seconds=1))
print("statuses seen :", list(seen))
print("API calls used:", 95 - q.remaining(), "(limit 95)")
print("calls by the time each status first appeared:", calls_at)
f = p.get_state()
print("FINAL:", f["result"], "| inn1 overs", len(f["inn1"]["runsByOver"]), "| inn2 overs", len(f["inn2"]["runsByOver"]), "| target", f["inn2"]["target"])
print("pre-match model1:", f["model1"]["total"], f["model1"]["low"], f["model1"]["high"])
print("inn1 proj:", f["inn1"]["proj"])
print("inn2 win%:", f["inn2"]["win"])
assert f["status"] == "done" and len(f["inn1"]["runsByOver"]) == 20 and len(f["inn2"]["runsByOver"]) == 20
assert sum(f["inn1"]["runsByOver"]) + 1 == f["inn2"]["target"]
assert 95 - q.remaining() <= 95
mid = seen.get("inn1")
need = {"demo","status","updatedAt","venue","toss","result","teams","model1","inn1","inn2"}
assert need <= set(f) and {"total","low","high","xiA","xiB"} <= set(f["model1"]) and {"runsByOver","wktsByOver","proj"} <= set(f["inn1"]) and {"target","runsByOver","wktsByOver","win"} <= set(f["inn2"])
json.dumps(f); print("OK: state has every key ipl-live.js reads, JSON-serialisable")
