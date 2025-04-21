import os
import re
import json
import asyncio
from urllib.parse import unquote
from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Message,
    CallbackQuery,
)

def slugify_show_name(name: str) -> str:
    return re.sub(r'\W+', '', name.lower().replace(' ', '_'))


# === Load Environment Variables ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
MAIN_CHANNEL_LINK = os.getenv("MAIN_CHANNEL_LINK")
STORAGE_CHANNEL_id = os.getenv("STORAGE_CHANNEL_id")

app = Client("kdrama_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# === JSON Database ===
DATA_FILE = "data.json"
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump({}, f)

def load_data():
    with open(DATA_FILE) as f:
        data = json.load(f)
    return {slugify_show_name(name): value for name, value in data.items()}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# === Global State ===
upload_state = {}  # user_id: {"show": show_name, "season": season_number}

# === Commands ===

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram import filters
import json

# Load data from JSON
def load_data():
    with open("data.json", "r", encoding="utf-8") as f:
        return json.load(f)

# Constants
MAIN_CHANNEL_LINK = "https://t.me/KDRAMAAVIL"  # Replace with your main channel

@app.on_message(filters.command("start"))
async def start(client, message):
    args = message.text.split(maxsplit=1)
    data = load_data()

    # If no slug or show name is passed, show the main interface with show list
    if len(args) == 1:
        user_buttons = [
            [InlineKeyboardButton("📣 Join Main Channel", url=MAIN_CHANNEL_LINK)],
            [InlineKeyboardButton("❗ Report For Show/Episodes", callback_data="report")],
        ]
        for show_name in data.keys():
            user_buttons.append([InlineKeyboardButton(show_name, callback_data=f"show_{show_name}")])
        await message.reply("🎬 **Welcome to K-Drama Bot!**\nChoose a show below:", reply_markup=InlineKeyboardMarkup(user_buttons))
        return

    # Handle both slugs and direct show names
    param = args[1].strip()

    # Case 1: Slug-based redirection (e.g., /start sweet_home)
    if param.replace("_", " ") not in data:  # Check if it's a slug
        show_name_requested = param.replace("_", " ").strip()
        show_data = data.get(show_name_requested)

        if not show_data:
            # Highlight the corresponding show button on the main interface
            user_buttons = [
                [InlineKeyboardButton("📣 Join Main Channel", url=MAIN_CHANNEL_LINK)],
                [InlineKeyboardButton("❗ Report For Show/Episodes", callback_data="report")],
            ]
            for name in data.keys():
                if name.lower().replace(" ", "_") == param:
                    user_buttons.append([InlineKeyboardButton(f"🌟 {name}", callback_data=f"show_{name}")])
                else:
                    user_buttons.append([InlineKeyboardButton(name, callback_data=f"show_{name}")])

            await message.reply(
                f"🎬 **Welcome to K-Drama Bot!**\nHighlighted: **{param.replace('_', ' ')}**",
                reply_markup=InlineKeyboardMarkup(user_buttons)
            )
            return

    # Case 2: Direct show name passed (e.g., /start Sweet_Home)
    show_name_requested = param.replace("_", " ").strip()
    show_data = data.get(show_name_requested)

    if not show_data:
        await message.reply("❌ Show not found.")
        return

    # Redirect user to the main show button
    show_buttons = [
        [InlineKeyboardButton("📣 Join Main Channel", url=MAIN_CHANNEL_LINK)],
        [InlineKeyboardButton(show_name_requested, callback_data=f"show_{show_name_requested}")]
    ]
    await message.reply(
        f"📺 Show: **{show_name_requested}**\nTap below to open the show.",
        reply_markup=InlineKeyboardMarkup(show_buttons)
    )
@app.on_message(filters.command("add") & filters.user(ADMIN_ID))
async def add_show(client, message):
    parts = message.text.split(" ", 1)
    if len(parts) < 2:
        await message.reply("❗ Usage: /add Show Name or /add Show Name SeasonNumber")
        return

    args = parts[1].strip()
    if not args:
        await message.reply("❗ Invalid input.")
        return

    data = load_data()
    arg_parts = args.rsplit(" ", 1)  # split only at last space

    if arg_parts[-1].isdigit():  # It's a season add
        show_name = arg_parts[0].strip()
        season_number = arg_parts[1].strip()

        if show_name not in data:
            await message.reply("❗ Show not found. Use /add Show Name first.")
            return
        if season_number in data[show_name]:
            await message.reply("⚠️ Season already exists!")
            return

        data[show_name][season_number] = []
        save_data(data)
        await message.reply(f"✅ Added season: {season_number} under *{show_name}*")

    else:  # It's a new show
        show_name = args
        if show_name in data:
            await message.reply("⚠️ Show already exists!")
            return
        data[show_name] = {}
        save_data(data)
        await message.reply(f"✅ Added show: *{show_name}*")


# ===== FIXED /upload COMMAND =====@app.on_message(filters.command("upload") & filters.user(ADMIN_ID))
@app.on_message(filters.command("upload") & filters.user(ADMIN_ID))
async def upload(client, message: Message):
    parts = message.text.split(" ", 1)
    if len(parts) < 2:
        return await message.reply("❗ Usage: /upload Show Name or /upload Show Name > season season1")

    args = parts[1].strip()
    if not args:
        return await message.reply("❗ Invalid show name or season.")

    # Check for new format: Show Name > season season1
    if ">" in args and "season" in args.lower():
        try:
            show_part, season_part = args.split(">", 1)
            show_part = show_part.strip()
            season_number = season_part.strip().lower().replace("season", "").strip()
        except ValueError:
            return await message.reply("❗ Invalid format. Use /upload ShowName > season season1")

        data = load_data()
        matched_show = None
        for show in data:
            if show == show_part:
                matched_show = show
                break

        if not matched_show:
            return await message.reply("❗ Show not found. Use /add Show Name first.")

        if season_number not in data[matched_show]:
            return await message.reply("❗ Season not found. Use /add Show Name SeasonNumber first.")

        upload_state[message.from_user.id] = {"show": matched_show, "season": season_number}
        return await message.reply(f"📤 Send videos now for *{matched_show}* Season *{season_number}*", quote=True)

    # Legacy format: /upload ShowName or /upload ShowName season1
    data = load_data()
    show_name = None
    season_number = None

    for name in data.keys():
        if args.startswith(name):
            remainder = args[len(name):].strip()
            show_name = name
            if remainder.lower().startswith("season"):
                season_number = remainder.lower().replace("season", "").strip()
            elif remainder.isdigit():
                season_number = remainder
            break

    if not show_name:
        return await message.reply("❗ Show not found. Use /add Show Name first.")

    if season_number:
        if season_number not in data[show_name]:
            return await message.reply("❗ Season not found. Use /add Show Name SeasonNumber first.")
        upload_state[message.from_user.id] = {"show": show_name, "season": season_number}
        return await message.reply(f"📤 Send videos now for *{show_name}* Season *{season_number}*", quote=True)
    else:
        upload_state[message.from_user.id] = {"show": show_name, "season": None}
        return await message.reply(f"📤 Send videos now for *{show_name}*", quote=True)


# ===== FIXED VIDEO SAVING =====
@app.on_message(filters.video & filters.user(ADMIN_ID))
async def handle_video(client, message: Message):
    user_id = message.from_user.id
    if user_id not in upload_state:
        return await message.reply("⚠️ First use /upload ShowName or /upload ShowName SeasonNumber")

    state = upload_state[user_id]
    show_name = state["show"]
    season_number = state["season"]

    try:
        # ✅ Forward to storage channel
        fwd = await message.forward(STORAGE_CHANNEL_id, disable_notification=True)
        file_id = fwd.video.file_id

        # ✅ NEW: delete from storage channel to keep chat invisible
        await fwd.delete()

        # ✅ Save to DB
        data = load_data()
        if season_number:
            data.setdefault(show_name, {}).setdefault(season_number, []).append(file_id)
        else:
            data.setdefault(show_name, {}).setdefault("episodes", []).append(file_id)

        save_data(data)

        await message.reply(
            f"✅ Uploaded and saved for *{show_name}*{' Season ' + season_number if season_number else ''}.",
            quote=True
        )

    except Exception as e:
        print("Upload error:", e)
        await message.reply("❌ Failed to upload. Please check bot permissions or channel ID.")



@app.on_callback_query(filters.regex("^season_(.+)"))
async def season_menu(client, callback_query: CallbackQuery):
    show_name, season_number = callback_query.data.split("_")[1:]
    data = load_data()

    if show_name not in data or season_number not in data[show_name]:
        await callback_query.answer("Season not found.")
        return

    buttons = []
    for idx, file_id in enumerate(data[show_name][season_number], start=1):
        buttons.append([InlineKeyboardButton(f"Episode {idx}", callback_data=f"episode_{show_name}_{season_number}_{idx}")])

    await callback_query.message.edit(
        f"🎬 {show_name} - Season {season_number}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_callback_query(filters.regex("^show_(.+)"))
async def show_menu(client, callback_query: CallbackQuery):
    show_name = callback_query.data.split("_", 1)[1]
    data = load_data()

    if show_name not in data:
        await callback_query.answer("Show not found.")
        return

    buttons = []

    # ✅ Handle if episodes directly exist (no seasons)
    if "episodes" in data[show_name]:
        for idx, file_id in enumerate(data[show_name]["episodes"], start=1):
            buttons.append([InlineKeyboardButton(f"Episode {idx}", callback_data=f"episode_{show_name}_episodes_{idx}")])

    # ✅ Handle all seasons
    for season in data[show_name]:
        if season != "episodes":
            buttons.append([InlineKeyboardButton(f"📁 Season {season}", callback_data=f"season_{show_name}_{season}")])

    # ✅ Handle no content case
    if not buttons:
        buttons.append([InlineKeyboardButton("🚫 No videos uploaded yet", callback_data="noop")])

    await callback_query.message.edit(
        f"📺 Show: {show_name}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_callback_query(filters.regex("^noop$"))
async def do_nothing(client, callback_query: CallbackQuery):
    await callback_query.answer("Nothing to show here.")


@app.on_callback_query(filters.regex("^episode_(.+)"))
async def send_episode(client, callback_query: CallbackQuery):
    show_name, season_number, episode_index = callback_query.data.split("_")[1:]
    episode_index = int(episode_index) - 1
    data = load_data()

    if show_name not in data or season_number not in data[show_name] or episode_index >= len(data[show_name][season_number]):
        await callback_query.answer("Episode not found.")
        return

    file_id = data[show_name][season_number][episode_index]
    sent_message = await client.send_video(callback_query.from_user.id, file_id, caption=f"🎬 {show_name} - Season {season_number} - Episode {episode_index + 1}")
    await callback_query.answer("✅ Sent video. It will auto-delete in 3 minutes.")
    await asyncio.sleep(180)
    await sent_message.delete()


@app.on_callback_query(filters.regex("^report$"))
async def handle_report(client, callback_query: CallbackQuery):
    # Edit the message to prompt the user to type the name of the missing show/episode
    await callback_query.message.edit("✍️ Please type the name of the missing show/episode.")
    return


@app.on_message(filters.text & ~filters.command(["start", "help", "add", "upload", "delete"]) & ~filters.user(ADMIN_ID))
async def forward_report(client, message):
    if message.text:
        # Forward the report to the admin
        await client.send_message(ADMIN_ID, f"📩 User Report: {message.text}")
        
        # Inform the user that their report has been sent
        await message.reply("✅ Report sent! It will be added soon if available in official dub in 1-2 days.")
        
        # Delete the user's message after 60 seconds to keep the chat clean
        await asyncio.sleep(60)
        try:
            await message.delete()
        except:
            pass

@app.on_message(filters.command("list") & filters.user(ADMIN_ID))
async def list_content(client, message):
    data = load_data()
    output = "📋 Available Shows:\n"
    for show_name, seasons in data.items():
        output += f"- {show_name}\n"
        if "episodes" in seasons:
            output += "  - Episodes\n"
        for season_number, episodes in seasons.items():
            if season_number != "episodes":
                output += f"  - Season {season_number}\n"
                for idx in range(len(episodes)):
                    output += f"    - Episode {idx + 1}\n"
    await message.reply(output)



from urllib.parse import quote  # Add at top if not already imported
@app.on_message(filters.command("get_links") & filters.user(ADMIN_ID))
async def get_links(client, message):
    data = load_data()

    if not data:
        await message.reply("❌ No shows found.")
        return

    links = []
    bot_username = (await client.get_me()).username  # Dynamically fetch bot username

    for show_name in data.keys():
        # Generate a consistent slug (lowercase with underscores)
        slug = quote(show_name.lower().replace(" ", "_"))
        link = f"https://t.me/{bot_username}?start={slug}"
        links.append(f"🔗 **{show_name}**: [Click to View]({link})")

    await message.reply(
        "**Here are the show links:**\n\n" + "\n".join(links),
        disable_web_page_preview=True
    )

@app.on_message(filters.command("help"))
async def help_command(client, message):
    if message.from_user.id == ADMIN_ID:
        help_text = """
Admin Help:
- /add ShowName: Add a new show.
- /add Show Name: for new show buttons.
- /add ShowName SeasonNumber: Add a new season.
- /upload SweetHome:✅ Uploads to whole show
- /upload Sweet Home:✅ Uploads to whole show (with spaces)
- /upload SweetHome 2:✅ Uploads to Season 2
- /upload Sweet Home 2:✅ Uploads to Season 2 (with spaces)
- /upload Show Name > season season:✅ New format now supported
- /delete SweetHome:✅ deletes full show (old format)
- /delete Sweet Home:✅ deletes full show (new format)
- /delete SweetHome 2:✅ deletes season 2
- /delete Sweet Home 2:✅ deletes season 2
- /delete SweetHome 2 3:✅ deletes episode 3 of season 2
- /delete Sweet Home 2 3:✅ deletes episode 3 of season 2
- /delete Show Name/4:Deletes episode 4 under the flat show (not season)
- /list: List all shows, seasons, and episodes.
"""
    else:
        help_text = """
User Help:
- /search Query: Search for shows.
- Tap on a show to view its seasons and episodes.
- Videos will auto-delete after 3 minutes. Save or forward them to keep them.
"""
    await message.reply(help_text)


    
@app.on_message(filters.command("delete") & filters.user(ADMIN_ID))
async def delete_content(client, message):
    parts = message.text.split(" ", 1)
    if len(parts) < 2:
        return await message.reply("❗ Usage:\n/delete Show Name\n/delete Show Name SeasonNumber\n/delete Show Name SeasonNumber EpisodeNumber\n/delete ShowName/EpisodeNumber")

    args = parts[1].strip()
    data = load_data()

    # ✅ NEW FORMAT: /delete Show Name/EpisodeNumber
    if "/" in args:
        try:
            show_part, episode_part = args.rsplit("/", 1)
            episode_index = int(episode_part) - 1
        except ValueError:
            return await message.reply("❗ Episode number must be a valid integer after '/'.")
        
        matched_show = None
        for show in data:
            if show == show_part.strip():
                matched_show = show
                break

        if not matched_show:
            return await message.reply("❗ Show not found.")

        if "episodes" not in data[matched_show] or episode_index < 0 or episode_index >= len(data[matched_show]["episodes"]):
            return await message.reply("❗ Episode index out of range or no episodes found under show.")

        del data[matched_show]["episodes"][episode_index]
        save_data(data)
        return await message.reply(f"✅ Deleted episode {episode_index + 1} directly from *{matched_show}* (not from a season)")

    # 🧠 Original formats (Show, Season, or Show + Season + Episode)
    show_name = None
    season_number = None
    episode_index = None

    for name in data.keys():
        if args.startswith(name):
            remainder = args[len(name):].strip().split()
            show_name = name
            if len(remainder) >= 1:
                season_number = remainder[0]
            if len(remainder) == 2:
                try:
                    episode_index = int(remainder[1]) - 1
                except ValueError:
                    return await message.reply("❗ Episode index must be a number.")
            break

    if not show_name:
        return await message.reply("❗ Show not found.")

    # Case 1: Delete entire show
    if season_number is None:
        del data[show_name]
        save_data(data)
        return await message.reply(f"✅ Deleted entire show: {show_name}")

    # Case 2: Delete entire season
    if episode_index is None:
        if season_number not in data[show_name]:
            return await message.reply("❗ Season not found.")
        del data[show_name][season_number]
        save_data(data)
        return await message.reply(f"✅ Deleted season {season_number} under {show_name}")

    # Case 3: Delete specific episode under season
    if season_number not in data[show_name]:
        return await message.reply("❗ Season not found.")
    episodes = data[show_name][season_number]
    if episode_index < 0 or episode_index >= len(episodes):
        return await message.reply("❗ Episode index out of range.")
    del episodes[episode_index]
    save_data(data)
    return await message.reply(f"✅ Deleted episode {episode_index + 1} from {show_name} Season {season_number}")


        
@app.on_callback_query(filters.regex("^episode_(.+)_(.+)_(\\d+)$"))
async def send_episode(client, callback_query: CallbackQuery):
    show_name, season_or_key, index = callback_query.data.split("_")[1:]
    index = int(index) - 1
    data = load_data()

    if show_name not in data or season_or_key not in data[show_name]:
        await callback_query.answer("Episode not found.")
        return

    try:
        file_id = data[show_name][season_or_key][index]
        await client.send_video(
            chat_id=callback_query.from_user.id,
            video=file_id,
            caption=f"{show_name} - Episode {index + 1}"
        )
    except Exception as e:
        print(f"Send episode error: {e}")
        await callback_query.answer("⚠️ Failed to send video.")


@app.on_message(filters.command("test_forward"))
async def test_forward(client, message):
    try:
        # Attempt to forward a test message to the storage channel
        await client.forward_messages(
            chat_id=STORAGE_CHANNEL_id,
            from_chat_id=message.chat.id,
            message_ids=message.message_id,
            disable_notification=True
        )
        await message.reply("✅ Forwarding successful!")
    except Exception as e:
        await message.reply(f"❌ Error forwarding: {e}")


if __name__ == "__main__":
    print("Bot is running...")
    app.run()