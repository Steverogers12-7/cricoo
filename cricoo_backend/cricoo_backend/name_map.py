"""API player names ("Virat Kohli") -> training names ("V Kohli").  Unresolved names are returned as-is
(Model 1 then treats them as a new player) and reported, so you can add them to ALIASES."""
import re
import difflib

# API name (lowercase) -> training name.  Add rows here when the backend logs an unresolved player.
ALIASES = {
    "bhuvneshwar kumar": "B Kumar",
    "karn sharma": "KV Sharma",
    "dinesh karthik": "KD Karthik",
}


def clean(name):
    n = re.sub(r"\s+(IMP|\(IMP\)|sub)\s*$", "", str(name).strip(), flags=re.I)   # "Swapnil Singh IMP"
    return re.sub(r"\s*\((c|wk|c & wk|wk & c)\)\s*$", "", n, flags=re.I).strip()


class NameMapper:
    def __init__(self, model1):
        self.m1 = model1
        H = model1.H
        last = H.sort_values("date").groupby("player_name").agg(team=("team", "last"), date=("date", "max"), n=("match_id", "nunique"))
        self.info = last
        self.names = list(model1.NAME2ID)

    def resolve(self, api_name, team=None):
        """returns (training_name_or_api_name, resolved: bool)"""
        n = clean(api_name)
        key = n.lower()
        if key in ALIASES:
            return ALIASES[key], True
        if n in self.m1.NAME2ID:
            return n, True
        low = {x.lower(): x for x in self.names}
        if key in low:
            return low[key], True
        parts = n.split()
        if len(parts) >= 2:
            sur, ini = parts[-1].lower(), parts[0][0].lower()
            def first_ok(x):                                    # "V Kohli", or "KD Karthik" for "Dinesh Karthik"
                f = x.split()[0]
                return f[0].lower() == ini or (f.isupper() and len(f) <= 3 and ini.upper() in f)
            cand = [x for x in self.names if x.split()[-1].lower() == sur and first_ok(x)]
            if len(cand) > 1 and team:
                same = [x for x in cand if self.info.loc[x, "team"] == team]
                cand = same or cand
            if len(cand) > 1:                                   # most appearances wins
                cand.sort(key=lambda x: -self.info.loc[x, "n"])
                if self.info.loc[cand[0], "n"] >= 2 * self.info.loc[cand[1], "n"]:
                    cand = cand[:1]
            if len(cand) == 1:
                return cand[0], True
        close = difflib.get_close_matches(n, self.names, n=1, cutoff=0.85)
        if close:
            return close[0], True
        return n, False

    def resolve_xi(self, api_names, team=None):
        out, bad = [], []
        for a in api_names:
            r, ok = self.resolve(a, team)
            out.append(r)
            if not ok:
                bad.append(a)
        return out, bad
