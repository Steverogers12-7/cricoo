"""Replays a fake match in (simulated) real time through the same interface as ApiClient. For tests / front-end dev."""
from datetime import timedelta

from quota import IST

D = dict(
    runs1=[6, 9, 7, 12, 8, 10, 5, 11, 9, 8, 7, 9, 8, 9, 10, 9, 10, 11, 10, 13],
    wk1=[0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0],
    runs2=[8, 7, 11, 6, 9, 12, 8, 10, 7, 9, 9, 8, 11, 7, 9, 10, 8, 12, 9, 8],
    wk2=[0, 1, 0, 1, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1])
XI_A = ["Faf du Plessis", "Virat Kohli", "Rajat Patidar", "Cameron Green", "Will Jacks", "Dinesh Karthik (WK)",
        "Mahipal Lomror", "Karn Sharma", "Lockie Ferguson", "Mohammed Siraj", "Yash Dayal"]
XI_B = ["Abhishek Sharma", "Aiden Markram", "Heinrich Klaasen (WK)", "Nitish Kumar Reddy", "Abdul Samad", "Shahbaz Ahmed",
        "Pat Cummins", "Jaydev Unadkat", "Bhuvneshwar Kumar", "Mayank Markande", "T Natarajan"]


class MockClient:
    OVER_S, BREAK_S = 200, 15 * 60

    def __init__(self, quota, clock, start):
        self.quota, self.clock, self.start = quota, clock, start

    def upcoming(self):
        self.quota.spend()
        return [{"match_id": "demo1", "start": self.start, "team_a": "RCB", "team_b": "SRH"}]

    def prepare(self, match_id, start=None):
        self.quota.spend()
        if self.clock() < self.start - timedelta(minutes=25):          # XI comes out ~25 min before the start
            return None
        self.quota.spend()
        return {"venue": "M Chinnaswamy Stadium, Bengaluru", "date": self.start.strftime("%Y-%m-%d"),
                "toss": "RCB won the toss and chose to bat", "batting_first": "RCB",
                "team_a": {"code": "RCB", "name": "Royal Challengers Bengaluru"},
                "team_b": {"code": "SRH", "name": "Sunrisers Hyderabad"}, "xi_a": XI_A, "xi_b": XI_B}

    @staticmethod
    def _entries(runs, wk, n):
        out, cr, cw = [], 0, 0
        for k in range(1, n + 1):
            cr += runs[k - 1]; cw += wk[k - 1]; out.append((6 * k, cr, cw))
        return out

    def progress(self, match_id):
        self.quota.spend()
        el = (self.clock() - self.start).total_seconds()
        n1 = max(0, min(20, int(el // self.OVER_S)))
        t2 = el - 20 * self.OVER_S - self.BREAK_S
        n2 = 0 if t2 < 0 else min(20, int(t2 // self.OVER_S))
        e1, e2 = self._entries(D["runs1"], D["wk1"], n1), self._entries(D["runs2"], D["wk2"], n2)
        done = n2 >= 20
        return {"inn1": {"started": el > 0, "finished": n1 >= 20, "entries": e1, "balls": None},
                "inn2": {"started": t2 >= 0, "finished": done, "entries": e2, "balls": None},
                "done": done, "result": "RCB won by 4 runs" if done else ""}

    def result_text(self, match_id):
        self.quota.spend()
        return ""
