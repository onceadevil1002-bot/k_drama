import logging
import asyncio
import time
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.config import ADMIN_IDS, STORAGE_CHANNEL_ID
from bot.utils.logger import logger, track_performance
from bot.database.mongo import db
from bot.utils.ui import safe_answer, notification_limiter
from pyrogram.errors import FloodWait

admin_filter = filters.user(ADMIN_IDS)

async def stats_cmd(client: Client, message: Message):
    """Detailed statistical overview matching monolith logic."""
    try:
        # Total users (from users_canonical)
        total_users = await db.userdb.count_documents({})
        
        # Private users
        private_users = await db.userdb.count_documents({
            "chats.chat_id": {"$exists": True}
        })
        
        # Groups count (aggregation)
        group_pipeline = [
            {"$unwind": "$chats"},
            {"$match": {"chats.chat_type": {"$in": ["group", "supergroup"]}}},
            {"$group": {"_id": "$chats.chat_id"}},
            {"$count": "total"}
        ]
        group_res = await db.userdb.aggregate(group_pipeline).to_list(1)
        total_groups = group_res[0]["total"] if group_res else 0
        
        # Totals
        total_favs = await db.favorites.count_documents({})
        total_reports = await db.reports.count_documents({})
        
        # Top 10 Favorites
        fav_pipeline = [
            {"$group": {"_id": {"show_name": "$show_name", "category": "$category"}, "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        top_favs = await db.favorites.aggregate(fav_pipeline).to_list(10)
        
        # Format message
        msg = "📊 **Bot Statistics**\n\n"
        msg += f"👥 **Users**\n"
        msg += f"├ Total Users: {total_users}\n"
        msg += f"├ Private Chats: {private_users}\n"
        msg += f"└ Groups: {total_groups}\n\n"
        msg += f"⭐ **Total Favorites**: {total_favs}\n"
        msg += f"⚠️ **Total Reports**: {total_reports}\n\n"
        
        if top_favs:
            msg += "🔥 **Top 10 Most Favorited:**\n"
            for idx, item in enumerate(top_favs, 1):
                name = item["_id"].get("show_name", "Unknown")
                cat = item["_id"].get("category", "N/A")
                msg += f"{idx}. {name} ({cat}) - ⭐ {item['count']}\n"
                
        await message.reply(msg)
    except Exception as e:
        logger.exception(f"stats_cmd error: {e}")
        await message.reply("❌ Error fetching statistics.")

async def broadcast_cmd(client: Client, message: Message):
    """
    Broadcast a message to all users AND groups discovered by the bot.
    Usage: /broadcast <text> OR reply to a message with /broadcast
    """
    try:
        # Check if replying to a message
        if message.reply_to_message:
            broadcast_msg = message.reply_to_message
            is_reply = True
        else:
            text_parts = message.text.split(maxsplit=1)
            if len(text_parts) < 2:
                return await message.reply(
                    "**Usage:**\n"
                    "`/broadcast <text>` - Broadcast text message\n"
                    "OR reply to **ANY** message with `/broadcast`"
                )
            broadcast_text = text_parts[1]
            is_reply = False
        
        status_msg = await message.reply("📡 Fetching recipients...")

        # 1. Fetch Users
        users_cursor = db.userdb.find({"is_bot": False}, {"user_id": 1})
        users = await users_cursor.to_list(length=None)

        # 2. Fetch Groups (aggregating from user chats)
        group_pipeline = [
            {"$unwind": "$chats"},
            {"$match": {"chats.chat_type": {"$in": ["group", "supergroup"]}}},
            {"$group": {"_id": "$chats.chat_id", "title": {"$first": "$chats.title"}}}
        ]
        group_results = await db.userdb.aggregate(group_pipeline).to_list(length=None)
        groups = [{"chat_id": g["_id"], "title": g.get("title", "Group")} for g in group_results]

        if not users and not groups:
            return await status_msg.edit_text("❌ No users or groups found.")

        # Combined list with deduplication
        seen_ids = set()
        targets = []
        for u in users:
            uid = u["user_id"]
            if uid not in seen_ids:
                targets.append(uid)
                seen_ids.add(uid)
        for g in groups:
            gid = g["chat_id"]
            if gid not in seen_ids:
                targets.append(gid)
                seen_ids.add(gid)

        await status_msg.edit_text(f"📡 Broadcasting to {len(users)} users and {len(groups)} groups...")
        
        sent = 0
        failed = 0
        
        for idx, chat_id in enumerate(targets):
            try:
                await notification_limiter.acquire()
                if is_reply:
                    await broadcast_msg.copy(chat_id)
                else:
                    await client.send_message(chat_id, broadcast_text)
                sent += 1
            except FloodWait as e:
                await asyncio.sleep(e.value)
                try:
                    if is_reply: await broadcast_msg.copy(chat_id)
                    else: await client.send_message(chat_id, broadcast_text)
                    sent += 1
                except: failed += 1
            except Exception:
                failed += 1
            
            if (idx + 1) % 20 == 0:
                try: await status_msg.edit_text(f"📡 Broadcasting... {idx + 1}/{len(targets)}\n✅ Sent: {sent}\n❌ Failed: {failed}")
                except: pass
        
        await status_msg.edit_text(
            f"✅ **Broadcast Complete!**\n\n"
            f"👤 Users: {len(users)}\n"
            f"📢 Groups: {len(groups)}\n"
            f"✅ Sent: {sent}\n"
            f"❌ Failed: {failed}"
        )
    except Exception as e:
        logger.exception(f"broadcast_cmd error: {e}")
        await message.reply("❌ Error during broadcast.")

async def selftest_cmd(client: Client, message: Message):
    """Test all critical systems modularly."""
    results = []
    
    # 1. MongoDB
    try:
        await db.command("ping")
        results.append("✅ MongoDB: Connected")
    except Exception as e:
        results.append(f"❌ MongoDB: {e}")
        
    # 2. Hash System
    try:
        h = await make_id("TestValue")
        v = await resolve_id(h)
        if v == "TestValue":
            results.append("✅ Hash System: OK")
        else:
            results.append(f"❌ Hash System: Mismatch ({v})")
    except Exception as e:
        results.append(f"❌ Hash System: {e}")
        
    # 3. Storage
    try:
        chat = await client.get_chat(STORAGE_CHANNEL_ID)
        results.append(f"✅ Storage: {chat.title}")
    except Exception as e:
        results.append(f"❌ Storage: {e}")
        
    # 4. Data
    from bot.services.shows import get_cached_data
    try:
        data = await get_cached_data()
        count = sum(len(s) for s in data.values())
        results.append(f"✅ Data: {count} shows")
    except Exception as e:
        results.append(f"❌ Data: {e}")

    await message.reply("🔧 **Self-Test Results**\n\n" + "\n".join(results))

async def sync_users_cmd(client: Client, message: Message):
    """Deep cleanup user database matching monolith logic."""
    status = await message.reply("🔄 Syncing users database...")
    try:
        removed = 0
        dups_fixed = 0
        
        # 1. Remove invalid users
        invalid_query = {
            "$or": [
                {"username": {"$in": [None, ""]}, "first_name": {"$in": [None, ""]}, "is_bot": {"$in": [None, False]}},
                {"user_id": None}
            ]
        }
        res = await db.userdb.delete_many(invalid_query)
        removed = res.deleted_count
        
        # 2. Find duplicates
        pipeline = [
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}, "docs": {"$push": "$_id"}}},
            {"$match": {"count": {"$gt": 1}}}
        ]
        duplicates = await db.userdb.aggregate(pipeline).to_list(length=None)
        for dup in duplicates:
            to_remove = dup["docs"][1:]
            for doc_id in to_remove:
                await db.userdb.delete_one({"_id": doc_id})
                dups_fixed += 1
        
        # 3. Re-index
        await db.create_indexes()
        
        final_count = await db.userdb.count_documents({})
        await status.edit_text(
            f"✅ **User Database Synced!**\n\n"
            f"🗑 Users Removed: {removed}\n"
            f"🔧 Duplicates Fixed: {dups_fixed}\n"
            f"👥 Remaining Users: {final_count}"
        )
    except Exception as e:
        logger.exception(f"sync_users_command error: {e}")
        await status.edit_text(f"❌ Sync failed: {e}")

@track_performance("set_sticker_cmd")
async def set_sticker_cmd(client: Client, message: Message):
    """Set the loading sticker by replying to a sticker."""
    if not message.reply_to_message or not message.reply_to_message.sticker:
        return await message.reply("❌ Please **reply** to a sticker with `/set_sticker`.")
    
    sticker_id = message.reply_to_message.sticker.file_id
    await db.config.update_one(
        {"_id": "settings"},
        {"$set": {"loading_sticker": sticker_id}},
        upsert=True
    )
    await message.reply("✅ Loading sticker updated successfully!")

def register_admin_handlers(app: Client):
    app.on_message(filters.command("stats") & admin_filter & filters.private)(stats_cmd)
    app.on_message(filters.command("broadcast") & admin_filter & filters.private)(broadcast_cmd)
    app.on_message(filters.command("selftest") & admin_filter & filters.private)(selftest_cmd)
    app.on_message(filters.command("sync_users") & admin_filter & filters.private)(sync_users_cmd)
    app.on_message(filters.command("set_sticker") & admin_filter & filters.private)(set_sticker_cmd)
