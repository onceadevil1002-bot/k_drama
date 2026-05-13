import logging
import asyncio
import time
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait

from bot.config import ADMIN_IDS, STORAGE_CHANNEL_ID
from bot.utils.logger import logger, track_performance
from bot.database.mongo import db
from bot.utils.ui import safe_answer, notification_limiter
from bot.utils.ids import make_id, resolve_id

# Build admin filter excluding invalid IDs (0) and deduplicating
_valid_admin_ids = list(set(uid for uid in ADMIN_IDS if uid != 0))
admin_filter = filters.user(_valid_admin_ids)

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
        
        # Total chow count (sum of all interaction counts)
        user_pipeline = [
            {"$group": {"_id": None, "total_interactions": {"$sum": "$interaction_count"}}}
        ]
        chow_res = await db.userdb.aggregate(user_pipeline).to_list(1)
        total_chow = chow_res[0]["total_interactions"] if chow_res else 0
        
        # Totals
        total_favs = await db.favorites.count_documents({})
        total_reports = await db.reports.count_documents({})
        unresolved_reports = await db.reports.count_documents({"status": {"$ne": "resolved", "$ne": "rejected"}})
        
        # Get 10 latest unresolved reports
        unresolved_cursor = db.reports.find(
            {"status": {"$nin": ["resolved", "rejected"]}}
        ).sort("created_at", -1).limit(10)
        unresolved_list = await unresolved_cursor.to_list(10)
        
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
        msg += f"⚠️ **Total Reports**: {total_reports} ({unresolved_reports} unresolved)\n"
        msg += f"📺 **Total Chow**: {total_chow}\n\n"
        
        # Add top favorites
        if top_favs:
            msg += "🔥 **Top 10 Most Favorited:**\n"
            for idx, item in enumerate(top_favs, 1):
                name = item["_id"].get("show_name", "Unknown")
                cat = item["_id"].get("category", "N/A")
                msg += f"{idx}. {name} ({cat}) - ⭐ {item['count']}\n"
        
        # Add latest unresolved reports
        if unresolved_list:
            msg += f"\n🚩 **Latest {len(unresolved_list)} Unresolved Reports:**\n"
            buttons = []
            for idx, report in enumerate(unresolved_list[:10], 1):
                report_id = str(report.get("_id"))
                user_info = report.get("user", {})
                report_text = str(report.get("report", "N/A"))[:40]  # Truncate to 40 chars
                status = report.get("status", "pending")
                
                msg += f"{idx}. **[{status.upper()}]** {report_text}... (User: {user_info.get('full_name', 'Unknown')})\n"
                
                # Add inline button for this report
                buttons.append([InlineKeyboardButton(
                    f"Report {idx}: {report_text[:20]}...",
                    callback_data=f"view_report|{report_id}"
                )])
            
            # Send main stats message first
            await message.reply(msg)
            
            # Send reports list with buttons
            reports_msg = f"📋 **Access Unresolved Reports** ({len(unresolved_list)} total)\n\n"
            reports_msg += "Click on any report to view full details:"
            await message.reply(
                reports_msg,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
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
                except Exception as retry_err:
                    logger.debug(f"broadcast retry failed for {chat_id}: {retry_err}")
                    failed += 1
            except Exception as send_err:
                logger.debug(f"broadcast failed for {chat_id}: {send_err}")
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

async def banned_list_cmd(client: Client, message: Message):
    """List all currently banned users."""
    try:
        banned = await db.banned_users.find({"active": True}).sort("banned_at", -1).to_list(50)
        if not banned:
            return await message.reply("✅ No banned users.")
        text = f"⛔ **Banned Users ({len(banned)})**\n\n"
        for u in banned:
            text += (
                f"👤 {u.get('full_name', 'Unknown')} (@{u.get('username', 'none')})\n"
                f"🆔 `{u['user_id']}`\n"
                f"📅 Banned: {u.get('banned_at', 'N/A')}\n"
                f"🔁 Leave count: {u.get('leave_count', 0)}\n\n"
            )
        await message.reply(text)
    except Exception as e:
        logger.exception(f"banned_list_cmd error: {e}")
        await message.reply("❌ Error fetching banned list.")


async def unban_cmd(client: Client, message: Message):
    """Unban a user by ID, username, or name. Usage: /unban <user_id|@username|name>"""
    if len(message.command) < 2:
        return await message.reply("Usage: `/unban <user_id | @username | name>`")

    query = message.text.split(" ", 1)[1].strip().replace("@", "")
    from bot.services.verification import unban_user

    # Build search filter
    filter_q = {"active": True, "$or": [
        {"username": {"$regex": f"^{query}$", "$options": "i"}},
        {"full_name": {"$regex": query, "$options": "i"}}
    ]}
    if query.isdigit():
        filter_q["$or"].append({"user_id": int(query)})

    try:
        banned_doc = await db.banned_users.find_one(filter_q)
        if not banned_doc:
            return await message.reply("❌ No active ban found for that user.")

        user_id = banned_doc["user_id"]
        await unban_user(user_id)

        # Notify the user
        try:
            await client.send_message(
                user_id,
                "✅ **Your ban has been lifted.**\n\nYou can now use the bot again. "
                "Please make sure to stay in the required channels."
            )
        except Exception:
            pass

        await message.reply(
            f"✅ Unbanned **{banned_doc.get('full_name', 'Unknown')}** (`{user_id}`)"
        )
    except Exception as e:
        logger.exception(f"unban_cmd error: {e}")
        await message.reply("❌ Error processing unban.")


async def view_report_cb(client: Client, callback_query: CallbackQuery):
    """Handle viewing a specific report from stats."""
    try:
        from bson import ObjectId
        report_id = callback_query.data.split("|")[1]
        
        # Convert string to ObjectId if necessary
        if isinstance(report_id, str):
            report_id = ObjectId(report_id)
        
        report = await db.reports.find_one({"_id": report_id})
        if not report:
            return await safe_answer(callback_query, "❌ Report not found.", show_alert=True)
        
        user_info = report.get("user", {})
        report_text = report.get("report", "N/A")
        status = report.get("status", "pending")
        created_at = report.get("created_at", "N/A")
        
        msg = (
            f"🚩 **Report Details**\n\n"
            f"👤 **From:** {user_info.get('full_name', 'Unknown')} (`{user_info.get('user_id')}`)\n"
            f"📝 **Report:** {report_text}\n"
            f"🚦 **Status:** {status.upper()}\n"
            f"⏰ **Created:** {created_at}\n"
            f"🆔 ID: `{report_id}`"
        )
        
        await callback_query.message.reply(msg)
        await safe_answer(callback_query)
    except Exception as e:
        logger.exception(f"view_report_cb error: {e}")
        await safe_answer(callback_query, "❌ Error loading report.", show_alert=True)


def register_admin_handlers(app: Client):
    app.on_message(filters.command("stats") & admin_filter & filters.private)(stats_cmd)
    app.on_message(filters.command("broadcast") & admin_filter & filters.private)(broadcast_cmd)
    app.on_message(filters.command("selftest") & admin_filter & filters.private)(selftest_cmd)
    app.on_message(filters.command("sync_users") & admin_filter & filters.private)(sync_users_cmd)
    app.on_message(filters.command("set_sticker") & admin_filter & filters.private)(set_sticker_cmd)
    app.on_message(filters.command("banned_list") & admin_filter & filters.private)(banned_list_cmd)
    app.on_message(filters.command("unban") & admin_filter & filters.private)(unban_cmd)
    app.on_callback_query(filters.regex(r"^view_report\|") & admin_filter)(view_report_cb)
