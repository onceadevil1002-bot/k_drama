import logging
import os
from datetime import datetime
from bot.database.mongo import db
from bot.config import ADMIN_IDS, STORAGE_CHANNEL_ID

logger = logging.getLogger(__name__)

async def upsert_user(client, user, chat=None, force_photo=False):
    """
    Update user data in the database with deep historical tracking.
    Captures past usernames, names, profile photos, and language codes.
    Admins are strictly isolated from this tracking.
    """
    if not user or user.id in ADMIN_IDS:
        return
    
    try:
        now = datetime.now()
        
        # 1. Fetch current (old) user document to detect changes
        old_doc = await db.userdb.find_one({"user_id": user.id}) or {}
        
        # 2. Extract max data
        first_name = user.first_name or ""
        last_name = user.last_name or ""
        full_name = f"{first_name} {last_name}".strip() or "Unknown"
        username = user.username or ""
        lang_code = user.language_code or "Unknown"
        is_premium = getattr(user, 'is_premium', False)
        is_bot = user.is_bot
        
        photo_file_id = old_doc.get("profile_pic")
        old_unique_id = old_doc.get("photo_unique_id")
        
        # Photo Detection: Use user.photo (instant, no API call)
        new_unique_id = None
        new_big_file_id = None
        if getattr(user, "photo", None):
            new_unique_id = user.photo.small_photo_unique_id
            new_big_file_id = user.photo.big_file_id
        
        photo_changed = new_unique_id and new_unique_id != old_unique_id
        
        # If user has a photo, always store the current big_file_id
        if new_big_file_id:
            # If photo changed and we had an old photo, persist old one to storage channel
            if photo_changed and old_doc.get("profile_pic") and force_photo:
                try:
                    old_file = await client.download_media(old_doc["profile_pic"])
                    if old_file:
                        try:
                            await client.get_chat(STORAGE_CHANNEL_ID)
                        except Exception:
                            pass
                        sent = await client.send_photo(
                            STORAGE_CHANNEL_ID, old_file,
                            caption=f"📸 **Past Profile Pic**\nID: `{user.id}`\nUsername: @{username}"
                        )
                        if sent and sent.photo:
                            # Store the permanent file_id in history
                            pass  # History is handled below
                        if os.path.exists(old_file):
                            os.remove(old_file)
                except Exception as e:
                    logger.debug(f"Past photo persist error for {user.id}: {e}")
            
            # Current photo = direct big_file_id (always valid while photo exists)
            photo_file_id = new_big_file_id

        # 3. Build History Records if Changed
        history_updates = {"$push": {}}
        
        def check_change(field, new_val):
            old_val = old_doc.get(field, None)
            if old_val != new_val and old_doc:  # Only log history if they aren't brand new
                history_updates["$push"][f"{field}_history"] = {
                    "old": old_val,
                    "new": new_val,
                    "changed_at": now
                }

        check_change("username", username)
        check_change("first_name", first_name)
        check_change("last_name", last_name)
        check_change("full_name", full_name)
        
        if photo_file_id and old_doc.get("profile_pic") != photo_file_id:
            history_updates["$push"]["profile_pic_history"] = {
                "file_id": photo_file_id,
                "changed_at": now
            }

        # 4. Build primary update operations
        set_ops = {
            "user_id": user.id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "language_code": lang_code,
            "is_premium": is_premium,
            "is_bot": is_bot,
            "last_interaction": now
        }
        
        if photo_file_id:
            set_ops["profile_pic"] = photo_file_id
        if new_unique_id:
            set_ops["photo_unique_id"] = new_unique_id

        update_ops = {
            "$set": set_ops,
            "$setOnInsert": {
                "created_at": now,
                "allow_global_notifications": True
            }
        }
        
        if history_updates["$push"]:
            update_ops["$push"] = history_updates["$push"]

        # 5. Chat Context Tracking (Group vs Bot)
        if chat and str(chat.type) != "ChatType.PRIVATE":
            chat_type = str(chat.type)
            chat_title = getattr(chat, 'title', None) or full_name
            existing_chats = old_doc.get("chats", [])
            chat_exists = any(c.get("chat_id") == chat.id for c in existing_chats)
            
            if chat_exists:
                # Update the specific chat entry in the DB independently
                await db.userdb.update_one(
                    {"user_id": user.id, "chats.chat_id": chat.id},
                    {"$set": {
                        "chats.$.last_seen": now, 
                        "chats.$.type": chat_type,
                        "chats.$.title": chat_title
                    }}
                )
            else:
                # Append brand new chat object using push to prevent duplicates
                if "$push" not in update_ops: update_ops["$push"] = {}
                update_ops["$push"]["chats"] = {
                    "chat_id": chat.id,
                    "type": chat_type,
                    "title": chat_title,
                    "first_seen": now,
                    "last_seen": now
                }
            
        await db.userdb.update_one(
            {"user_id": user.id},
            update_ops,
            upsert=True
        )
    except Exception as e:
        logger.debug(f"upsert_user error: {e}")

async def refresh_all_profiles(client):
    """
    Background task to refresh all user profiles (additive).
    This keeps historical data up to date.
    """
    logger.info("Starting global profile refresh...")
    cursor = db.userdb.find({}, {"user_id": 1})
    count = 0
    async for doc in cursor:
        try:
            user_id = doc["user_id"]
            # Get user object from Telegram
            user = await client.get_users(user_id)
            await upsert_user(client, user, force_photo=True)
            count += 1
            if count % 100 == 0:
                logger.info(f"Refreshed {count} profiles...")
            await asyncio.sleep(0.5) # Avoid flood
        except Exception as e:
            logger.debug(f"Refresh error for {doc.get('user_id')}: {e}")
            continue
    logger.info(f"Completed refresh of {count} user profiles.")

async def get_watch_history(user_id):
    """Retrieve the last 10 watched shows for a user."""
    user = await db.userdb.find_one({"user_id": user_id}, {"watch_history": 1})
    return user.get("watch_history", []) if user else []

async def add_to_watch_history(user_id, show_name):
    """Add a show to watch history, keeping only the last 10."""
    await db.userdb.update_one(
        {"user_id": user_id},
        {
            "$push": {
                "watch_history": {
                    "$each": [show_name],
                    "$slice": -10
                }
            }
        },
        upsert=True
    )
