import logging
from datetime import datetime
from bot.database.mongo import db

logger = logging.getLogger(__name__)

async def calculate_max_profile(user_id):
    """Generate or retrieve a deep analysis profile for a user."""
    try:
        # Check if we have one, or generate it
        user_doc = await db.userdb.find_one({"user_id": user_id})
        if not user_doc:
            return None
            
        # Example metrics calculation
        profile = {
            "user_id": user_id,
            "full_name": user_doc.get("full_name", "Unknown"),
            "username": user_doc.get("username"),
            "interaction_count": user_doc.get("interaction_count", 0),
            "last_interaction": user_doc.get("last_interaction", datetime.now()),
            "status": "active" if user_doc.get("interaction_count", 0) > 10 else "passive",
            "is_premium": user_doc.get("is_premium", False)
        }
        
        # Save to users_max
        await db.users_max.update_one(
            {"user_id": user_id},
            {"$set": profile},
            upsert=True
        )
        return profile
    except Exception as e:
        logger.error(f"Error calculating max profile: {e}")
        return None

def format_max_profile_text(profile, user_id):
    """Format the profile dictionary into a readable string."""
    if not profile:
        return f"❌ Profile for `{user_id}` not found."
        
    return (
        f"🧠 **DEEP ANALYSIS (Max-Profile)**\n"
        f"🆔 ID: `{user_id}`\n"
        f"👤 Name: {profile['full_name']}\n"
        f"📛 Username: @{profile['username'] or 'None'}\n\n"
        f"📊 **Metrics:**\n"
        f"├ Interactions: {profile['interaction_count']}\n"
        f"├ Last Seen: {profile['last_interaction'].strftime('%Y-%m-%d %H:%M')}\n"
        f"├ Status Class: {profile['status'].upper()}\n"
        f"└ Premium: {'✅' if profile['is_premium'] else '❌'}"
    )
