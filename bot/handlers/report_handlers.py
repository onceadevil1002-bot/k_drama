import logging
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from bot.utils.ui import safe_answer
from bot.utils.ids import resolve_id
from bot.services.reports import create_report, update_report_status, get_report, delete_report
from bot.config import ADMIN_IDS

logger = logging.getLogger(__name__)

# State for users waiting to type a report
report_waiting = {} # user_id -> context

# State for admins waiting to send message to reporter
report_reply_waiting = {} # user_id -> {report_id, report_target_user_id}

async def report_command(client: Client, message: Message):
    """Initiate a report for a specific show/category."""
    user_id = message.from_user.id
    report_waiting[user_id] = {"mode": "global"}
    await message.reply(
        "⚠️ **Report Issue**\n\nPlease describe the issue you're facing.\n"
        "Include the show name and episode if possible.\n\n"
        "Type your message below 👇"
    )

async def report_main_cb(client: Client, callback_query: CallbackQuery):
    """Handle the main menu report button."""
    user_id = callback_query.from_user.id
    report_waiting[user_id] = {"mode": "global"}
    await callback_query.message.reply(
        "⚠️ **Report Issue**\n\nPlease describe the issue you're facing.\n"
        "Include the show name and episode if possible.\n\n"
        "Type your message below 👇"
    )
    await safe_answer(callback_query)


async def report_callback_handler(client: Client, callback_query: CallbackQuery):
    """Handle the 'Report' button from show menu."""
    parts = callback_query.data.split("|")
    cat_id = parts[1]
    show_id = parts[2]
    
    category = await resolve_id(cat_id)
    show_name = await resolve_id(show_id)
    
    user_id = callback_query.from_user.id
    report_waiting[user_id] = {
        "mode": "specific",
        "category": category,
        "show_name": show_name
    }
    
    await callback_query.message.reply(
        f"⚠️ **Reporting: {show_name}**\n\nPlease describe what's wrong (e.g., link broken, wrong episode).\n\n"
        "Type your message below 👇"
    )
    await safe_answer(callback_query)

