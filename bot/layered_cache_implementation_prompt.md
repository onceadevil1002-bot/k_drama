# Implementation Prompt: Layered Cache Architecture for KDrama Bot

## CONTEXT — READ THIS FIRST BEFORE TOUCHING ANY FILE

You are working on a Telegram bot (Pyrogram) that serves Korean drama content.
The bot has ~528 shows in MongoDB. Each show document contains the show name,
category, poster, and all episode data embedded inside it.

The bot currently has ONE cache (`show_cache` in `bot/utils/cache.py`) that loads
EVERYTHING from MongoDB into one Python dict. When it expires or gets cleared,
the whole thing reloads from scratch — causing 10+ second delays for users and admins.

Your job is to replace this single cache with a 3-layer cache system and fix how
admin actions update the cache. You are NOT changing the MongoDB schema. You are NOT
migrating any data. The database stays exactly as it is.

---

## WHAT YOU WILL CHANGE — FILE LIST

1. `bot/utils/cache.py` — Replace the entire file with the new layered cache
2. `bot/services/shows.py` — Rewrite `load_data()` and `get_cached_data()` to use new cache
3. `bot/database/admin_data_entry.py` — Replace all `show_cache.clear()` calls with targeted invalidation

Do NOT touch any other file unless it directly imports `show_cache` and calls `.clear()` or `.get()` on it.
If you find other files doing that, apply the same targeted invalidation fix described below.

---

## STEP 1 — REWRITE `bot/utils/cache.py`

Delete everything in this file and replace with exactly this:

```python
import asyncio
import time
import logging
from cachetools import TTLCache

logger = logging.getLogger(__name__)

# ── Existing verify cache — DO NOT TOUCH ──────────────────────────────────────
verify_cache = TTLCache(maxsize=50000, ttl=3600)


# ── NEW: Layered Cache ─────────────────────────────────────────────────────────

class LayeredCache:
    """
    Three-layer cache for show data.

    L1 — Category Index  (TTL: 10 minutes)
         Stores: { category_name → list of {show_name, poster} }
         Used for: showing the show list when a user clicks a category button.
         Size: small — only 7 categories, each with a list of show names.

    L2 — Show Detail     (TTL: 1 hour)
         Stores: { show_slug → full show document from MongoDB }
         Used for: showing seasons, episodes, qualities, and file IDs when a
                   user clicks on a specific show.
         Size: one entry per show that has been clicked. Grows lazily.

    Rules:
    - Neither layer is ever fully cleared.
    - When an admin imports an episode or updates a show, only that show's
      L2 entry is dropped. L1 is only touched if a show was added or deleted
      (because the show list changed). Everything else stays cached.
    - Stampede protection: asyncio.Lock per cache key so that when two users
      click the same thing at the same time, only ONE goes to MongoDB.
      The other waits and then reads the result that the first one fetched.
    """

    def __init__(self):
        # L1: category name → list of show metadata dicts
        self._l1: TTLCache = TTLCache(maxsize=20, ttl=600)
        self._l1_lock = asyncio.Lock()

        # L2: show_slug → full show document
        self._l2: TTLCache = TTLCache(maxsize=2000, ttl=3600)
        self._l2_locks: dict[str, asyncio.Lock] = {}

        # Track L2 load timestamps for background refresh logic
        self._l2_loaded_at: dict[str, float] = {}

    # ── L1 methods ─────────────────────────────────────────────────────────────

    async def get_category(self, category: str, loader_fn) -> list:
        """
        Get the list of shows for a category.
        If not cached, calls loader_fn(category) to fetch from MongoDB.
        loader_fn must be: async def loader(category: str) -> list
        """
        if category in self._l1:
            return self._l1[category]

        async with self._l1_lock:
            # Double-check: another coroutine may have loaded it while we waited
            if category in self._l1:
                return self._l1[category]

            logger.debug(f"LayeredCache L1 miss: loading category '{category}' from DB")
            data = await loader_fn(category)
            self._l1[category] = data
            return data

    def set_category(self, category: str, data: list):
        """Directly set L1 for a category (used after adding a new show)."""
        self._l1[category] = data

    def invalidate_category(self, category: str):
        """
        Drop a category from L1. Call this ONLY when a show is added or deleted
        from that category — because the show list changed.
        Do NOT call this for episode imports or poster updates.
        """
        self._l1.pop(category, None)
        logger.debug(f"LayeredCache L1 invalidated: category '{category}'")

    # ── L2 methods ─────────────────────────────────────────────────────────────

    def _get_l2_lock(self, slug: str) -> asyncio.Lock:
        """Get or create a per-slug lock."""
        if slug not in self._l2_locks:
            self._l2_locks[slug] = asyncio.Lock()
        return self._l2_locks[slug]

    async def get_show(self, slug: str, loader_fn) -> dict | None:
        """
        Get the full show document for a slug.
        If not cached, calls loader_fn() to fetch from MongoDB.
        loader_fn must be: async def loader() -> dict | None
        (no arguments — the caller already knows category+show_name)
        """
        if slug in self._l2:
            return self._l2[slug]

        lock = self._get_l2_lock(slug)
        async with lock:
            if slug in self._l2:
                return self._l2[slug]

            logger.debug(f"LayeredCache L2 miss: loading show '{slug}' from DB")
            data = await loader_fn()
            if data is not None:
                self._l2[slug] = data
                self._l2_loaded_at[slug] = time.time()
            return data

    def set_show(self, slug: str, data: dict):
        """Directly set L2 for a show (used after writing to DB)."""
        self._l2[slug] = data
        self._l2_loaded_at[slug] = time.time()

    def invalidate_show(self, slug: str):
        """
        Drop a show from L2. Call this after ANY admin action that modifies
        a show: episode import, poster update, show edit.
        The next user who clicks that show will re-fetch from MongoDB.
        L1 is NOT touched — the show still exists in the category list.
        """
        self._l2.pop(slug, None)
        self._l2_loaded_at.pop(slug, None)
        logger.debug(f"LayeredCache L2 invalidated: show '{slug}'")

    def needs_background_refresh(self, slug: str, refresh_before_seconds: int = 300) -> bool:
        """
        Returns True if a cached show is within `refresh_before_seconds` of
        its 1-hour TTL expiring. Use this to trigger background refresh so
        users never hit a cold miss.
        Default: refresh if loaded more than 55 minutes ago (5 min before expiry).
        """
        loaded_at = self._l2_loaded_at.get(slug)
        if loaded_at is None:
            return False
        age = time.time() - loaded_at
        return age > (3600 - refresh_before_seconds)


# Singleton — import this everywhere instead of the old show_cache
layered_cache = LayeredCache()


# ── LEGACY COMPATIBILITY ───────────────────────────────────────────────────────
# Keep the old DataCache class and show_cache only if other parts of the bot
# import show_cache directly. Once all callers are migrated, delete this.

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
```

