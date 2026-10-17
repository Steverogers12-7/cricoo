"""Cricket Live Line API client (Latiyal Infotech).

Interface used by the poller (everything else in the backend is API-agnostic):
    upcoming()          -> [{"match_id", "start": aware datetime, "team_a": code, "team_b": code}]
    prepare(match_id)   -> None while toss/XI not out, else
                           {"venue","date","toss","batting_first": code,
                            "team_a": {"code","name"}, "team_b": {...}, "xi_a": [names], "xi_b": [names]}
                           (team_a = the side that bats first)
    result_text(id)     -> official result string from matchInfo ("" while live)
    progress(match_id)  -> {"inn1": {"started","finished","entries","balls"}, "inn2": {...}, "done": bool, "result": str}
                           entries = [(legal_balls, cum_runs, cum_wkts), ...] one per completed over

Default mode is RapidAPI (host cricket-live-line1.p.rapidapi.com); set env RAPID_KEY.
"""
import os
import re
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------- config (env vars)
API_MODE = os.getenv("API_MODE", "rapidapi")          # "rapidapi" (your setup) | "direct" (token in URL)
API_BASE = os.getenv("API_BASE", "https://apicricketchampion.in/apiv5")   # prefix before /<endpoint>/<token>  (check in Postman)
API_TOKEN = os.getenv("API_TOKEN", "")
RAPID_HOST = os.getenv("RAPID_HOST", "cricket-live-line1.p.rapidapi.com")
RAPID_KEY = os.getenv("RAPID_KEY", "")
SERIES_FILTER = os.getenv("SERIES_FILTER", "Indian Premier League")   # only matches whose series name contains this
MATCH_TYPE = os.getenv("MATCH_TYPE", "")                              # e.g. "T20": keep only that match_type ("" = any)
EXCLUDE_SERIES = [x.strip().lower() for x in os.getenv("EXCLUDE_SERIES", "").split(",") if x.strip()]   # e.g. "women"

# logical name -> (HTTP method, path).  Paths are from the official Postman collection (direct mode).
DIRECT = {
    "upcoming": ("GET", "upcomingMatches"),
    "live_list": ("GET", "liveMatchList"),
    "info": ("POST", "matchInfo"),
    "playing11": ("POST", "playingXiByMatchId"),
    "scorecard": ("POST", "scorecardByMatchId"),
    "over_history": ("POST", "matchOverHistory"),
}
RAPID = {   # from the RapidAPI "Code snippet" tabs ({id} = match id)
    "upcoming": ("GET", "/upcomingMatches"),
    "live_list": ("GET", "/liveMatches"),            # used by upcoming() to pick up matches that already started
    "info": ("GET", "/match/{id}"),
    "playing11": ("GET", "/match/{id}/playingXI"),
    "scorecard": ("GET", "/match/{id}/scorecard"),
    "over_history": ("GET", "/match/{id}/overHistory"),
}


class QuotaExceeded(Exception):
    pass


class NoData(Exception):
    """API answered status:false 'No Data Found' (e.g. over history before the first over, XI not announced yet)."""


# ---------------------------------------------------------------- helpers
def over_to_legal(s):
    """'16.3' = 16 overs 3 balls = 99 legal balls (NOT a decimal).  '20' / '20.0' = 120."""
    a, _, b = str(s).partition(".")
    return int(a or 0) * 6 + int(b or 0)


def clean_fow(fow):
    """fall of wickets sanity: scores must not go down (the sample has wicket 5 = 16 instead of ~160)."""
    out, last = [], -1
    for f in fow:
        sc = int(f["score"])
        if sc < last:
            continue
        out.append({"score": sc, "wicket": int(f["wicket"]), "legal": over_to_legal(f["over"]),
                    "player": re.sub(r"\s+IMP$", "", str(f["player"]))})
        last = sc
    return out


# ---------------------------------------------------------------- parsers
def parse_playing11(js):
    d = js["data"]
    return {k: {"name": d[k]["name"], "code": d[k]["short_name"],
                "players": [{"name": p["name"], "role": p.get("play_role", ""), "id": p["player_id"]} for p in d[k]["player"]]}
            for k in ("team_a", "team_b")}


