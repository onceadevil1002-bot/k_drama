import asyncio
import json
import os
import time
from datetime import datetime
from bot.database.mongo import db
from bot.config import ADMIN_IDS
from bot.utils.logger import logger

BACKUP_DIR = os.path.join(os.getcwd(), "backup")

async def create_json_backup() -> str:
    """Exports the entire database structure into a single JSON file and returns its path."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"db_backup_{timestamp}.json"
    filepath = os.path.join(BACKUP_DIR, filename)
    
    try:
        data = {
            "shows": await db.shows.find({}, {"_id": 0}).to_list(10000),
            "users": await db.users.find({}, {"_id": 0}).to_list(100000),
            "stats": await db.stats.find({}, {"_id": 0}).to_list(10000),
            "favorites": await db.favorites.find({}, {"_id": 0}).to_list(100000)
        }
        
        # Make it json serializable handling any remaining ObjectIds or datetimes
        def json_serial(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            return str(obj)

        def _dump_sync():
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, default=json_serial)
                
        await asyncio.to_thread(_dump_sync)
            
        logger.info(f"💾 Database backed up successfully: {filename}")
        return filepath
    except Exception as e:
        logger.exception(f"Error creating DB Backup: {e}")
        return None

async def trigger_backup(client=None, send_to_admin=False):
    """Creates a backup. If send_to_admin is True, sends to the primary admin via DM."""
    filepath = await create_json_backup()
    
    if filepath and send_to_admin and client and ADMIN_IDS:
        try:
            admin_id = ADMIN_IDS[0]
            await client.send_document(
                chat_id=admin_id,
                document=filepath,
                caption=f"📁 **Automated DB Backup**\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\nThis is your scheduled backup."
            )
        except Exception as e:
            logger.error(f"Failed to send backup to admin {admin_id}: {e}")

async def midnight_backup_loop(client):
    """Background task to send a backup every midnight."""
    await client.wait_for_message_on_startup() if hasattr(client, "wait_for_message_on_startup") else None
    
    while True:
        now = datetime.now()
        # Calculate time until next midnight
        next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        from datetime import timedelta
        next_midnight += timedelta(days=1)
        
        seconds_to_wait = (next_midnight - now).total_seconds()
        logger.info(f"Next midnight backup scheduled in {int(seconds_to_wait)} seconds.")
        
        await asyncio.sleep(seconds_to_wait)
        logger.info("Running midnight DB backup...")
        await trigger_backup(client, send_to_admin=True)
