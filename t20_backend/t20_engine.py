"""T20 in-play engine (INNINGS 1 ONLY): loads inplay_t20_v2.pkl and predicts the final score from over-level data.

    entries = [(legal_balls, cum_runs, cum_wkts), ...]   one entry per completed over (same shape as the IPL engine)

Rules implemented here
  * model target is the REMAINING runs, so  prediction = model output (clipped at 0) + current_score
  * before EARLY_OVERS overs (default 5, i.e. legal balls < 30) the shown projection gets + EARLY_ADD (default 20)
    - the raw (un-added) prediction is kept in the result as "raw"
  * range80 comes from t20_bands.json (empirical 10/90 percentiles of the model's own errors, per over, on the
    2025-26 test set); it is built around the RAW prediction, so it is not shifted by EARLY_ADD
"""
import json
import os
import pickle
import re
import difflib

import numpy as np
import pandas as pd

from venue_utils import VenueResolver

HERE = os.path.dirname(os.path.abspath(__file__))
EARLY_ADD = float(os.getenv("EARLY_ADD", "20"))
EARLY_OVERS = int(os.getenv("EARLY_OVERS", "5"))

TEAM_ALIASES = {"usa": "United States of America", "u.s.a.": "United States of America", "united states": "United States of America",
                "uae": "United Arab Emirates", "png": "Papua New Guinea"}
_GENERIC = {"stadium", "cricket", "ground", "international", "the", "club", "association", "sports", "complex"}


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


class T20Engine:
    def __init__(self, model_path=None, venue_map=None, bands_path=None, early_add=None, early_overs=None):
        model_path = model_path or os.getenv("T20_MODEL", os.path.join(HERE, "models", "inplay_t20_v2.pkl"))
        with open(model_path, "rb") as f:                      # only load your own pickle files
            b = pickle.load(f)
        assert b.get("target") == "residual", "bundle must predict the remaining runs (target='residual')"
        self.bundle, self.model, self.F = b, b["model"], list(b["features"])
        self.cat_cols, self.cats = list(b["cat_cols"]), b["categories"]
        self.keep_venue, self.keep_bat, self.keep_bowl = set(b["keep_venue"]), set(b["keep_bat"]), set(b["keep_bowl"])
        self.early_add = float(EARLY_ADD if early_add is None else early_add)
        self.early_balls = 6 * int(EARLY_OVERS if early_overs is None else early_overs)

        self.vr = VenueResolver(venue_map or os.path.join(HERE, "venue_mapping.json"))
        self.canon = list(self.vr.city)
        bp = bands_path or os.getenv("T20_BANDS", os.path.join(HERE, "t20_bands.json"))
        self.bands = json.load(open(bp)) if os.path.exists(bp) else None     # {"1": [q10, q25, q75, q90], ... "20": [...]}
        self.model_path = model_path

    # ------------------------------------------------------------------ names
    def _resolve_venue(self, name):
        name = str(name or "").strip()
        if not name:
            return None
        parts = [p.strip() for p in name.split(",") if p.strip()]
        tries = [(name, None)]
        if len(parts) >= 2:
            tries += [(", ".join(parts[:-1]), parts[-1]), (parts[0], parts[-1]), (parts[0], None)]
        for v, c in tries:
            can, _, _ = self.vr.resolve(v, city=c)
            if can:
                return can
        a, best = _vtok(name), (0.0, 0.0, None)                # word-overlap fallback (API spelling differs from cricsheet)
        for tv in self.canon:
            t = _vtok(tv)
            if not a or not t:
                continue
            inter = len(a & t)
            cand = (inter / min(len(a), len(t)), inter / len(a | t), tv)
            if cand[:2] > best[:2]:
                best = cand
        return best[2] if best[0] >= 0.75 else None

    def venue(self, name):
        """-> (value fed to the model, canonical name or None).  Not in the training keep-list => 'Other' (same as training)."""
        can = self._resolve_venue(name)
        return (can if can in self.keep_venue else "Other"), can

    def team(self, name, kept):
        """API team name -> (value fed to the model, resolved: bool).  Unknown => 'Other' (same as training)."""
        n = re.sub(r"\s+men$", "", str(name or "").strip(), flags=re.I)
        low = {k.lower(): k for k in kept}
        if n.lower() in low:
            return low[n.lower()], True
        if n.lower() in TEAM_ALIASES and TEAM_ALIASES[n.lower()] in kept:
            return TEAM_ALIASES[n.lower()], True
        close = difflib.get_close_matches(n.lower(), list(low), n=1, cutoff=0.9)
        if close:
            return low[close[0]], True
        return "Other", False

    # ------------------------------------------------------------------ prediction
    def _frame(self, entries, bat, bowl, ven):
        at = {0: (0, 0)}
        for legal, r, w in entries:
            at[legal] = (r, w)
        rows = []
        for legal, r, w in entries:
            r30, w30 = _at(at, legal - 30)                     # training used shift(30).fillna(0): before ball 31 the lag is 0
            rows.append({"ball_no": legal, "wicket": w, "current_score": r, "runs_last_30": r - r30, "wkts_last_30": w - w30,
                         "balls_remaining": 120 - legal, "crr": r / legal * 6})
        X = pd.DataFrame(rows)
        n = len(X)
        for c, v in (("batting_team", bat), ("bowling_team", bowl), ("venue", ven)):
            X[c] = pd.Categorical([v] * n, categories=self.cats[c])
        return X[self.F]

    def _band(self, legal):
        if not self.bands:
            return None
        o = int(min(max((legal - 1) // 6 + 1, 1), 20))
        return self.bands[str(o)]

    def inn1_series(self, entries, bat, bowl, ven):
        """one dict per entry: raw, point (= raw + EARLY_ADD while legal < early_balls), early (bool), low80, high80"""
        if not entries:
            return []
        X = self._frame(entries, bat, bowl, ven)
        raw = np.maximum(self.model.predict(X), 0.0) + X["current_score"].values
        out = []
        for (legal, r, w), p in zip(entries, raw):
            early = legal < self.early_balls
            q = self._band(legal)
            out.append({"raw": float(p), "early": early, "point": float(p) + (self.early_add if early else 0.0),
                        "low80": float(p) + q[0] if q else None, "high80": float(p) + q[3] if q else None})
        return out

    def baseline(self, bat, bowl, ven):
        """pre-match number = the model at ball 1 with nothing on the board (this is an 'early' prediction too)"""
        s = self.inn1_series([(1, 0, 0)], bat, bowl, ven)[0]
        q = self._band(6)                                      # first-over band is the widest one we have
        low = s["raw"] + q[0] if q else s["point"] - 30
        high = s["raw"] + q[3] if q else s["point"] + 30
        return {"total": int(round(s["point"])), "raw": s["raw"], "low": int(round(low)), "high": int(round(max(high, s["point"])))}
