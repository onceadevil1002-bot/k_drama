import hashlib
import base64
import time
import logging
from collections import OrderedDict
from bot.database.mongo import db

logger = logging.getLogger(__name__)

_hash_memory_cache = OrderedDict()

async def make_id(value: str) -> str:
    """Create stable 1char hash with MongoDB persistence."""
    if not value:
        return "empty"
    
    h = hashlib.sha256(value.strip().lower().encode()).hexdigest()[:16]
    
    try:
        await db.hash_lookup.update_one(
            {"hash": h},
            {
                "$set": {
                    "hash": h,
                    "value": value,
                    "last_accessed": time.time()
                }
            },
            upsert=True
        )
    except Exception as e:
        logger.debug(f"Hash storage error: {e}")
    
    return h

async def resolve_id(h: str) -> str:
    if not h or h == "empty":
        return h
    
    # Convert bytes to str if needed
    if isinstance(h, bytes):
        try:
            h = h.decode('utf-8')
        except (UnicodeDecodeError, AttributeError):
            return h
    
    if h in _hash_memory_cache:
        # Move to end (most recently used)
        _hash_memory_cache.move_to_end(h)
        return _hash_memory_cache.get(h, h) # Assuming hashed_id was a typo for h
    
    try:
        result = await db.hash_lookup.find_one(
            {"hash": h},
            {"value": 1, "_id": 0}
        )
        
        if result:
            value = result["value"]
            _hash_memory_cache[h] = value
            
            # Evict oldest if cache exceeds limit (max 10,000 entries)
            if len(_hash_memory_cache) > 10000:
                _hash_memory_cache.popitem(last=False)
            
            return value
    except Exception as e:
        logger.debug(f"Hash lookup error: {e}")
    
    return h

def normalize_show_slug(show_name: str) -> str:
    """Base64 encode show name for URLs."""
    if not show_name:
        return "unknown"
    encoded = base64.urlsafe_b64encode(show_name.encode('utf-8')).decode('ascii')
    return encoded.rstrip('=')

def decode_show_slug(slug: str) -> str:
    """Decode base64 slug to original name."""
    try:
        padded = slug + "=" * ((4 - len(slug) % 4) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode('utf-8')).decode('utf-8')
        return decoded
    except Exception:
        return slug.replace("_", " ")
