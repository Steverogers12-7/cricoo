T20 innings-1 live predictor (same architecture as the cricoo IPL backend)

FOLDER:  t20_backend/  (app + model + deps all inside, nothing else needed)
  public_assets_js/t20-live.js  -> copy to  public/assets/js/t20-live.js  (replace old one), then set API_BASE in it

LOCAL TEST (Windows, in this folder):
  pip install -r requirements.txt
  set T20_MODEL=models\inplay_t20_v2.pkl
  python test_t20.py                      (should end with: ALL T20 TESTS PASSED)
  set MODE=mock & set MOCK_SPEED=20 & python -m flask --app t20_app run --port 5000
  open http://127.0.0.1:5000/api/t20/live   and   /health

LIVE:
  set RAPID_KEY=<your rapidapi key>
  deploy: gunicorn -w 1 --threads 4 -b 0.0.0.0:$PORT t20_app:app   (Procfile included)
  env (optional): EARLY_ADD (default 20), EARLY_OVERS (5), DAILY_LIMIT (95), ALLOWED_ORIGIN, T20_MODEL, T20_BANDS
  API endpoint: /api/t20/live      health: /health
  Run it as a SEPARATE service from the IPL app (separate quota counters, same RapidAPI key).

AUTOMATION (no manual step on match day)
  The backend poller finds the next men's T20 from the API by itself, starts ~28 min before the match, waits for XI + toss,
  then updates every over; the page polls /api/t20/live every 60s and switches pre -> inn1 -> break -> inn2 -> done by itself.
  1) deploy: render.yaml (Render Blueprint) or any host using the Procfile; set RAPID_KEY only.
  2) page: in t_20.html BEFORE the t20-live.js script add:   <meta name="t20-api" content="https://YOUR-SERVICE.onrender.com">
     (or <script>window.T20_API_BASE='https://...'</script>).  Empty = demo mode.
  Free Render sleeps when idle: ping /health with a free uptime monitor (UptimeRobot, every 5 min) so the poller keeps running.
  DAILY_LIMIT is 45 here so IPL (95) + T20 on the same RapidAPI key do not exceed the key's daily cap; adjust to your plan.
