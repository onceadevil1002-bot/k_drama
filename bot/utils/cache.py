import time
from cachetools import TTLCache

# Local TTL Cache for frequent checks
verify_cache = TTLCache(maxsize=50000, ttl=3600)  # 1 hour TTL

class DataCache:
    def __init__(self):
        self.data = None
        self.timestamp = 0
        self.ttl = 300  # 5 minutes

    def get(self):
        now = time.time()
        if self.data and (now - self.timestamp) < self.ttl:
            return self.data
        return None

    def set(self, data):
        self.data = data
        self.timestamp = time.time()

    def clear(self):
        self.data = None
        self.timestamp = 0

show_cache = DataCache()