def parse_scorecard(js):
    """final / current scorecard -> {1: {...}, 2: {...}, 'result': str}"""
    d, out = js["data"], {}
    for no, inn in d["scorecard"].items():
        t = inn["team"]
        out[int(no)] = {"code": t["short_name"], "runs": int(t["score"]), "wkts": int(t["wicket"]),
                        "legal": over_to_legal(t["over"]), "fow": clean_fow(inn.get("fallwicket", []))}
    out["result"] = d.get("result", "")
    return out


def parse_upcoming(js):
    """upcomingMatches -> list of dicts. match_time is IST."""
    out = []
    for m in js["data"]:
        try:
            day = m["date_wise"].split(",")[0].strip()                 # "13 Oct 2026, Tuesday" -> "13 Oct 2026"
            start = datetime.strptime(f'{day} {m["match_time"]}', "%d %b %Y %I:%M %p").replace(tzinfo=IST)
        except Exception:
            continue
        out.append({"match_id": str(m["match_id"]), "start": start, "team_a": m["team_a_short"], "team_b": m["team_b_short"],
                    "series": m.get("series", ""), "venue": m.get("venue", ""), "match_type": m.get("match_type", "")})
    return out


def parse_live(js):
    """liveMatches -> same shape as parse_upcoming. Live items have match_date '14-Oct' (no year, no date_wise)."""
    out, now = [], datetime.now(IST)
    for m in js["data"]:
        try:
            start = datetime.strptime(f'{m["match_date"]}-{now.year} {m["match_time"]}',
                                      "%d-%b-%Y %I:%M %p").replace(tzinfo=IST)
        except Exception:
            continue
        out.append({"match_id": str(m["match_id"]), "start": start, "team_a": m["team_a_short"], "team_b": m["team_b_short"],
                    "series": m.get("series", ""), "venue": m.get("venue", ""), "match_type": m.get("match_type", "")})
    return out


def _tok(s):
    return set(re.sub(r"[^a-z ]", " ", str(s).lower()).split())


def batting_first_from_toss(toss, a, b):
    """'Royal Challengers Bangalore have won the toss and have opted to bat' -> code of the team that bats first.
    Teams are matched by word overlap.  a, b = {"name","code"}.  Returns None if toss text is empty / not understood."""
    t = str(toss or "").lower().strip()
    m = re.search(r"^(.*?)\s+(?:have\s+|has\s+)?won the toss", t)
    d = re.search(r"(?:opted|chose|elected|decided|choose|elect|opt)\s+to\s+(bat|bowl|field)", t)
    if not (m and d):
        return None
    w = _tok(m.group(1))
    sc = {}
    for team in (a, b):
        tt = _tok(team["name"])
        sc[team["code"]] = len(w & tt) / max(1, len(tt))
    win = max(sc, key=sc.get)
    if sc[win] == 0 or (len(set(sc.values())) == 1):
        return None
    other = b["code"] if win == a["code"] else a["code"]
    return win if d.group(1) == "bat" else other


def parse_info(js):
    d = js["data"]
    a = {"code": d["team_a_short"], "name": d["team_a"]}
    b = {"code": d["team_b_short"], "name": d["team_b"]}
    return {"venue": d.get("venue", ""), "toss": d.get("toss", ""), "team_a": a, "team_b": b,
            "batting_first": batting_first_from_toss(d.get("toss", ""), a, b),
            "series": d.get("series", ""), "result": d.get("result", ""), "match_status": d.get("match_status")}


# ---------------------------------------------------------------- over history
# data = {"1": [over, over, ...newest first], "2": [...], ...}   (a T20 has "1" and "2")
# over = {"team": {"score": "390-10" (cumulative AFTER this over), "team": "AUS", "bowler", "over": 138, "runs": 0, "wkts": 1},
#         "overs": ["W", "0"]}     <- ball tokens of that over. Wides / no-balls are NOT legal deliveries.
_NOT_LEGAL = re.compile(r"(WB|WD|NB)$", re.I)


def _legal(tokens):
    return sum(1 for t in tokens if not _NOT_LEGAL.search(str(t)))


