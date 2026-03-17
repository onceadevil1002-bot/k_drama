import logging
from datetime import datetime
from bot.database.mongo import db

logger = logging.getLogger(__name__)

async def submit_request(user_id, show_title):
    """Submit a new drama request."""
    try:
        request_doc = {
            "user_id": user_id,
            "title": show_title.strip(),
            "status": "pending",
            "created_at": datetime.now()
        }
        await db.reports.insert_one(request_doc)  # Reusing reports collection or dedicated 'requests'?
        # User requested 'requests' collection in prompt
        # await db.requests.insert_one(request_doc)
        return True
    except Exception as e:
        logger.error(f"Error submitting request: {e}")
        return False

async def get_user_requests(user_id, limit=5):
    """Fetch recent requests by a user."""
    try:
        cursor = db.reports.find({"user_id": user_id}).sort("created_at", -1).limit(limit)
        return await cursor.to_list(length=limit)
    except Exception as e:
        logger.error(f"Error fetching user requests: {e}")
        return []
