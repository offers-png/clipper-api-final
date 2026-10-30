"""Per-user sliding-window limit for paid AI calls.

In-memory and per process: a restart or a second instance starts fresh counts.
"""
import math
import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, limit, window, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.hits = {}
        self.lock = threading.Lock()

    def hit(self, key):
        """Record one request; return 0 when allowed, else seconds until a slot frees."""
        now = self.clock()
        with self.lock:
            hits = self.hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, math.ceil(hits[0] + self.window - now))
            hits.append(now)
            return 0