def _ball_token(t):
    """API token -> token understood by t20-live.js ("Wd", "Wd1", "Nb", "Nb4", "Lb1", "B4", "W", "0".."6")."""
    t = str(t).strip()
    m = re.fullmatch(r"(\d*)(?:WB|WD)", t, re.I)
    if m:
        return "Wd" + (m.group(1) if m.group(1) not in ("", "0") else "")
    m = re.fullmatch(r"(\d*)NB", t, re.I)
    if m:
        return "Nb" + (m.group(1) if m.group(1) not in ("", "0") else "")
    m = re.fullmatch(r"(\d+)LB", t, re.I)
    if m:
        return "Lb" + m.group(1)
    m = re.fullmatch(r"(\d+)BYES?", t, re.I)
    if m:
        return "B" + m.group(1)
    return "W" if t.upper() == "W" else t


def _innings(items, force_finished=False):
    """items = one innings of the API (newest over first) -> Innings kwargs (+ 'last_score')."""
    if not items:
        return {"started": False, "finished": False, "entries": [], "balls": None, "last_score": None}
    by = {}
    for it in items:
        t = it["team"]
        r, w = (int(x) for x in str(t["score"]).split("-"))
        by[int(t["over"])] = (r, w, it.get("overs") or [])
    by = {o: v for o, v in by.items() if 1 <= o <= 20}          # T20 only (a super over would be key "3")
    if not by:
        return {"started": False, "finished": False, "entries": [], "balls": None, "last_score": None}
    last = max(by)
    lr, lw, ltoks = by[last]
    nlegal = _legal(ltoks)
    complete = nlegal >= 6                                       # the newest over may still be in progress
    finished = force_finished or lw >= 10 or (last >= 20 and complete)
    entries, balls = [], []
    for o in sorted(by):
        r, w, tk = by[o]
        if o == last and not complete:
            if finished:                                         # all out / target chased in the middle of an over
                entries.append((6 * (o - 1) + max(1, nlegal), r, w))
                balls.append([_ball_token(x) for x in tk])
            continue                                             # otherwise: over in progress, not shown yet
        entries.append((6 * o, r, w))
        balls.append([_ball_token(x) for x in tk])
    return {"started": True, "finished": finished, "entries": entries, "balls": balls, "last_score": (lr, lw)}


def parse_over_history(js):
    d = js["data"]
    i2_items = d.get("2") or []
    i1 = _innings(d.get("1") or [], force_finished=bool(i2_items))          # innings 2 exists -> innings 1 is over
    total1 = i1["last_score"][0] if i1["last_score"] else None
    i2 = _innings(i2_items)
    if i2["started"] and total1 is not None and i2["last_score"][0] > total1 and not i2["finished"]:
        i2 = _innings(i2_items, force_finished=True)                       # target passed in the middle of an over
    for i in (i1, i2):
        i.pop("last_score")
    return {"inn1": i1, "inn2": i2, "done": i2["finished"], "result": ""}


EMPTY_PROGRESS = {"inn1": {"started": False, "finished": False, "entries": [], "balls": None},
                  "inn2": {"started": False, "finished": False, "entries": [], "balls": None}, "done": False, "result": ""}


