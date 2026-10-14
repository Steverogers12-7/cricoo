"""cricoo engine: loads the 3 bundles and exposes the prediction functions used by the live tracker.

Everything here is a re-statement of the serving code in the notebooks (predict_from_feed /
predict_chase_from_feed / Model1.predict_xi), but works on OVER-LEVEL data:
    entries = [(legal_balls, cum_runs, cum_wkts), ...]   one entry per completed over
              (+ one last entry if the innings ended mid-over)
test_engine.py proves it gives the same numbers as the notebook functions.
"""
import os
import pickle
import re
import numpy as np
import pandas as pd
from cricoo_model1 import Model1


def _logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


_GENERIC = {"stadium", "cricket", "ground", "international", "the", "club", "association", "sports", "complex"}
VENUE_ALIASES = {"himachal pradesh cricket association": "HPCA Stadium, Dharamsala"}   # API name fragment -> training name


def _vtok(s):
    return set(re.sub(r"[^a-z0-9 ]", " ", str(s).lower()).split()) - _GENERIC


def _at(at, key):
    """(cum_runs, cum_wkts) after `key` legal balls. Exact when key is a known entry, else latest entry before it."""
    if key <= 0:
        return (0, 0)
    if key in at:
        return at[key]
    ks = [k for k in at if k <= key]
    return at[max(ks)] if ks else (0, 0)


class Engine:
    def __init__(self, model_dir="models"):
        p = lambda f: os.path.join(model_dir, f)
        self.m1 = Model1.load(p("model1_bundle.pkl"))
        with open(p("inplay_bundle.pkl"), "rb") as f:
            self.inplay = pickle.load(f)
        with open(p("model2_bundle.pkl"), "rb") as f:
            self.m2 = pickle.load(f)

    # ---------- venue / B2 ----------
    def venue(self, name):
        """API venue string -> venue string used in training (unknown venue is kept as is = offset 0)."""
        if not name:
            return name
        for frag, tv in VENUE_ALIASES.items():
            if frag in str(name).lower() and tv in self.m1.VENUES:
                return tv
        v, _ = self.m1.resolve_venue(name)
        if v:
            return v
        a, best = _vtok(name), (0.0, 0.0, None)
        for tv in self.m1.VENUES:                            # word overlap, e.g. "M.Chinnaswamy Stadium, Bengaluru"
            b = _vtok(tv)
            if not a or not b:
                continue
            inter = len(a & b)
            cand = (inter / min(len(a), len(b)), inter / len(a | b), tv)
            if cand[:2] > best[:2]:
                best = cand
        return best[2] if best[0] >= 0.75 else name

    def b2(self, as_of, venue):
        return float(self.m1.b2_at(np.datetime64(pd.Timestamp(as_of)), venue)[0])

    # ---------- Model 1 (pre-match) ----------
    def pre_match(self, xi, team, venue, as_of):
        r = self.m1.predict_xi(list(xi), team, venue, as_of=pd.Timestamp(as_of), verbose=False)
        return {"total": int(r["team_total"]), "low": int(r["range80"][0]), "high": int(r["range80"][1]),
                "b2": self.b2(as_of, r["venue"]), "warnings": r["warnings"]}

    # ---------- in-play (innings 1) ----------
    def inn1_series(self, entries, b2):
        ip = self.inplay
        at = {0: (0, 0)}
        for legal, r, w in entries:
            at[legal] = (r, w)
        rows = []
        for legal, r, w in entries:
            r30, w30 = _at(at, legal - 30)
            rows.append({"balls_bowled": legal, "balls_left": 120 - legal, "cum_runs": r, "cum_wkts": w,
                         "wkts_in_hand": 10 - w, "crr": r / legal * 6, "runs_l30": r - r30,
                         "wkts_l30": w - w30, "b2": b2})
        if not rows:
            return []
        pred = np.maximum(ip["lgbm"].predict(pd.DataFrame(rows)[ip["features"]]), 0.0)
        out = []
        for (legal, r, w), m in zip(entries, pred):
            wgt = float(np.clip((legal - ip["blend_start"]) / ip["blend_len"], 0, 1))
            point = (1 - wgt) * b2 + wgt * (r + float(m))
            q = ip["quantiles"][min(max((legal - 1) // 6, 0), 19)]
            out.append({"point": point,
                        "range50": (point + q[0.25], point + q[0.75]),
                        "range80": (point + q[0.10], point + q[0.90])})
        return out

    # ---------- Model 2 (chase win probability) ----------
    def chase_series(self, entries, target, b2):
        m2 = self.m2
        at = {0: (0, 0)}
        for legal, r, w in entries:
            at[legal] = (r, w)
        res, rows, idx = [], [], []
        for legal, r, w in entries:
            needed, left, hand = target - r, 120 - legal, 10 - w
            if needed <= 0:
                res.append(1.0); continue
            if hand <= 0 or left <= 0:
                res.append(0.0); continue
            r30, w30 = _at(at, legal - 30)
            rows.append({"runs_needed": needed, "balls_left": left, "wkts_in_hand": hand,
                         "crr": r / legal * 6, "rrr": needed / left * 6, "target": target,
                         "runs_l30": r - r30, "wkts_l30": w - w30, "target_vs_par": target - b2,
                         "rpb_needed": needed / left, "wkts_x_balls": hand * left / 120})
            idx.append(len(res)); res.append(None)
        if rows:
            X = pd.DataFrame(rows)
            p_l = m2["logit"].predict_proba(X[m2["logit_features"]])[:, 1]
            p_g = m2["gbm"].predict_proba(X[m2["gbm_features"]])[:, 1]
            p = m2["platt"].predict_proba(_logit((p_l + p_g) / 2).reshape(-1, 1))[:, 1]
            for i, v in zip(idx, p):
                res[i] = float(v)
        return res
