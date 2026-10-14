"""Builds t20_bands.json (per-over error percentiles) from YOUR inplay_t20_v2.pkl and the test CSV, and prints the early-over bias.
    python t20_make_bands.py models/inplay_t20_v2.pkl t20_test_2025_2026.csv
bands[o] = [q10, q25, q75, q90] of (actual - raw prediction) at the end of over o.  The API uses raw + q10 / raw + q90 as its 80% range.
The bias table tells you what EARLY_ADD to use (the best constant is about the mean error of the early overs)."""
import json, pickle, sys
import numpy as np, pandas as pd

pkl, csv = sys.argv[1], sys.argv[2]
b = pickle.load(open(pkl, "rb"))
te = pd.read_csv(csv, parse_dates=["date"]).sort_values(["date", "match_id", "ball_no"]).reset_index(drop=True)
for c, k in (("venue", "keep_venue"), ("batting_team", "keep_bat"), ("bowling_team", "keep_bowl")):
    te[c] = te[c].where(te[c].isin(set(b[k])), "Other")
g = te.groupby("match_id")
te["runs_last_30"] = te["current_score"] - g["current_score"].shift(30).fillna(0)
te["wkts_last_30"] = te["wicket"] - g["wicket"].shift(30).fillna(0)
te["balls_remaining"] = 120 - te["ball_no"]
te["crr"] = te["current_score"] / te["ball_no"] * 6
for c in b["cat_cols"]:
    te[c] = pd.Categorical(te[c], categories=b["categories"][c])
pred = np.maximum(b["model"].predict(te[b["features"]]), 0) + te["current_score"].values
err = te["run_x"].values - pred
bands = {}
for o in range(1, 21):
    r = err[(te["ball_no"] == 6 * o).values]
    bands[str(o)] = [float(np.quantile(r, q)) for q in (0.10, 0.25, 0.75, 0.90)]
json.dump(bands, open("t20_bands.json", "w"), indent=1)
print("wrote t20_bands.json")
ov = (te["ball_no"] % 6 == 0) & (te["ball_no"] < 30)
print("early overs (6,12,18,24): mean(actual - pred) =", round(err[ov].mean(), 2), "| MAE", round(np.abs(err[ov]).mean(), 2))
for a in (0, 5, 8, 10, 12, 15, 20):
    print(f"   EARLY_ADD={a:<2} -> MAE {np.abs(err[ov] - a).mean():.2f}")
