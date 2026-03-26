import logging
import asyncio
import os
from datetime import datetime
from bot.database.mongo import db
from bot.config import ADMIN_IDS, STORAGE_CHANNEL_ID

logger = logging.getLogger(__name__)


async def _get_full_user(client, user_id: int):
    """
    Fetch the full Pyrogram User object via get_users().
    This guarantees user.photo, dc_id, is_verified, is_scam, is_fake are populated.
    message.from_user does NOT include these fields — only get_users() does.
    """
    try:
        return await client.get_users(user_id)
    except Exception as e:
        logger.debug(f"_get_full_user failed for {user_id}: {e}")
        return None


async def _fetch_common_chats(client, user_id: int) -> list:
    """
    Fetch all groups/channels shared between the bot and this user.
    This is the maximum social graph data a bot can access — Telegram does
    not expose groups the bot is not in (hard platform limit).
    Returns list of dicts with chat_id, title, type, members_count.
    """
    try:
        common = await client.get_common_chats(user_id)
        result = []
        for chat in common:
            result.append({
                "chat_id": chat.id,
                "title": getattr(chat, "title", ""),
                "type": str(chat.type),
                "members_count": getattr(chat, "members_count", 0),
                "username": getattr(chat, "username", ""),
                "scanned_at": datetime.now()
            })
        return result
    except Exception as e:
        logger.debug(f"_fetch_common_chats failed for {user_id}: {e}")
        return []


async def upsert_user(client, user, chat=None, force_photo=False):
    """
    OPTIMIZATION: await resolves instantly — heavy work runs as background task.
    Keeps async def so all existing await upsert_user(...) calls work unchanged.
    All data extraction still happens — just after response is already sent.
    """
    if not user or user.id in ADMIN_IDS:
        return
    asyncio.create_task(_upsert_user_task(client, user, chat, force_photo))


