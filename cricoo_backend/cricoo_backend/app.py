"""Flask entry point.   gunicorn -w 1 --threads 4 -b 0.0.0.0:$PORT app:app      (ONE worker: one poller)
env: API_MODE, API_BASE, API_TOKEN, ALLOWED_ORIGIN (your site, e.g. https://cricoo.com), DAILY_LIMIT (default 95), MODE=mock for demo"""
import os
import threading
import warnings
from datetime import datetime, timedelta

from flask import Flask, jsonify, request

warnings.filterwarnings("ignore")
from engine import Engine
from name_map import NameMapper
from tracker import Tracker
from quota import QuotaGuard, IST
from poller import Poller

app = Flask(__name__)
engine = Engine(os.path.join(os.path.dirname(__file__), "models"))
quota = QuotaGuard(os.getenv("QUOTA_FILE", "quota.json"), int(os.getenv("DAILY_LIMIT", "95")))
tracker = Tracker(engine, NameMapper(engine.m1))

if os.getenv("MODE") == "mock":
    from mock_client import MockClient
    # fake match that "starts" 2 minutes from now; simulated over = 200 s (set MOCK_SPEED=20 to run 20x faster)
    speed = float(os.getenv("MOCK_SPEED", "1"))
    t0 = datetime.now(IST)
    clock = lambda: t0 + (datetime.now(IST) - t0) * speed
    client = MockClient(quota, clock, t0 + timedelta(minutes=2 * speed + 25))
    poller = Poller(client, tracker, quota, clock=clock)
else:
    from api_client import ApiClient
    poller = Poller(ApiClient(quota), tracker, quota)

threading.Thread(target=poller.run_forever, daemon=True).start()


@app.after_request
def cors(r):
    r.headers["Access-Control-Allow-Origin"] = os.getenv("ALLOWED_ORIGIN", "*")
    return r


@app.get("/api/ipl/live")
def live():
    r = jsonify(poller.get_state())
    r.headers["Cache-Control"] = "public, max-age=20"
    return r


@app.get("/health")
def health():
    return jsonify(poller.health())
