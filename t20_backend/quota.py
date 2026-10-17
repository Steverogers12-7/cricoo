import json, os, threading
from datetime import datetime
from zoneinfo import ZoneInfo
from api_client import QuotaExceeded

IST = ZoneInfo("Asia/Kolkata")
RESERVE = int(os.getenv("QUOTA_RESERVE", "3"))      # calls kept unused when RapidAPI's own count is known


class QuotaGuard:
    """Per-day call counter. Local count (quota.json, lost on Render restarts) + RapidAPI's own
    'remaining' header, which is the truth. remaining() = the smaller of the two."""
    def __init__(self, path="quota.json", limit=95):
        self.path, self.limit, self.lock = path, limit, threading.Lock()
        self.day, self.used = self._today(), 0
        self.api_left = None                          # last 'remaining' reported by RapidAPI today
        try:
            d = json.load(open(path))
            if d.get("day") == self.day:
                self.used = int(d["used"])
        except Exception:
            pass

    def _today(self):
        return datetime.now(IST).strftime("%Y-%m-%d")

    def _roll(self):
        if self._today() != self.day:
            self.day, self.used, self.api_left = self._today(), 0, None

    def _remaining(self):
        left = self.limit - self.used
        if self.api_left is not None:
            left = min(left, self.api_left - RESERVE)
        return left

    def sync(self, header_value):
        """Call with the x-ratelimit-requests-remaining header of the latest response."""
        try:
            v = int(float(header_value))
        except Exception:
            return
        with self.lock:
            self._roll()
            self.api_left = v

    def remaining(self):
        with self.lock:
            self._roll()
            return self._remaining()

    def spend(self, n=1):
        with self.lock:
            self._roll()
            if self._remaining() < n:
                raise QuotaExceeded(f"local {self.used}/{self.limit}, rapidapi_left={self.api_left}")
            self.used += n
            try:
                json.dump({"day": self.day, "used": self.used}, open(self.path, "w"))
            except Exception:
                pass