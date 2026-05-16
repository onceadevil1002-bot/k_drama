import asyncio
import time
import logging
from cachetools import TTLCache

logger = logging.getLogger(__name__)

# ── Existing verify cache — DO NOT TOUCH ──────────────────────────────────────
verify_cache = TTLCache(maxsize=50000, ttl=3600)


# ── Helper for composite cache keys ────────────────────────────────────────────
def _make_show_cache_key(category: str, show_slug: str) -> str:
    """Generate cache key that includes category to differentiate same show in different categories."""
    return f"{category}:{show_slug}"


# ── NEW: Layered Cache ─────────────────────────────────────────────────────────

class LayeredCache:
    """
    Three-layer cache for show data.

    L1 — Category Index  (TTL: 10 minutes)
         Stores: { category_name → list of {show_name, poster} }
         Used for: showing the show list when a user clicks a category button.

    L2 — Show Detail     (TTL: 1 hour)
         Stores: { category:show_slug → full show document from MongoDB }
         Used for: showing seasons, episodes, qualities, and file IDs.

    Rules:
    - Neither layer is ever fully cleared.
    - Episode imports → invalidate_show() only.
    - Show added/deleted → invalidate_category() + invalidate_show().
    - Stampede protection: asyncio.Lock per cache key.
    - Cache key for L2 includes category to differentiate same show across categories.
    """

    def __init__(self):
        # L1: category name → list of show metadata dicts
        self._l1: TTLCache = TTLCache(maxsize=20, ttl=600)
        self._l1_lock = asyncio.Lock()

        # L2: category:show_slug → full show document
        self._l2: TTLCache = TTLCache(maxsize=2000, ttl=3600)
        self._l2_locks: dict = {}

        # Track L2 load timestamps for background refresh logic
        self._l2_loaded_at: dict = {}

    # ── L1 methods ─────────────────────────────────────────────────────────────

    async def get_category(self, category: str, loader_fn) -> list:
        if category in self._l1:
            return self._l1[category]

        async with self._l1_lock:
            if category in self._l1:
                return self._l1[category]

            logger.debug(f"LayeredCache L1 miss: loading category '{category}' from DB")
            data = await loader_fn()
            self._l1[category] = data
            return data

    def set_category(self, category: str, data: list):
        self._l1[category] = data

    def invalidate_category(self, category: str):
        self._l1.pop(category, None)
        logger.debug(f"LayeredCache L1 invalidated: category '{category}'")

    # ── L2 methods ─────────────────────────────────────────────────────────────

    def _get_l2_lock(self, cache_key: str) -> asyncio.Lock:
        if cache_key not in self._l2_locks:
            self._l2_locks[cache_key] = asyncio.Lock()
        return self._l2_locks[cache_key]

    async def get_show(self, cache_key: str, loader_fn) -> dict | None:
        if cache_key in self._l2:
            return self._l2[cache_key]

        lock = self._get_l2_lock(cache_key)
        async with lock:
            if cache_key in self._l2:
                return self._l2[cache_key]

            logger.debug(f"LayeredCache L2 miss: loading show '{cache_key}' from DB")
            data = await loader_fn()
            if data is not None:
                self._l2[cache_key] = data
                self._l2_loaded_at[cache_key] = time.time()
            return data

    def set_show(self, cache_key: str, data: dict):
        self._l2[cache_key] = data
        self._l2_loaded_at[cache_key] = time.time()

    def invalidate_show(self, cache_key: str):
        self._l2.pop(cache_key, None)
        self._l2_loaded_at.pop(cache_key, None)
        logger.debug(f"LayeredCache L2 invalidated: show '{cache_key}'")

    def needs_background_refresh(self, cache_key: str, refresh_before_seconds: int = 300) -> bool:
        loaded_at = self._l2_loaded_at.get(cache_key)
        if loaded_at is None:
            return False
        age = time.time() - loaded_at
        return age > (3600 - refresh_before_seconds)


# Singleton — import this everywhere
layered_cache = LayeredCache()


# ── LEGACY COMPATIBILITY ───────────────────────────────────────────────────────
# Kept so any remaining references to show_cache don't crash at import.
# Will be fully removed once all callers are migrated.

class DataCache:
    def __init__(self):
        self.data = None
        self.timestamp = 0
        self.ttl = 300
        self._lock = asyncio.Lock()

    def get(self):
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
        return self._lock


show_cache = DataCache()  # Legacy — will be removed after full migration
