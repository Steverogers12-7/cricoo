# ============================================================================
# cricoo_model1.py   (Model 1: innings-1 total + har player ka contribution)
#
# Is file ko apne  Desktop/cricoo/  folder mein rakho (notebook ke saath).
#
#   train_bundle(processed_dir)   -> bundle (dict) banata hai: LightGBM model + config + history
#   save_bundle(bundle, path)     -> .pkl mein save
#   Model1.load(path)             -> .pkl se load, aur predict_xi() ke liye ready
#
# .pkl mein sirf plain dict hai (model + DataFrames), koi custom class nahi.
# Isliye pickle kisi bhi notebook mein khulega, bas yeh file (cricoo_model1.py) aur
# lightgbm / pandas / numpy installed hon.
#
# Pickle sirf apni files ke liye kholo (kisi anjaan se mili .pkl kabhi load mat karo).
#
# Model ka matlab (sach):
#   * Team total   = B2 baseline (venue + era), kyunki walk-forward mein wahi sabse sahi nikla
#   * Har player   = LightGBM ke player-level expected runs, jinhe team total ke barabar scale kiya
#   * Range        = pichle 3 saal ke B2 residuals ke quantiles (x1.10)
# ============================================================================
import os
import pickle
import difflib
import numpy as np
import pandas as pd
import lightgbm as lgb

VERSION = "model1-v1"

CONFIG = dict(
    FEATURES=["p_rpb_b", "p1_bpa_b", "p_exp_pos", "bucket_code", "pr_rpb", "pr_bpa",
              "lg1_rpb", "lg1_bpa", "venue_offset", "is_home",
              "p_apps_before", "p_balls_before", "f3"],
    BUCKET_CODE={"unk": 0, "open": 1, "top": 2, "mid": 3, "low": 4, "tail": 5},
    PARAMS=dict(objective="tweedie", tweedie_variance_power=1.3,
                n_estimators=300, learning_rate=0.03, num_leaves=8, min_child_samples=100,
                subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                reg_lambda=5.0, random_state=42, verbose=-1),
    LEAGUE_DAYS=730, VENUE_DAYS=1095, K_V=5, K_BALLS=60, K_APPS=5,
    INTERVAL_DAYS=1095,
    INTERVAL_SCALE=1.10,   # 2019-24 pe coverage target tak laane ke liye chuna; 2025-26 holdout pe confirm
    QS=(0.10, 0.25, 0.75, 0.90),
)

H_COLS = ["match_id", "date", "season", "innings_no", "team", "venue", "player_id", "player_name",
          "actual_position", "runs", "balls", "bucket"]
TI_COLS = ["match_id", "date", "season", "innings_no", "team", "venue",
           "total_runs", "xi_runs", "legal_balls", "inn1_full"]
H_SORT = ["date", "match_id", "innings_no", "team", "actual_position"]

ND = lambda d: np.timedelta64(int(d), "D")


# ----------------------------------------------------------------------------
# TRAIN + SAVE
# ----------------------------------------------------------------------------
def train_bundle(processed_dir):
    """processed/ ki CSVs (features_v2.csv, team_innings.csv) se final model banata hai."""
    H = pd.read_csv(os.path.join(processed_dir, "features_v2.csv"), parse_dates=["date"])
    H = H.sort_values(H_SORT, na_position="last").reset_index(drop=True)
    ti = pd.read_csv(os.path.join(processed_dir, "team_innings.csv"), parse_dates=["date"])

    cfg = CONFIG
    train = H[H["usable_m1"] == 1].copy()
    train["bucket_code"] = train["bucket"].map(cfg["BUCKET_CODE"])
    model = lgb.LGBMRegressor(**cfg["PARAMS"]).fit(train[cfg["FEATURES"]], train["runs"])

    return {
        "version": VERSION,
        "trained_rows": int(len(train)),
        "last_data_date": str(H["date"].max().date()),
        "config": cfg,
        "lgbm": model,
        "H": H[H_COLS].copy(),
        "ti": ti[TI_COLS].copy(),
    }


