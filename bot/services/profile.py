"""
Phase 3 — Max Profile (Deep Analysis)
Reads live from userdb + aggregates from favorites + reports.
No separate users_max collection — compute on demand.
"""
import logging
from datetime import datetime
from bot.database.mongo import db

logger = logging.getLogger(__name__)


async def calculate_max_profile(user_id: int) -> dict:
    """
    Build full deep-analysis profile from userdb.
    Computes: preferred_category, activity_score, browse_to_watch_ratio,
    peak_hour, total_favorites, total_reports.
    """
    try:
        u = await db.userdb.find_one({"user_id": user_id})
        if not u:
            return None

        # Preferred category — most browsed
        categories = u.get("categories_browsed", [])
        preferred_category = (
            max(categories, key=lambda x: x.get("count", 0))["category"]
            if categories else "N/A"
        )

        # Activity score — interaction_count weighted by recency
        interaction_count = u.get("interaction_count", 0)
        last_interaction  = u.get("last_interaction")
        days_since_last   = 999
        if last_interaction:
            days_since_last = (datetime.now() - last_interaction).days
        if days_since_last <= 1:
            activity_score = min(100, interaction_count * 2)
        elif days_since_last <= 7:
            activity_score = min(100, interaction_count)
        elif days_since_last <= 30:
            activity_score = min(50, interaction_count // 2)
        else:
            activity_score = min(10, interaction_count // 10)

        # Peak hour — from interaction_timestamps
        timestamps = u.get("interaction_timestamps", [])
        peak_hour = "N/A"
        if timestamps:
            hour_counts = {}
            for ts in timestamps:
                h = ts.hour if hasattr(ts, "hour") else 0
                hour_counts[h] = hour_counts.get(h, 0) + 1
            peak_hour = f"{max(hour_counts, key=hour_counts.get):02d}:00"

        # Browse to watch ratio
        shows_opened    = len(u.get("shows_opened", []))
        episodes_watched = len(u.get("episodes_watched", []))
        if shows_opened > 0:
            bw_ratio = f"{episodes_watched}/{shows_opened}"
        else:
            bw_ratio = "N/A"

        # Favorites count from db.favorites
        total_favorites = await db.favorites.count_documents({"user_id": user_id})

        # Reports count
        total_reports = await db.reports.count_documents({"user.user_id": user_id})

        # Search queries summary
        searches = u.get("search_queries", [])
        recent_searches = [s.get("query", "") for s in searches[-5:]]

        # Common chats
        common_chats = u.get("common_chats", [])

        return {
            "user_id":          user_id,
            "full_name":        u.get("full_name", "Unknown"),
            "username":         u.get("username", ""),
            "first_name":       u.get("first_name", ""),
            "last_name":        u.get("last_name", ""),
            "language_code":    u.get("language_code", "Unknown"),
            "dc_id":            u.get("dc_id", "?"),
            "is_premium":       u.get("is_premium", False),
            "is_verified":      u.get("is_verified", False),
            "is_scam":          u.get("is_scam", False),
            "is_fake":          u.get("is_fake", False),
            "profile_pic":      u.get("profile_pic"),
            "profile_pic_history": u.get("profile_pic_history", []),
            "created_at":       u.get("created_at"),
            "last_interaction": last_interaction,
            "interaction_count": interaction_count,
            "activity_score":   activity_score,
            "preferred_category": preferred_category,
            "browse_to_watch_ratio": bw_ratio,
            "peak_hour":        peak_hour,
            "total_favorites":  total_favorites,
            "total_reports":    total_reports,
            "recent_searches":  recent_searches,
            "common_chats_count": len(common_chats),
            "common_chats":     common_chats,
            "known_groups":     [c for c in u.get("chats", [])],
            "watch_history":    u.get("watch_history", []),
            "warned":           u.get("warned", False),
            "banned":           False,  # checked separately
        }
    except Exception as e:
        logger.error(f"calculate_max_profile error for {user_id}: {e}")
        return None


def format_max_profile_text(profile: dict) -> str:
    """Format full profile into Telegram message text."""
    if not profile:
        return "❌ Profile not found."

    user_id = profile["user_id"]
    last_seen = "N/A"
    if profile.get("last_interaction"):
        try:
            last_seen = profile["last_interaction"].strftime("%Y-%m-%d %H:%M")
        except Exception:
            last_seen = str(profile["last_interaction"])

    first_seen = "N/A"
    if profile.get("created_at"):
        try:
            first_seen = profile["created_at"].strftime("%Y-%m-%d")
        except Exception:
            first_seen = str(profile["created_at"])

    searches_text = ", ".join(profile["recent_searches"]) if profile["recent_searches"] else "None"

    flags = []
    if profile["is_premium"]:  flags.append("💎 Premium")
    if profile["is_verified"]: flags.append("✅ Verified")
    if profile["is_scam"]:     flags.append("⚠️ SCAM")
    if profile["is_fake"]:     flags.append("⚠️ FAKE")
    if profile["warned"]:      flags.append("🔶 Warned")
    flags_text = " | ".join(flags) if flags else "None"

    return (
        f"🧠 **DEEP ANALYSIS**\n\n"
        f"🆔 ID: `{user_id}`\n"
        f"👤 Name: {profile['full_name']}\n"
        f"📛 Username: @{profile['username'] or 'None'}\n"
        f"🌐 Language: {profile['language_code']} | DC: {profile['dc_id']}\n"
        f"🏷 Flags: {flags_text}\n\n"
        f"📊 **Activity**\n"
        f"├ Score: {profile['activity_score']}/100\n"
        f"├ Interactions: {profile['interaction_count']}\n"
        f"├ First Seen: {first_seen}\n"
        f"├ Last Seen: {last_seen}\n"
        f"└ Peak Hour: {profile['peak_hour']}\n\n"
        f"🎬 **Content Behaviour**\n"
        f"├ Preferred Category: {profile['preferred_category']}\n"
        f"├ Browse→Watch: {profile['browse_to_watch_ratio']}\n"
        f"├ Favorites: {profile['total_favorites']}\n"
        f"├ Reports Filed: {profile['total_reports']}\n"
        f"└ Recent Searches: {searches_text}\n\n"
        f"🌐 **Social**\n"
        f"├ Common Chats: {profile['common_chats_count']}\n"
        f"└ Known Groups: {len(profile['known_groups'])}"
    )