async def _upsert_user_task(client, user, chat=None, force_photo=False):
    """
    Update user data in the database with deep historical tracking.
    Phase 1 additions:
    - dc_id, is_verified, is_scam, is_fake
    - interaction_count increment
    - Fixed photo extraction using get_users() for full object
    - Profile pic history actually written (was pass before)
    - common_chats via get_common_chats()
    """
    try:
        now = datetime.now()
        user_id = user.id

        # 1. Fetch current document
        old_doc = await db.userdb.find_one({"user_id": user_id}) or {}

        # 2. Get full user object — but only once per hour.
        #    get_users() adds ~1300ms per call. Calling it on every message kills performance.
        #    We use last_full_fetch timestamp to throttle — 1hr between full fetches.
        #    On first interaction or after 1hr, fetch full data. Otherwise use partial.
        last_fetch = old_doc.get("last_full_fetch")
        needs_full_fetch = (
            not last_fetch or
            (now - last_fetch).total_seconds() > 3600  # 1 hour
        )
        if needs_full_fetch:
            full_user = await _get_full_user(client, user_id) or user
            # Mark fetch time so we don't call again for 1hr
            # Written into set_ops below
        else:
            full_user = user  # Use partial object — still has name/username

        first_name  = full_user.first_name or ""
        last_name   = full_user.last_name or ""
        full_name   = f"{first_name} {last_name}".strip() or "Unknown"
        username    = full_user.username or ""
        lang_code   = full_user.language_code or "Unknown"
        is_premium  = getattr(full_user, "is_premium", False)
        is_bot      = full_user.is_bot
        is_verified = getattr(full_user, "is_verified", False)
        is_scam     = getattr(full_user, "is_scam", False)
        is_fake     = getattr(full_user, "is_fake", False)
        dc_id       = getattr(full_user, "dc_id", None)

        # 3. Photo extraction
        # big_file_id from user.photo is a CHAT_PHOTO type — send_photo rejects it.
        # We must use get_user_profile_photos() to get actual PHOTO type file_ids.
        # Only do this during full fetch (once per hour) to avoid API spam.
        photo_file_id  = old_doc.get("profile_pic")
        old_unique_id  = old_doc.get("photo_unique_id")
        new_unique_id  = None

        if needs_full_fetch and getattr(full_user, "photo", None):
            new_unique_id = full_user.photo.small_photo_unique_id
            photo_changed = new_unique_id and new_unique_id != old_unique_id

            if photo_changed or not photo_file_id:
                # Download current photo and re-upload to storage channel
                # This gives us a proper PHOTO type file_id (not CHAT_PHOTO)
                tmp_path = None
                old_tmp = None
                try:
                    tmp_path = await client.download_media(full_user.photo.big_file_id)
                    if tmp_path:
                        if photo_changed and old_doc.get("profile_pic") and force_photo:
                            # Also persist the old photo before overwriting
                            try:
                                old_tmp = await client.download_media(old_doc["profile_pic"])
                                if old_tmp:
                                    old_sent = await client.send_photo(
                                        STORAGE_CHANNEL_ID, old_tmp,
                                        caption=f"📸 Past Profile Pic\nID: {user_id} @{username}"
                                    )
                                    if old_sent and old_sent.photo:
                                        await db.userdb.update_one(
                                            {"user_id": user_id},
                                            {"$push": {"profile_pic_history": {
                                                "file_id": old_sent.photo.file_id,
                                                "changed_at": now
                                            }}}
                                        )
                            except Exception as e:
                                logger.debug(f"Old photo persist error for {user_id}: {e}")
                            finally:
                                if old_tmp and os.path.exists(old_tmp):
                                    try:
                                        os.remove(old_tmp)
                                    except Exception as rm_err:
                                        logger.warning(f"Failed to delete old_tmp {old_tmp}: {rm_err}")

                        # Upload current photo to storage channel for permanent PHOTO file_id
                        curr_sent = await client.send_photo(
                            STORAGE_CHANNEL_ID, tmp_path,
                            caption=f"📸 Current Profile Pic\nID: {user_id} @{username}"
                        )
                        if curr_sent and curr_sent.photo:
                            photo_file_id = curr_sent.photo.file_id  # Proper PHOTO type
                except Exception as e:
                    logger.debug(f"Photo download/upload error for {user_id}: {e}")
                finally:
                    # GUARANTEED cleanup — runs even if send_photo raises
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception as rm_err:
                            logger.warning(f"Failed to delete tmp_path {tmp_path}: {rm_err}")


        # 4. Build history records on change
        history_push = {}

        def check_change(field, new_val):
            old_val = old_doc.get(field)
            if old_val != new_val and old_doc:
                history_push[f"{field}_history"] = {
                    "old": old_val,
                    "new": new_val,
                    "changed_at": now
                }

        check_change("username",   username)
        check_change("first_name", first_name)
        check_change("last_name",  last_name)
        check_change("full_name",  full_name)

        if photo_file_id and old_doc.get("profile_pic") != photo_file_id:
            history_push["profile_pic_history"] = {
                "file_id": photo_file_id,
                "changed_at": now,
                "source": "current"
            }

        # 5. Primary set operations
        set_ops = {
            "user_id":      user_id,
            "username":     username,
            "first_name":   first_name,
            "last_name":    last_name,
            "full_name":    full_name,
            "language_code": lang_code,
            "is_premium":   is_premium,
            "is_bot":       is_bot,
            "is_verified":  is_verified,
            "is_scam":      is_scam,
            "is_fake":      is_fake,
            "last_interaction": now
        }
        if dc_id is not None:
            set_ops["dc_id"] = dc_id
        if photo_file_id:
            set_ops["profile_pic"] = photo_file_id
        if new_unique_id:
            set_ops["photo_unique_id"] = new_unique_id
        if needs_full_fetch:
            set_ops["last_full_fetch"] = now

        update_ops = {
            "$set": set_ops,
            "$inc": {"interaction_count": 1},  # FIX: was never incremented
            "$setOnInsert": {
                "created_at": now,
                "allow_global_notifications": False
            }
        }

        # Rolling interaction timestamps — capped at last 200
        update_ops["$push"] = {
            "interaction_timestamps": {
                "$each": [now],
                "$slice": -200
            }
        }

        if history_push:
            for k, v in history_push.items():
                update_ops["$push"][k] = v

        # 6. Chat context tracking (groups the bot IS in)
        from pyrogram.enums import ChatType
        if chat and chat.type != ChatType.PRIVATE:
            chat_type  = str(chat.type)
            chat_title = getattr(chat, "title", None) or full_name
            existing_chats = old_doc.get("chats", [])
            chat_exists = any(c.get("chat_id") == chat.id for c in existing_chats)

            if chat_exists:
                await db.userdb.update_one(
                    {"user_id": user_id, "chats.chat_id": chat.id},
                    {"$set": {
                        "chats.$.last_seen": now,
                        "chats.$.type":      chat_type,
                        "chats.$.title":     chat_title
                    }}
                )
            else:
                update_ops["$push"]["chats"] = {
                    "chat_id":    chat.id,
                    "type":       chat_type,
                    "title":      chat_title,
                    "first_seen": now,
                    "last_seen":  now
                }

        await db.userdb.update_one(
            {"user_id": user_id},
            update_ops,
            upsert=True
        )

        # 7. Common chats — fetch asynchronously, don't block the main upsert
        #    Only refresh if not fetched in the last 6 hours to avoid API spam
        last_common = old_doc.get("common_chats_scanned_at")
        should_refresh = (
            not last_common or
            (now - last_common).total_seconds() > 21600  # 6 hours
        )
        if should_refresh:
            asyncio.create_task(_update_common_chats(client, user_id, now))

    except Exception as e:
        logger.debug(f"upsert_user error for {getattr(user, 'id', '?')}: {e}")


async def _update_common_chats(client, user_id: int, now: datetime):
    """Background task — fetch and store common chats without blocking upsert."""
    try:
        common = await _fetch_common_chats(client, user_id)
        await db.userdb.update_one(
            {"user_id": user_id},
            {"$set": {
                "common_chats": common,
                "common_chats_scanned_at": now
            }}
        )
        if common:
            logger.debug(f"Updated {len(common)} common chats for user {user_id}")
    except Exception as e:
        logger.debug(f"_update_common_chats error for {user_id}: {e}")


async def refresh_all_profiles(client):
    """Midnight refresh — force photo re-fetch for all users."""
    logger.info("Starting global profile refresh...")
    cursor = db.userdb.find({}, {"user_id": 1})
    count  = 0
    async for doc in cursor:
        try:
            user_id   = doc["user_id"]
            full_user = await client.get_users(user_id)
            await upsert_user(client, full_user, force_photo=True)
            count += 1
            if count % 100 == 0:
                logger.info(f"Refreshed {count} profiles...")
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.debug(f"Refresh error for {doc.get('user_id')}: {e}")
    logger.info(f"Completed refresh of {count} user profiles.")


async def get_watch_history(user_id):
    user = await db.userdb.find_one({"user_id": user_id}, {"watch_history": 1})
    return user.get("watch_history", []) if user else []


async def add_to_watch_history(user_id, show_name):
    await db.userdb.update_one(
        {"user_id": user_id},
        {
            "$push": {
                "watch_history": {
                    "$each":  [show_name],
                    "$slice": -10
                }
            }
        },
        upsert=True
    )
