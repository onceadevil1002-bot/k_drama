import time
import asyncio
from collections import deque
from threading import Lock

class RateLimiter:
    """Rate limiter to prevent FloodWait errors."""
    def __init__(self, max_per_second=10):
        self.max_per_second = max_per_second
        self.timestamps = deque()
        self.lock = Lock()
    
    async def acquire(self):
        """Wait if rate limit would be exceeded."""
        with self.lock:
            now = time.time()
            
            # Remove timestamps older than 1 second
            while self.timestamps and self.timestamps[0] < now - 1:
                self.timestamps.popleft()
            
            # Check if we need to wait
            if len(self.timestamps) >= self.max_per_second:
                sleep_time = 1 - (now - self.timestamps[0])
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                    now = time.time()
            
            self.timestamps.append(now)

# Global instances
notification_limiter = RateLimiter(max_per_second=8)
request_limiter = RateLimiter(max_per_second=2)
