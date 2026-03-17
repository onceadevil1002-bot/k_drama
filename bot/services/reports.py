import logging
from datetime import datetime
from bson import ObjectId
from bot.database.mongo import db

logger = logging.getLogger(__name__)

async def create_report(user, chat, report_data):
    """
    Create a new report in the database.
    Ensures all user details are captured for admin search.
    """
    try:
        first_name = getattr(user, "first_name", "") or ""
        last_name = getattr(user, "last_name", "") or ""
        full_name = f"{first_name} {last_name}".strip() or "Unknown"
        
        report = {
            "user": {
                "user_id": user.id,
                "first_name": first_name,
                "last_name": last_name,
                "full_name": full_name,
                "username": getattr(user, "username", "") or ""
            },
            "chat_id": chat.id,
            "report": str(report_data), # Stringify if complex
            "status": "pending",
            "created_at": datetime.now()
        }
        result = await db.reports.insert_one(report)
        return result.inserted_id
    except Exception as e:
        logger.error(f"Error creating report: {e}")
        return None

async def update_report_status(report_id, new_status):
    """Update the status of a report."""
    try:
        if isinstance(report_id, str):
            report_id = ObjectId(report_id)
        
        await db.reports.update_one(
            {"_id": report_id},
            {"$set": {"status": new_status, "updated_at": datetime.now()}}
        )
        return True
    except Exception as e:
        logger.error(f"Error updating report status: {e}")
        return False

async def get_report(report_id):
    """Get a report by ID."""
    try:
        if isinstance(report_id, str):
            report_id = ObjectId(report_id)
        return await db.reports.find_one({"_id": report_id})
    except Exception as e:
        logger.error(f"Error fetching report: {e}")
        return None

async def delete_report(report_id):
    """Delete a report."""
    try:
        if isinstance(report_id, str):
            report_id = ObjectId(report_id)
        await db.reports.delete_one({"_id": report_id})
        return True
    except Exception as e:
        logger.error(f"Error deleting report: {e}")
        return False
