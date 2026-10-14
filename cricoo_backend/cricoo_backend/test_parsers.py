"""Parser tests on (trimmed) copies of the real API responses."""
import warnings; warnings.filterwarnings("ignore")
from datetime import datetime
import api_client as A
from engine import Engine

# ---- matchInfo (real response, trimmed to the keys we read)
info_js = {"msg": "Data found.", "status": True, "data": {
    "team_a": "Sunrisers Hyderabad", "team_a_short": "SRH", "team_b": "Royal Challengers Bengaluru", "team_b_short": "RCB",
    "venue": "Rajiv Gandhi International Stadium, Hyderabad", "series": "Indian Premier League 2024",
    "toss": "Royal Challengers Bangalore have won the toss and have opted to bat", "match_status": 3,
    "result": "Royal Challengers Bangalore won by 35 runs"}}
i = A.parse_info(info_js)
print("batting first:", i["batting_first"], "| venue:", i["venue"])
assert i["batting_first"] == "RCB"
a, b = i["team_a"], i["team_b"]
for toss, want in [("Sunrisers Hyderabad have won the toss and have opted to bowl", "RCB"),
                   ("Sunrisers Hyderabad have won the toss and have opted to bat", "SRH"),
                   ("Royal Challengers Bengaluru won the toss and elected to field", "SRH"), ("", None), ("Toss delayed due to rain", None)]:
    got = A.batting_first_from_toss(toss, a, b); assert got == want, (toss, got, want)
print("toss parsing OK (bat/bowl/field, Bangalore vs Bengaluru spelling, empty/odd text)")

# ---- upcomingMatches (two real rows + one fake IPL row)
up = {"data": [
  {"match_id": 14145, "team_a_short": "NSW", "team_b_short": "TAS", "series": "Australia Domestic One-Day Cup 2026-27", "date_wise": "13 Oct 2026, Tuesday", "match_time": "04:30 AM", "venue": "Cricket Central, Sydney", "match_type": "ODI"},
  {"match_id": 15260, "team_a_short": "PAK", "team_b_short": "SL", "series": "Sri Lanka tour of Pakistan, 2026", "date_wise": "13 Oct 2026, Tuesday", "match_time": "06:30 PM", "venue": "Rawalpindi Cricket Stadium, Rawalpindi", "match_type": "T20"},
  {"match_id": 99999, "team_a_short": "RCB", "team_b_short": "MI", "series": "Indian Premier League 2027", "date_wise": "28 Mar 2027, Sunday", "match_time": "07:30 PM", "venue": "M.Chinnaswamy Stadium, Bengaluru", "match_type": "T20"}]}
ms = A.parse_upcoming(up)
print([(m["match_id"], m["start"].strftime("%d %b %H:%M %Z"), m["team_a"], m["team_b"]) for m in ms])
assert ms[0]["start"].hour == 4 and ms[1]["start"].hour == 18 and ms[2]["start"] == datetime(2027, 3, 28, 19, 30, tzinfo=A.IST)
class Q:                                         # fake quota/session just to run the filter
    def spend(self): pass
c = A.ApiClient(Q()); c._call = lambda name, mid=None: up
f = c.upcoming(); assert [m["match_id"] for m in f] == ["99999"]; print("IPL filter OK ->", [(m["team_a"], m["team_b"]) for m in f])

# ---- venue strings: API spelling -> training venue
E = Engine("models")
tests = {"Rajiv Gandhi International Stadium, Hyderabad": "Rajiv Gandhi International Stadium", "M.Chinnaswamy Stadium, Bengaluru": "M Chinnaswamy Stadium",
  "MA Chidambaram Stadium, Chennai": "MA Chidambaram Stadium", "Wankhede Stadium, Mumbai": "Wankhede Stadium", "Eden Gardens, Kolkata": "Eden Gardens",
  "Arun Jaitley Stadium, Delhi": "Arun Jaitley Stadium", "Narendra Modi Stadium, Ahmedabad": "Narendra Modi Stadium", "Sawai Mansingh Stadium, Jaipur": "Sawai Mansingh Stadium",
  "Shaheed Veer Narayan Singh International Stadium, Raipur": "Shaheed Veer Narayan Singh Stadium, Raipur",
  "Barsapara Cricket Stadium, Barsapara, Guwahati, Assam": "Barsapara Stadium, Guwahati",
  "Maharaja Yadavindra Singh International Cricket Stadium, Mullanpur, Chandigarh": "Maharaja Yadavindra Singh Stadium, Mullanpur",
  "Himachal Pradesh Cricket Association Stadium, Dharamsala": "HPCA Stadium, Dharamsala",
  "Dr. Y.S. Rajasekhara Reddy ACA-VDCA Cricket Stadium, Visakhapatnam": "ACA-VDCA Stadium, Visakhapatnam",
  "Bharat Ratna Shri Atal Bihari Vajpayee Ekana Cricket Stadium, Lucknow": "Ekana Cricket Stadium, Lucknow"}
bad = 0
for k, v in tests.items():
    got = E.venue(k); ok = (got == v) or (v in got); bad += not ok
    print(("OK  " if ok else "MISS"), k, "->", got)
print("unmatched venues would be treated as a new venue (offset 0) - visible in /health" if bad else "all venues matched")