---

## STEP 2 — REWRITE `bot/services/shows.py`

Replace the `load_data()` and `get_cached_data()` functions completely.
Keep `increment_view`, `get_trending_shows`, `_LEGACY_CATEGORY_MAP` exactly as they are.

**Delete `load_data()` and replace with these two functions:**

```python
async def load_category_index(category: str) -> list:
    """
    Load show metadata for ONE category from MongoDB.
    Does NOT fetch episode data — only show_name and poster.
    This is the L1 loader. Called only on cache miss for that category.
    """
    projection = {
        "show_name": 1,
        "poster": 1,
        "_id": 0
    }
    results = []
    try:
        cursor = db.shows.find({"category": category}, projection)
        async for doc in cursor:
            raw_category = category
            normalized = _LEGACY_CATEGORY_MAP.get(raw_category, raw_category)
            results.append({
                "show_name": doc.get("show_name", ""),
                "poster": doc.get("poster", []),
                "category": normalized
            })
        return results
    except Exception as e:
        logger.exception(f"load_category_index error for '{category}': {e}")
        return []


async def load_show_detail(category: str, show_name: str) -> dict | None:
    """
    Load the full document for ONE show from MongoDB.
    This includes all episode data — seasons, qualities, file IDs.
    This is the L2 loader. Called only on cache miss for that specific show.
    Hits the (category, show_name) index directly — fast regardless of
    how many shows are in the database.
    """
    try:
        raw_category_variants = [category]
        # Also check legacy names in case DB has old category names
        for old_name, canonical in _LEGACY_CATEGORY_MAP.items():
            if canonical == category:
                raw_category_variants.append(old_name)

        doc = await db.shows.find_one(
            {
                "category": {"$in": raw_category_variants},
                "show_name": show_name
            }
        )
        if doc and "_id" in doc:
            doc["_id"] = str(doc["_id"])  # Make serializable
        return doc
    except Exception as e:
        logger.exception(f"load_show_detail error for '{show_name}': {e}")
        return None
```

