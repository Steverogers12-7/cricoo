import json, os, threading
from datetime import datetime
from zoneinfo import ZoneInfo
from api_client import QuotaExceeded

IST = ZoneInfo("Asia/Kolkata")


class QuotaGuard:
    """Persistent per-day call counter (survives restarts). Keep a few calls in reserve."""
    def __init__(self, path="quota.json", limit=95):
        self.path, self.limit, self.lock = path, limit, threading.Lock()
        self.day, self.used = self._today(), 0
        try:
            d = json.load(open(path))
            if d.get("day") == self.day:
                self.used = int(d["used"])
        except Exception:
            pass

    def _today(self):
        return datetime.now(IST).strftime("%Y-%m-%d")

    def remaining(self):
        with self.lock:
            if self._today() != self.day:
                self.day, self.used = self._today(), 0
            return self.limit - self.used

    def spend(self, n=1):
        with self.lock:
            if self._today() != self.day:
                self.day, self.used = self._today(), 0
            if self.used + n > self.limit:
                raise QuotaExceeded(f"{self.used}/{self.limit} used today")
            self.used += n
            try:
                json.dump({"day": self.day, "used": self.used}, open(self.path, "w"))
            except Exception:
                pass