def save_bundle(bundle, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(bundle, f, protocol=4)
    return os.path.getsize(path)


# ----------------------------------------------------------------------------
# MODEL
# ----------------------------------------------------------------------------
class Model1:
    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls(pickle.load(f))

    def __init__(self, bundle):
        self.bundle = bundle
        self.version = bundle["version"]
        self.cfg = c = bundle["config"]
        self.model = bundle["lgbm"]
        self.FEATURES = c["FEATURES"]
        self.BUCKET_CODE = c["BUCKET_CODE"]

        H = bundle["H"].sort_values(H_SORT, na_position="last").reset_index(drop=True)
        ti = bundle["ti"]
        self.H, self.ti = H, ti
        self.last_data = H["date"].max()

        # ---- naam / venue lookup ----
        pairs = H.groupby(["player_name", "player_id"]).size().rename("n").reset_index()
        pairs = pairs.sort_values("n", ascending=False).drop_duplicates("player_name")
        self.NAME2ID = dict(zip(pairs["player_name"], pairs["player_id"]))
        self.LOWER2NAME = {n.lower(): n for n in self.NAME2ID}
        self.ID2NAME = H.groupby("player_id")["player_name"].agg(lambda s: s.value_counts().index[0]).to_dict()
        self.KNOWN_IDS = set(self.ID2NAME)
        self.VENUES = sorted(ti["venue"].unique())
        self.TEAMS = sorted(set(ti["team"]))

        # ---- team-total history (B2) ----
        M = (ti[(ti["innings_no"] == 1) & (ti["inn1_full"] == 1)]
             .sort_values(["date", "match_id"]).reset_index(drop=True))
        self.m_dates = M["date"].values
        self.m_tot = M["total_runs"].values.astype(float)
        self.m_ven = M["venue"].values
        M["league_avg"] = [self._league_avg_at(d) for d in self.m_dates]
        self.m_resid = self.m_tot - M["league_avg"].values
        M["b2"] = [self.b2_at(d, v)[0] for d, v in zip(self.m_dates, self.m_ven)]
        M["b2_resid"] = M["total_runs"] - M["b2"]
        self.M = M
        self.b2r_d, self.b2r = M["date"].values, M["b2_resid"].values

        # ---- player-level lookup ----
        self.PL = {p: d for p, d in H.groupby("player_id", sort=False)}
        H1 = H[H["innings_no"] == 1]
        self.h1_d, self.h1_r = H1["date"].values, H1["runs"].values
        self.h1_b, self.h1_bk = H1["balls"].values, H1["bucket"].values
        self.ti_tm, self.ti_vn, self.ti_dt = ti["team"].values, ti["venue"].values, ti["date"].values
        self.ti_other = (ti["total_runs"] - ti["xi_runs"]).values
        self.ti_legal, self.ti_inn, self.ti_full = ti["legal_balls"].values, ti["innings_no"].values, ti["inn1_full"].values

    # ---------------- pehchaan ----------------
    def resolve_player(self, x):
        if x in self.KNOWN_IDS:
            return x, []
        if x in self.NAME2ID:
            return self.NAME2ID[x], []
        if x.lower() in self.LOWER2NAME:
            return self.NAME2ID[self.LOWER2NAME[x.lower()]], []
        hits = [n for n in self.NAME2ID if x.lower() in n.lower()]
        if len(hits) == 1:
            return self.NAME2ID[hits[0]], []
        return None, (hits[:5] if hits else difflib.get_close_matches(x, list(self.NAME2ID), n=5, cutoff=0.6))

    def resolve_venue(self, v):
        exact = [x for x in self.VENUES if x.lower() == v.lower()]
        if exact:
            return exact[0], []
        hits = [x for x in self.VENUES if v.lower() in x.lower()]
        if len(hits) == 1:
            return hits[0], []
        return None, hits or difflib.get_close_matches(v, self.VENUES, n=5, cutoff=0.4)

    def last_xi(self, team):
        """Us team ka sabse recent XI (naam)."""
        r = self.H[self.H["team"] == team]
        mid = r.loc[r["date"] == r["date"].max(), "match_id"].iloc[0]
        x = r[r["match_id"] == mid]
        x = x[x["innings_no"] == x["innings_no"].min()]
        return list(x["player_name"])

    # ---------------- team total (B2) + interval ----------------
    def _league_avg_at(self, as_of):
        w = (self.m_dates < as_of) & (self.m_dates >= as_of - ND(self.cfg["LEAGUE_DAYS"]))
        return self.m_tot[w].mean() if w.any() else np.nan

    def b2_at(self, as_of, venue):
        """return (b2, league_avg, venue ke pichle 3 saal ke matches)"""
        la = self._league_avg_at(as_of)
        wv = ((self.m_ven == venue) & (self.m_dates < as_of)
              & (self.m_dates >= as_of - ND(self.cfg["VENUE_DAYS"])) & ~np.isnan(self.m_resid))
        return la + self.m_resid[wv].sum() / (wv.sum() + self.cfg["K_V"]), la, int(wv.sum())

    def interval_at(self, as_of):
        c = self.cfg
        w = (self.b2r_d < as_of) & (self.b2r_d >= as_of - ND(c["INTERVAL_DAYS"])) & ~np.isnan(self.b2r)
        r = self.b2r[w] if w.sum() >= 50 else self.b2r[(self.b2r_d < as_of) & ~np.isnan(self.b2r)]
        return {q: c["INTERVAL_SCALE"] * float(np.quantile(r, q)) for q in c["QS"]}

    # ---------------- player features ----------------
    @staticmethod
    def _bucket_of(pos):
        if np.isnan(pos):
            return "unk"
        return "open" if pos <= 2.5 else "top" if pos <= 4.5 else "mid" if pos <= 6.5 else "low" if pos <= 8.5 else "tail"

    def _league_and_bucket_priors(self, as_of):
        win = (self.h1_d < as_of) & (self.h1_d >= as_of - ND(self.cfg["LEAGUE_DAYS"]))
        sb = self.h1_b[win].sum()
        lg_rpb = self.h1_r[win].sum() / sb if sb > 0 else 1.25
        lg_bpa = sb / win.sum() if win.sum() > 0 else 10.8
        pri = {}
        for b in self.BUCKET_CODE:
            wb = win & (self.h1_bk == b)
            bb = self.h1_b[wb].sum()
            pri[b] = (self.h1_r[wb].sum() / bb if bb > 0 else lg_rpb,
                      bb / wb.sum() if wb.sum() > 0 else lg_bpa)
        return lg_rpb, lg_bpa, pri

    def _venue_offset_at(self, as_of, venue):
        c = self.cfg
        wall = ((self.m_dates < as_of) & (self.m_dates >= as_of - ND(c["VENUE_DAYS"])) & ~np.isnan(self.m_resid))
        lg_resid = self.m_resid[wall].mean() if wall.any() else 0.0
        wv = wall & (self.m_ven == venue)
        return (self.m_resid[wv].sum() - wv.sum() * lg_resid) / (wv.sum() + c["K_V"])

    def _is_home_at(self, as_of, team, venue):
        w = (self.ti_tm == team) & (self.ti_dt < as_of) & (self.ti_dt >= as_of - ND(self.cfg["VENUE_DAYS"]))
        return int(w.sum() >= 5 and (w & (self.ti_vn == venue)).sum() / w.sum() >= 0.25)

    def build_features(self, pids, team, venue, as_of):
        """pids: player ids ki list (None = naya khiladi). return DataFrame (har player ek row)."""
        c = self.cfg
        lg_rpb, lg_bpa, pri = self._league_and_bucket_priors(as_of)
        vo, home = self._venue_offset_at(as_of, venue), self._is_home_at(as_of, team, venue)
        rows = []
        for p in pids:
            h = self.PL[p][self.PL[p]["date"] < as_of] if p in self.PL else self.H.iloc[0:0]
            h1 = h[h["innings_no"] == 1]
            pos = h["actual_position"].values[-10:]
            pexp = float(np.nanmean(pos)) if np.isfinite(pos).any() else np.nan
            b = self._bucket_of(pexp)
            pr_rpb, pr_bpa = pri[b]
            runs, balls = h["runs"].sum(), h["balls"].sum()
            rpb_b = (runs + c["K_BALLS"] * pr_rpb) / (balls + c["K_BALLS"])
            bpa_b = (h1["balls"].sum() + c["K_APPS"] * pr_bpa) / (len(h1) + c["K_APPS"])
            rows.append({"player_id": p, "p_rpb_b": rpb_b, "p1_bpa_b": bpa_b, "p_exp_pos": pexp,
                         "bucket": b, "bucket_code": self.BUCKET_CODE[b], "pr_rpb": pr_rpb, "pr_bpa": pr_bpa,
                         "lg1_rpb": lg_rpb, "lg1_bpa": lg_bpa, "venue_offset": vo, "is_home": home,
                         "p_apps_before": len(h), "p_balls_before": balls, "f3": bpa_b * rpb_b})
        return pd.DataFrame(rows)

    # ---------------- PREDICT ----------------
    def predict_xi(self, xi, team, venue, as_of=None, verbose=True):
        """
        xi    : 11 player ke naam (ya registry id)   e.g. ["V Kohli", "PD Salt", ...]
        team  : franchise code                       e.g. "RCB"
        venue : stadium ka naam / hissa              e.g. "Chinnaswamy"
        as_of : match ki date (default: aaj). Sirf isse PEHLE ka data use hota hai.
        """
        as_of = np.datetime64(pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.today().normalize())
        warns = []
        if team not in self.TEAMS:
            warns.append(f"team '{team}' data mein nahi hai (valid: {self.TEAMS}); home/away = 0 maana gaya")
        ven, sugg = self.resolve_venue(venue)
        if ven is None:
            warns.append(f"venue '{venue}' nahi mila {sugg} -> naya venue maana gaya (offset 0)")
            ven = venue
        if len(xi) != 11:
            warns.append(f"XI mein {len(xi)} naam hain (11 hone chahiye)")
        if len(xi) == 0:
            raise ValueError("XI khaali hai - 11 player ke naam do")
        if (as_of - np.datetime64(self.last_data)) > ND(400):
            warns.append("data bahut purana hai (400+ din) - naye matches jodke pipeline dobara chalao")

        pids, names = [], []
        for x in xi:
            p, sg = self.resolve_player(x)
            if p is None:
                warns.append(f"'{x}' nahi mila -> naya khiladi maana gaya. Shayad: {sg}")
            pids.append(p)
            names.append(self.ID2NAME.get(p, x))

        b2, la, nv = self.b2_at(as_of, ven)
        if nv < 5:
            warns.append(f"is venue pe pichle 3 saal mein sirf {nv} matches -> venue effect kam bharose ka")
        iv = self.interval_at(as_of)
        w = (self.ti_dt < as_of) & (self.ti_dt >= as_of - ND(self.cfg["LEAGUE_DAYS"]))
        other = float(self.ti_other[w].mean()) if w.any() else 0.0            # extras + impact sub
        wl = w & (self.ti_inn == 1) & (self.ti_full == 1)
        legal = float(self.ti_legal[wl].mean()) if wl.any() else 119.0

        F = self.build_features(pids, team, ven, as_of)
        ml = self.model.predict(F[self.FEATURES])
        contrib = ml / ml.sum() * (b2 - other)                                   # sum = team total - extras
        balls = F["p1_bpa_b"].values / F["p1_bpa_b"].sum() * legal

        players_df = pd.DataFrame({
            "player": names, "exp_position": F["p_exp_pos"].round(1), "bucket": F["bucket"],
            "exp_balls": balls.round(0), "exp_runs": contrib.round(1),
            "history_matches": F["p_apps_before"]})
        result = {
            "model_version": self.version, "team": team, "venue": ven,
            "as_of": str(pd.Timestamp(as_of).date()),
            "team_total": round(b2),
            "range50": (round(b2 + iv[0.25]), round(b2 + iv[0.75])),
            "range80": (round(b2 + iv[0.10]), round(b2 + iv[0.90])),
            "extras_and_impact": round(other), "league_avg": round(la, 1), "venue_matches": nv,
            "players": players_df, "warnings": warns}
        result["players_records"] = players_df.replace({np.nan: None}).to_dict("records")   # JSON ke liye (API mein kaam aayega)
        if verbose:
            print(f"{team} @ {ven} | as_of {result['as_of']}")
            print(f"Expected 1st-innings total : {result['team_total']}")
            print(f"   50% range : {result['range50'][0]} - {result['range50'][1]}")
            print(f"   80% range : {result['range80'][0]} - {result['range80'][1]}")
            print(f"   (league avg abhi {result['league_avg']}, venue ke {nv} recent matches, extras+impact ~{result['extras_and_impact']})\n")
            print(players_df.to_string(index=False))
            print(f"\nsum of player runs = {contrib.sum():.0f}  (+ extras/impact {other:.0f} = team total)")
            for w_ in warns:
                print("WARNING:", w_)
        return result
