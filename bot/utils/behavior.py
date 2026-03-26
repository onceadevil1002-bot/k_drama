"""
Phase 2 — Behavioral Tracking
Single entry point for all user behavior events.
All writes are fire-and-forget via asyncio.create_task() — never blocks handlers.

Event types:
  search        — user ran /search or inline query
  category_open — user opened a category
  show_open     — user opened a show details page
  episode_watch — user actually received an episode file
  fav_add       — user added a show to favorites
  fav_remove    — user removed a show from favorites
"""

import asyncio
import logging
from datetime import datetime
from bot.database.mongo import db

logger = logging.getLogger(__name__)

# Per-collection caps to protect MongoDB free tier
_CAPS = {
    "search_queries":    50,
    "categories_browsed": 100,
    "shows_opened":      100,
    "episodes_watched":  200,
}


async def track_behavior(user_id: int, event: str, data: dict):
    """
    Write a behavioral event to the user's DB record.
    Always call via asyncio.create_task() — never await directly in handlers.

    data must include at minimum whatever is relevant for that event type.
    Timestamps are added automatically.
    """
    if not user_id:
        return
    try:
        now = datetime.now()
        data["ts"] = now

        if event == "search":
            # {query, results_count}
            await db.userdb.update_one(
                {"user_id": user_id},
                {"$push": {
                    "search_queries": {
                        "$each":  [data],
                        "$slice": -_CAPS["search_queries"]
                    }
                }}
            )

        elif event == "category_open":
            # {category}
            # Upsert into categories_browsed array — increment count if exists
            existing = await db.userdb.find_one(
                {"user_id": user_id, "categories_browsed.category": data["category"]},
                {"categories_browsed.$": 1}
            )
            if existing and existing.get("categories_browsed"):
                await db.userdb.update_one(
                    {"user_id": user_id, "categories_browsed.category": data["category"]},
                    {
                        "$inc": {"categories_browsed.$.count": 1},
                        "$set": {"categories_browsed.$.last_at": now}
                    }
                )
            else:
                await db.userdb.update_one(
                    {"user_id": user_id},
                    {"$push": {
                        "categories_browsed": {
                            "$each":  [{"category": data["category"], "count": 1, "last_at": now}],
                            "$slice": -_CAPS["categories_browsed"]
                        }
                    }}
                )

        elif event == "show_open":
            # {show_name, category}
            await db.userdb.update_one(
                {"user_id": user_id},
                {"$push": {
                    "shows_opened": {
                        "$each":  [data],
                        "$slice": -_CAPS["shows_opened"]
                    }
                }}
            )

        elif event == "episode_watch":
            # {show_name, category, season, episode, quality}
            await db.userdb.update_one(
                {"user_id": user_id},
                {"$push": {
                    "episodes_watched": {
                        "$each":  [data],
                        "$slice": -_CAPS["episodes_watched"]
                    }
                }}
            )

        elif event in ("fav_add", "fav_remove"):
            # {show_name, category}
            await db.userdb.update_one(
                {"user_id": user_id},
                {"$push": {
                    "fav_events": {
                        "$each":  [{"action": event, **data}],
                        "$slice": -50
                    }
                }}
            )

    except Exception as e:
        logger.debug(f"track_behavior error [{event}] for {user_id}: {e}")