# ---------------------------------------------------------------- client
class ApiClient:
    def __init__(self, quota):
        self.quota = quota
        self.s = requests.Session()
        self.calls = 0                                       # API calls made since this process started
        self._info = {}                                      # match_id -> parsed info, once the toss is known

    def _call(self, name, match_id=None):
        self.quota.spend()                                   # raises QuotaExceeded when the daily limit is reached
        self.calls += 1
        print(f"[api] call #{self.calls}: {name} {match_id or ''} (quota left {self.quota.remaining()})", flush=True)
        if API_MODE == "rapidapi":
            method, path = RAPID[name]
            url = f"https://{RAPID_HOST}{path.format(id=match_id)}"
            headers, data = {"x-rapidapi-key": RAPID_KEY, "x-rapidapi-host": RAPID_HOST}, None
        else:
            method, path = DIRECT[name]
            url, headers = f"{API_BASE.rstrip('/')}/{path}/{API_TOKEN}", {}
            data = {"match_id": match_id} if match_id is not None else None
        r = self.s.request(method, url, data=data, headers=headers, timeout=20)
        left = r.headers.get("x-ratelimit-requests-remaining")
        if self.calls == 1:                                  # once: show which rate-limit headers RapidAPI sends
            print("[api] ratelimit headers:", {k: v for k, v in r.headers.items() if "ratelimit" in k.lower()}, flush=True)
        if left is not None:
            self.quota.sync(left)
        r.raise_for_status()
        js = r.json()
        if not js.get("status", True):
            if "no data" in str(js.get("msg", "")).lower():
                raise NoData(f"{name}: {js.get('msg')}")
            raise RuntimeError(f"{name}: {js.get('msg')}")
        return js

    @staticmethod
    def _keep(m):
        return (SERIES_FILTER.lower() in m["series"].lower() and not m["team_a"].startswith("TBC")
                and (not MATCH_TYPE or str(m.get("match_type", "")).upper() == MATCH_TYPE.upper())
                and not any(x in m["series"].lower() for x in EXCLUDE_SERIES))

    def upcoming(self):
        """1 call normally. liveMatches (2nd call) is only fetched when today's match is NOT in upcoming
        (i.e. it already started, e.g. after a restart mid-match). LIVE_ALWAYS=1 forces it (double-headers)."""
        ms = parse_upcoming(self._call("upcoming"))
        out = [m for m in ms if self._keep(m)]
        today = datetime.now(IST).strftime("%Y-%m-%d")
        has_today = any(m["start"].astimezone(IST).strftime("%Y-%m-%d") == today for m in out)
        live = []
        if not has_today or os.getenv("LIVE_ALWAYS") == "1":
            try:
                live = parse_live(self._call("live_list"))
            except (NoData, requests.RequestException, RuntimeError) as e:
                print(f"[api] liveMatches failed: {e!r}", flush=True)
                live = []                                    # QuotaExceeded is not caught on purpose
            seen = {m["match_id"] for m in ms}
            out += [m for m in live if m["match_id"] not in seen and self._keep(m)]
        print(f"[api] upcoming={len(ms)} kept={len(out)} live={len(live)}", flush=True)
        # manual override: set env FORCE_MATCH_ID (+ optional FORCE_START "19:00", FORCE_TEAMS "IND,WI")
        fid = os.getenv("FORCE_MATCH_ID", "").strip()
        if fid and fid not in {m["match_id"] for m in out}:
            hh, mm = (os.getenv("FORCE_START", "19:00").split(":") + ["0"])[:2]
            ta, tb = (os.getenv("FORCE_TEAMS", "A,B").split(",") + ["B"])[:2]
            start = datetime.now(IST).replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            out.append({"match_id": fid, "start": start, "team_a": ta.strip(), "team_b": tb.strip(),
                        "series": "forced", "venue": "", "match_type": ""})
        return out

    def prepare(self, match_id, start=None):
        """None until BOTH toss and playing XIs are out. Toss (info) is checked first: 1 call per attempt while
        waiting; once the toss is known it is cached, so later attempts cost only the XI call."""
        info = self._info.get(match_id)
        if info is None:
            try:
                info = parse_info(self._call("info", match_id))
            except NoData:
                return None
            if not info["batting_first"]:                    # toss not announced / text not understood
                return None
            self._info[match_id] = info
        try:
            xi = parse_playing11(self._call("playing11", match_id))
        except NoData:
            return None
        ta, tb = xi["team_a"], xi["team_b"]
        if not (len(ta["players"]) >= 11 and len(tb["players"]) >= 11):
            return None
        first = info["batting_first"]
        a, b = (ta, tb) if ta["code"] == first else (tb, ta)
        from tracker import with_role
        day = (start or datetime.now(IST)).strftime("%Y-%m-%d")
        return {"venue": info["venue"], "date": day, "toss": info["toss"], "batting_first": first,
                "team_a": {"code": a["code"], "name": a["name"]}, "team_b": {"code": b["code"], "name": b["name"]},
                "xi_a": [with_role(p["name"], p["role"]) for p in a["players"][:11]],
                "xi_b": [with_role(p["name"], p["role"]) for p in b["players"][:11]]}

    def progress(self, match_id):
        try:
            return parse_over_history(self._call("over_history", match_id))
        except NoData:                                       # before the first over is finished
            return EMPTY_PROGRESS

    def result_text(self, match_id):
        """official result string from matchInfo ('' while the match is live)."""
        return parse_info(self._call("info", match_id)).get("result", "")