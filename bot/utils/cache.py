import time
import asyncio
from cachetools import TTLCache

# Local TTL Cache for frequent checks
verify_cache = TTLCache(maxsize=50000, ttl=3600)  # 1 hour TTL


class DataCache:
    """
    Thread-safe cached data store with stampede protection.
    An asyncio.Lock ensures that when cache expires, only ONE coroutine
    fetches from MongoDB while all others await the result — not 50 parallel DB reads.
    """
    def __init__(self):
        self.data = None
        self.timestamp = 0
        self.ttl = 300  # 5 minutes
        self._lock = asyncio.Lock()

    def get(self):
        """Returns cached data if still valid, else None. Non-blocking."""
        now = time.time()
        if self.data is not None and (now - self.timestamp) < self.ttl:
            return self.data
        return None

    def set(self, data):
        self.data = data
        self.timestamp = time.time()

    def clear(self):
        self.data = None
        self.timestamp = 0

    @property
    def lock(self):
        """Expose the lock so callers can use 'async with show_cache.lock:'."""
        return self._lock


show_cache = DataCache()
