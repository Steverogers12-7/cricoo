"""Parity test: engine.py (over-level) == notebook serving functions (ball-level feed). Run: python test_engine.py"""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from engine import Engine, _logit

E = Engine("models")

# ---- the two functions copied verbatim from the notebooks ----
def predict_from_feed(bundle, feed, b2):
    cum_r = cum_w = legal = 0
    cum_at_legal = {0: (0, 0)}
    for runs, wk, is_legal in feed:
        cum_r += runs; cum_w += wk; legal += int(is_legal)
        cum_at_legal[legal] = (cum_r, cum_w)
    r30, w30 = cum_at_legal.get(legal - 30, (0, 0)) if legal > 30 else (0, 0)
    x = pd.DataFrame([{"balls_bowled": legal, "balls_left": 120 - legal, "cum_runs": cum_r, "cum_wkts": cum_w,
        "wkts_in_hand": 10 - cum_w, "crr": cum_r / legal * 6, "runs_l30": cum_r - r30, "wkts_l30": cum_w - w30, "b2": b2}])[bundle["features"]]
    model_pred = cum_r + max(float(bundle["lgbm"].predict(x)[0]), 0.0)
    w = float(np.clip((legal - bundle["blend_start"]) / bundle["blend_len"], 0, 1))
    point = (1 - w) * b2 + w * model_pred
    q = bundle["quantiles"][min(max((legal - 1) // 6, 0), 19)]
    return point, point + q[0.25], point + q[0.75], point + q[0.10], point + q[0.90]

def predict_chase_from_feed(bundle, feed, target, b2):
    cum_r = cum_w = legal = 0
    at_legal = {0: (0, 0)}
    for runs, wk, is_legal in feed:
        cum_r += runs; cum_w += wk; legal += int(is_legal)
        at_legal[legal] = (cum_r, cum_w)
    needed, left, in_hand = target - cum_r, 120 - legal, 10 - cum_w
    if needed <= 0: return 1.0
    if in_hand <= 0 or left <= 0: return 0.0
    r30, w30 = at_legal.get(legal - 30, (0, 0)) if legal > 30 else (0, 0)
    x = pd.DataFrame([{"runs_needed": needed, "balls_left": left, "wkts_in_hand": in_hand,
        "crr": cum_r / legal * 6 if legal else 0.0, "rrr": needed / left * 6, "target": target,
        "runs_l30": cum_r - r30, "wkts_l30": cum_w - w30, "target_vs_par": target - b2,
        "rpb_needed": needed / left, "wkts_x_balls": in_hand * left / 120}])
    p_l = bundle["logit"].predict_proba(x[bundle["logit_features"]])[:, 1][0]
    p_g = bundle["gbm"].predict_proba(x[bundle["gbm_features"]])[:, 1][0]
    return float(bundle["platt"].predict_proba(_logit(np.array([(p_l + p_g) / 2])).reshape(-1, 1))[:, 1][0])

# ---- a made-up match: per-over (runs, wkts) ----
runs1 = [6, 9, 7, 12, 8, 10, 5, 11, 9, 8, 7, 9, 8, 9, 10, 9, 10, 11, 10, 13]
wk1   = [0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0]
def ball_feed(runs, wks):          # spread an over's runs/wickets over 6 legal balls
    feed = []
    for r, w in zip(runs, wks):
        for i in range(6):
            feed.append((r // 6 + (1 if i < r % 6 else 0), 1 if i < w else 0, 1))
    return feed
def entries(runs, wks):
    out, cr, cw = [], 0, 0
    for k, (r, w) in enumerate(zip(runs, wks), 1):
        cr += r; cw += w; out.append((6 * k, cr, cw))
    return out

b2 = E.b2("2026-04-10", E.venue("Chinnaswamy"))
print("B2 @ Chinnaswamy:", round(b2, 1))
ent = entries(runs1, wk1)
ser = E.inn1_series(ent, b2)
d1 = 0
for k in range(1, 21):
    ref = predict_from_feed(E.inplay, ball_feed(runs1[:k], wk1[:k]), b2)
    mine = (ser[k-1]["point"], *ser[k-1]["range50"], *ser[k-1]["range80"])
    d1 = max(d1, max(abs(a - b) for a, b in zip(ref, mine)))
print("innings-1 max abs diff:", d1)

runs2 = [8, 7, 11, 6, 9, 12, 8, 10, 7, 9, 9, 8, 11, 7, 9, 10, 8, 12, 9, 8]
wk2   = [0, 1, 0, 1, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1]
target = sum(runs1) + 1
ent2 = entries(runs2, wk2)
win = E.chase_series(ent2, target, b2)
d2 = 0
for k in range(1, 21):
    ref = predict_chase_from_feed(E.m2, ball_feed(runs2[:k], wk2[:k]), target, b2)
    d2 = max(d2, abs(ref - win[k-1]))
print("chase max abs diff    :", d2)
assert d1 < 1e-9 and d2 < 1e-9, "engine != notebook serving"
print("OK: engine matches notebook serving code")
print("win% by over:", [round(100 * w) for w in win])
print("inn1 projection by over:", [round(s['point']) for s in ser])
