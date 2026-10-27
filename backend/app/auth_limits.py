"""Bounded, thread-safe single-process throttling. Production needs shared limits."""
from functools import lru_cache
import hashlib
import math
from threading import Lock
import time

from fastapi import HTTPException


class AuthRateLimiter:
    def __init__(self, clock=time.monotonic, max_keys=10000):
        self.clock = clock
        self.max_keys = max_keys
        self.entries = {}
        self.lock = Lock()

    def check(self, rules):
        """Each rule is (scope, identity, maximum attempts, window seconds)."""
        with self.lock:
            now = self.clock()
            self.entries = {key: entry for key, entry in self.entries.items() if entry[1] > now}
            keys = []
            for scope, identity, limit, window in rules:
                key = (scope, hashlib.sha256(identity.encode('utf-8')).digest())
                count, expires = self.entries.get(key, (0, now + window))
                if count >= limit:
                    self._reject(max(1, math.ceil(expires - now)))
                keys.append((key, count, expires))
            if len(set(self.entries) | {key for key, _, _ in keys}) > self.max_keys:
                self._reject(60)
            for key, count, expires in keys:
                self.entries[key] = (count + 1, expires)

    @staticmethod
    def _reject(seconds):
        raise HTTPException(429, "Too many authentication requests. Please try again later.",
                            headers={"Retry-After": str(seconds), "Cache-Control": "no-store"})


@lru_cache(maxsize=1)
def get_auth_limiter():
    return AuthRateLimiter()