**Delete `get_cached_data()` and replace with these two functions:**

```python
async def get_category_shows(category: str) -> list:
    """
    Public API — use this everywhere you need the show list for a category.
    Returns from L1 cache if available. Fetches from DB on miss.
    What it returns: list of { show_name, poster, category }
    What it does NOT return: episode data (intentional — not needed for browsing)
    """
    from bot.utils.cache import layered_cache
    return await layered_cache.get_category(
        category,
        loader_fn=lambda cat=category: load_category_index(cat)
    )


async def get_show_detail(category: str, show_name: str) -> dict | None:
    """
    Public API — use this everywhere you need a show's full data
    (seasons, episodes, qualities, file IDs).
    Returns from L2 cache if available. Fetches from DB on miss.

    Also triggers a background refresh if the cached entry is close
    to its 1-hour expiry — so the user always gets a fast response.
    """
    from bot.utils.cache import layered_cache
    from bot.utils.ids import normalize_show_slug
    import asyncio

    slug = normalize_show_slug(show_name)

    # Check if we need a background refresh before fetching
    if layered_cache.needs_background_refresh(slug):
        # Schedule background refresh — does not block the current user
        asyncio.create_task(_background_refresh_show(category, show_name, slug))

    return await layered_cache.get_show(
        slug,
        loader_fn=lambda: load_show_detail(category, show_name)
    )


async def _background_refresh_show(category: str, show_name: str, slug: str):
    """
    Silently refresh a show's L2 cache entry in the background.
    Called when the entry is close to expiry. The user who triggered this
    gets the old cached data immediately. The next user gets the fresh data.
    """
    from bot.utils.cache import layered_cache
    try:
        fresh_data = await load_show_detail(category, show_name)
        if fresh_data:
            layered_cache.set_show(slug, fresh_data)
            logger.debug(f"Background refresh complete for '{show_name}'")
    except Exception as e:
        logger.debug(f"Background refresh failed for '{show_name}': {e}")
```

**Keep these functions exactly as they are — do not touch them:**
- `increment_view()`
- `get_trending_shows()`
- `_LEGACY_CATEGORY_MAP`

---

## STEP 3 — FIX `bot/database/admin_data_entry.py`

There are exactly 3 places in this file that call `show_cache.clear()`. Find each one
and replace it with targeted cache invalidation. Here is exactly what to do:

### Fix 3a — Inside `handle_import_receive()` (after episode is saved to DB)

**Find this block (around line 363):**
```python
# Clear cache
from bot.utils.cache import show_cache
show_cache.clear()
```

**Replace it with:**
```python
# Targeted cache invalidation — only drop this specific show from L2.
# L1 (category show list) is NOT touched — the show still exists,
# only its episode content changed.
from bot.utils.cache import layered_cache
from bot.utils.ids import normalize_show_slug
layered_cache.invalidate_show(normalize_show_slug(show_name))
```

---

### Fix 3b — Inside `handle_poster_receive()` (after poster is saved to DB)

**Find this block (around line 468):**
```python
from bot.utils.cache import show_cache
show_cache.clear()
```

**Replace it with:**
```python
from bot.utils.cache import layered_cache
from bot.utils.ids import normalize_show_slug
layered_cache.invalidate_show(normalize_show_slug(state["show"]))
```

---

### Fix 3c — Inside `add_show_cmd()` (after a new show is added to DB)

This is the ONLY case where L1 must also be invalidated, because a new show
means the category's show list changed.

Find the `add_show_cmd` function. After it successfully inserts/updates the show
in MongoDB, find any `show_cache.clear()` call and replace with:

```python
from bot.utils.cache import layered_cache
from bot.utils.ids import normalize_show_slug
# New show added — invalidate L1 so the category list refreshes
layered_cache.invalidate_category(category)
# Also drop L2 for this show if it was somehow cached already
layered_cache.invalidate_show(normalize_show_slug(show_name))
```

---

### Fix 3d — Inside `delete_command_handler()` (after a show/episode is deleted)

Same logic as add_show_cmd — if a show is fully deleted, L1 must be invalidated.
If only an episode is deleted (show still exists), only L2 needs invalidation.

