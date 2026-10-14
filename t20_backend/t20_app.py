"""Flask entry point for the T20 (innings-1) in-play predictor.   gunicorn -w 1 --threads 4 -b 0.0.0.0:$PORT t20_app:app
(ONE worker: one poller.  Do not run this and the IPL app.py at the same time on the SAME RapidAPI key - each keeps its own
 daily call counter, so together they can go over the key's daily limit.)

env: RAPID_KEY (or API_MODE=direct + API_TOKEN), T20_MODEL (path to inplay_t20_v2.pkl, default models/inplay_t20_v2.pkl),
     EARLY_ADD (default 20: added to the projection before EARLY_OVERS overs), EARLY_OVERS (default 5),
     ALLOWED_ORIGIN, DAILY_LIMIT (default 95), QUOTA_FILE, MODE=mock (+ MOCK_SPEED) for a fake match
"""
import os
import threading
import warnings
from datetime import datetime, timedelta

# the shared api_client reads these at import time: take every men's T20 (not only the IPL)
os.environ.setdefault("SERIES_FILTER", "")
os.environ.setdefault("MATCH_TYPE", "T20")
os.environ.setdefault("EXCLUDE_SERIES", "women")

from flask import Flask, jsonify

warnings.filterwarnings("ignore")
import poller as P
from poller import Poller
from quota import QuotaGuard, IST
from t20_engine import T20Engine
from t20_tracker import T20Tracker

P.NONE_STATE["next"] = "Live scores and predictions appear here when the next T20 match starts."

app = Flask(__name__)
engine = T20Engine()
quota = QuotaGuard(os.getenv("QUOTA_FILE", "quota.json"), int(os.getenv("DAILY_LIMIT", "95")))
tracker = T20Tracker(engine)

if os.getenv("MODE") == "mock":
    from mock_client import MockClient
    speed = float(os.getenv("MOCK_SPEED", "1"))
    t0 = datetime.now(IST)
    clock = lambda: t0 + (datetime.now(IST) - t0) * speed
    client = MockClient(quota, clock, t0 + timedelta(minutes=2 * speed + 25))
    poller = Poller(client, tracker, quota, clock=clock)
else:
    from api_client import ApiClient
    poller = Poller(ApiClient(quota), tracker, quota)

# ---- poller thread + watchdog: if the thread is missing or dead it is (re)started, on import and on every request
_poller_thread = None
_poller_lock = threading.Lock()


def poller_alive():
    return _poller_thread is not None and _poller_thread.is_alive()


def ensure_poller():
    global _poller_thread
    if poller_alive():
        return
    with _poller_lock:
        if not poller_alive():
            _poller_thread = threading.Thread(target=poller.run_forever, daemon=True, name="poller")
            _poller_thread.start()


ensure_poller()


@app.before_request
def _poller_guard():
    ensure_poller()


@app.after_request
def cors(r):
    r.headers["Access-Control-Allow-Origin"] = os.getenv("ALLOWED_ORIGIN", "*")
    return r


@app.get("/api/t20/live")
def live():
    r = jsonify(poller.get_state())
    r.headers["Cache-Control"] = "public, max-age=20"
    return r


@app.get("/health")
def health():
    h = poller.health()
    h.update({"model": os.path.basename(engine.model_path), "early_add": engine.early_add, "early_overs": engine.early_balls // 6,
              "bands_loaded": engine.bands is not None, "poller_alive": poller_alive()})
    return jsonify(h)


@app.get("/debug/upcoming")
def debug_upcoming():
    """Diagnostics (costs 1 API call per hit): what the server sees from upcomingMatches and how the filters treat it."""
    import api_client as A
    now = datetime.now(IST)
    out = {"now_ist": now.strftime("%Y-%m-%d %H:%M"), "filters": {"SERIES_FILTER": A.SERIES_FILTER, "MATCH_TYPE": A.MATCH_TYPE, "EXCLUDE_SERIES": A.EXCLUDE_SERIES},
           "poller_sched_day": poller.sched_day, "poller_sched_len": len(poller.sched), "poller_last_error": poller.last_error}
    try:
        ms = A.parse_upcoming(poller.c._call("upcoming"))
        day = now.strftime("%Y-%m-%d")
        today = [m for m in ms if m["start"].astimezone(IST).strftime("%Y-%m-%d") == day]
        out.update({"parsed_count": len(ms), "today_all": [(m["match_id"], m["team_a"], m["team_b"], m["series"], m["match_type"], m["start"].strftime("%H:%M")) for m in today],
                    "today_after_filter": [(m["team_a"], m["team_b"]) for m in today if A.SERIES_FILTER.lower() in m["series"].lower()
                                           and (not A.MATCH_TYPE or str(m["match_type"]).upper() == A.MATCH_TYPE.upper())
                                           and not any(x in m["series"].lower() for x in A.EXCLUDE_SERIES)]})
    except Exception as e:
        out["error"] = repr(e)
    return jsonify(out)


@app.get("/debug/tick")
def debug_tick():
    """Diagnostics (costs 1 API call): force the poller to re-read today's schedule right now and show the result / traceback."""
    import traceback
    err = None
    poller.sched_day = None
    poller.wake = poller.clock()
    try:
        poller._tick(poller.clock())
    except Exception:
        err = traceback.format_exc()
    return jsonify({"error": err, "sched_day": poller.sched_day, "sched": [(m["team_a"], m["team_b"], m["start"].strftime("%H:%M")) for m in poller.sched],
                    "wake": str(poller.wake), "cur": (poller.cur or {}).get("id"), "state_status": poller.get_state().get("status"),
                    "state_next": poller.get_state().get("next"), "last_error": poller.last_error, "quota_remaining": quota.remaining(),
                    "poller_alive": poller_alive(), "threads": [t.name for t in threading.enumerate()]})