async def handle_report_text(client: Client, message: Message):
    """Handle the actual report description text."""
    user_id = message.from_user.id
    if user_id not in report_waiting:
        return
        
    context = report_waiting.pop(user_id)
    text = message.text.strip()
    
    if not text:
        return await message.reply("❌ Report cancelled (empty text).")
        
    report_data = {
        "issue": text,
        "category": context.get("category", ""),
        "show_name": context.get("show_name", "")
    }
    
    report_id = await create_report(message.from_user, message.chat, report_data)
    if report_id:
        try:
            full_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()
        except:
            full_name = message.from_user.first_name or "Unknown"
            
        await message.reply("✅ **Report submitted!**\nAdmin will review it soon.")
        
        # Notify Admin (matching Monolith moderation toolbar)
        for admin in ADMIN_IDS:
            try:
                caption = (
                    f"🚩 **NEW USER REPORT**\n\n"
                    f"👤 **From:** {full_name} (`{user_id}`)\n"
                    f"🎬 **Show:** {report_data['show_name'] or 'N/A'}\n"
                    f"📂 **Category:** {report_data['category'] or 'N/A'}\n"
                    f"💬 **Issue:** {text}\n\n"
                    f"🆔 ID: `{report_id}`"
                )
                
                buttons = [
                    [
                        InlineKeyboardButton("🧠 Deep Analysis", callback_data=f"max_profile_{user_id}"),
                        InlineKeyboardButton("🗂 History", callback_data=f"user_history_{user_id}")
                    ],
                    [
                        InlineKeyboardButton("⚙️ Processing", callback_data=f"report_status|{report_id}|processing"),
                        InlineKeyboardButton("✅ Resolve", callback_data=f"report_status|{report_id}|resolved"),
                        InlineKeyboardButton("❌ Reject", callback_data=f"report_status|{report_id}|rejected")
                    ],
                    [
                        InlineKeyboardButton("💬 Send Msg", callback_data=f"report_send_msg|{report_id}|{user_id}"),
                    ],
                    [
                        InlineKeyboardButton("👤 More from User", callback_data=f"report_view_user|{user_id}"),
                        InlineKeyboardButton("🔍 Same Show", callback_data=f"report_search_show|{report_data['show_name']}")
                    ]
                ]
                
                await client.send_message(
                    admin,
                    caption,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                logger.info(f"Report {report_id} notification sent to admin {admin}")
            except Exception as notify_err:
                # Log exact error so we can diagnose why admin isn't receiving notifications
                logger.error(
                    f"Failed to notify admin {admin} of report {report_id}: "
                    f"{type(notify_err).__name__}: {notify_err}"
                )
    else:
        await message.reply("❌ Error submitting report.")

async def report_status_handler(client: Client, callback_query: CallbackQuery):
    """Handle report status changes by admin."""
    parts = callback_query.data.split("|")
    report_id = parts[1]
    new_status = parts[2]
    
    success = await update_report_status(report_id, new_status)
    if success:
        report = await get_report(report_id)
        if report:
            user_target_id = report["user"]["user_id"]
            if new_status == "resolved":
                msg = "🟢 **Your report has been resolved!**\nThank you for helping us improve."
            elif new_status == "processing":
                msg = "🟡 **Your report is now under processing.**\nOur team is working on it."
            elif new_status == "rejected":
                msg = "🔴 **Your report was reviewed and rejected.**\nPlease ensure details are accurate."
            else:
                msg = f"⚪ Your report status changed to: **{new_status}**"
            
            try:
                await client.send_message(user_target_id, msg)
            except Exception as e:
                logger.warning(f"Could not notify user {user_target_id} of report status update: {e}")
        
        # Only delete the report from admin chat if resolved or rejected
        if new_status in ["resolved", "rejected"]:
            await callback_query.message.delete()
            await safe_answer(callback_query, f"✅ Report {new_status.capitalize()} and deleted")
        else:
            # For processing, just show confirmation without deleting
            await safe_answer(callback_query, f"✅ Report marked as {new_status.capitalize()}")
    else:
        await safe_answer(callback_query, "Failed to update report.", show_alert=True)

async def report_send_msg_handler(client: Client, callback_query: CallbackQuery):
    """Handle 'Send Message' button - initiates message sending flow."""
    parts = callback_query.data.split("|")
    report_id = parts[1]
    target_user_id = int(parts[2])
    admin_id = callback_query.from_user.id
    
    # Store the context for this admin
    report_reply_waiting[admin_id] = {
        "report_id": report_id,
        "target_user_id": target_user_id
    }
    
    await callback_query.message.reply(
        f"📝 **Sending message to reporter** (User ID: {target_user_id})\n\n"
        f"Please type your message below 👇\n"
        f"This message will be sent directly to the user who filed the report."
    )
    await safe_answer(callback_query)

async def handle_report_reply_text(client: Client, message: Message):
    """Handle the actual message text sent by admin to reporter."""
    admin_id = message.from_user.id
    if admin_id not in report_reply_waiting:
        return
    
    context = report_reply_waiting.pop(admin_id)
    reply_text = message.text.strip()
    
    if not reply_text:
        return await message.reply("❌ Message cancelled (empty text).")
    
    target_user_id = context["target_user_id"]
    
    try:
        # Send message to the reporter
        response_msg = (
            f"📩 **Response from Admin**\n\n"
            f"{reply_text}"
        )
        await client.send_message(target_user_id, response_msg)
        await message.reply(f"✅ **Message sent to user {target_user_id}**")
        logger.info(f"Admin {admin_id} sent response to report user {target_user_id}")
    except Exception as e:
        await message.reply(f"❌ Failed to send message: {e}")
        logger.error(f"Failed to send report response to user {target_user_id}: {e}")

def register_report_handlers(app: Client):
    app.on_message(filters.command("report") & filters.private)(report_command)
    app.on_callback_query(filters.regex(r"^report$"))(report_main_cb)
    app.on_callback_query(filters.regex(r"^report\|"))(report_callback_handler)
    app.on_callback_query(filters.regex(r"^report_status\|"))(report_status_handler)
    app.on_callback_query(filters.regex(r"^report_send_msg\|"))(report_send_msg_handler)
    # CRITICAL: Must be group=1, NOT group=0.
    # handle_import_receive in admin_data_entry.py is registered at group=0 for ALL private
    # text messages. In Pyrogram, only the first matching handler per group fires.
    # By using group=1, this handler always runs after group=0, so user reports are
    # never silently swallowed by the import handler.
    app.on_message(
        filters.text & filters.private & ~filters.regex(r"^/"),
        group=1
    )(handle_report_text)
    app.on_message(
        filters.text & filters.private & ~filters.regex(r"^/"),
        group=2
    )(handle_report_reply_text)