For full show deletion:
```python
from bot.utils.cache import layered_cache
from bot.utils.ids import normalize_show_slug
layered_cache.invalidate_category(category)
layered_cache.invalidate_show(normalize_show_slug(show_name))
```

For episode-only deletion (show still exists in the list):
```python
from bot.utils.cache import layered_cache
from bot.utils.ids import normalize_show_slug
layered_cache.invalidate_show(normalize_show_slug(show_name))
# Do NOT call invalidate_category — the show still exists
```

---

## STEP 4 — FIND AND FIX ALL OTHER `show_cache.clear()` CALLS

Search the entire codebase for:
```
show_cache.clear()
```

For every occurrence you find outside the files already fixed above:
- If it is called after modifying a specific show → use `layered_cache.invalidate_show(slug)`
- If it is called after adding/deleting a show → also call `layered_cache.invalidate_category(category)`
- If it is called after a bulk operation that changes many shows → call `layered_cache.invalidate_category(category)` for each affected category

Never call a method that clears everything. There is no such method on `LayeredCache` — this is intentional.

---

## STEP 5 — UPDATE ALL CALL SITES OF `get_cached_data()`

Search the entire codebase for:
```
get_cached_data()
```

For each occurrence, determine what the caller actually needs:

**Case A: Caller needs the show list for a category (to display shows to user)**
Replace:
```python
data = await get_cached_data()
shows_in_category = data.get(category, {})
```
With:
```python
from bot.services.shows import get_category_shows
shows_in_category = await get_category_shows(category)
```

**Case B: Caller needs full episode data for a specific show**
Replace:
```python
data = await get_cached_data()
show_data = data.get(category, {}).get(show_name, {})
```
With:
```python
from bot.services.shows import get_show_detail
show_data = await get_show_detail(category, show_name)
```

**Case C: Caller uses `get_cached_data()` just to validate a show name exists**
(For example, in `import_command_handler` which only checks if the show name is valid)
Replace:
```python
data = await get_cached_data()
actual_show_name = find_show_in_data(data, category, show_name_input)
```
With:
```python
# Direct DB query — hits the index, no cache overhead, no full scan
from bot.utils.ids import normalize_show_slug
from bot.utils.slug import normalize_slug
query_slug = normalize_slug(show_name_input)
show_doc = await db.shows.find_one(
    {"category": category, "show_name": {"$regex": f"^{re.escape(show_name_input)}$", "$options": "i"}},
    {"show_name": 1}
)
if not show_doc:
    return await message.reply(f"❌ Show '{show_name_input}' not found in {category}.")
actual_show_name = show_doc["show_name"]
```

---

## WHAT NOT TO DO

- Do NOT delete `show_cache` from `cache.py` yet. Keep it at the bottom as legacy until
  you have confirmed every caller is migrated to `layered_cache`.
- Do NOT call `layered_cache.invalidate_category()` when only an episode changes.
  The category show list did not change — only the show's internal data changed.
- Do NOT add any method to `LayeredCache` that clears all of L1 or all of L2 at once.
  If you feel you need one, that is a signal that something else is wrong.
- Do NOT change the MongoDB schema, collection names, or document structure.
- Do NOT modify `mongo.py`.
- Do NOT modify `increment_view()` or `get_trending_shows()` in `shows.py`.

---

## HOW TO VERIFY IT WORKS

After implementation, the following behavior should be true:

1. Bot starts → nothing is loaded into cache yet.
2. User clicks "K-Hindi" category → L1 miss → DB query fetches only show names and posters for K-Hindi → cached for 10 min.
3. Same user or another user clicks "K-Hindi" again within 10 min → L1 hit → instant response, no DB query.
4. User clicks a specific show → L2 miss → DB query fetches that ONE show's full document including episodes → cached for 1 hour.
5. Same show clicked again within 1 hour → L2 hit → instant response.
6. Admin imports an episode for that show → DB updated → `invalidate_show(slug)` called → L2 entry for that show is dropped → L1 untouched.
7. Next user clicks that show → L2 miss → DB query fetches fresh data → cached again for 1 hour.
8. Admin adds a brand new show → DB updated → `invalidate_category(category)` AND `invalidate_show(slug)` called → next category click fetches fresh list from DB.
9. At no point is the entire cache cleared. At no point is the entire `shows` collection scanned.
```
