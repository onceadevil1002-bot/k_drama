import os
import re
from pymongo import MongoClient
from dotenv import load_dotenv
from pyrogram import Client
import asyncio
from urllib.parse import unquote
from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Message,
    CallbackQuery,
)

# Load environment variables from .env
load_dotenv()

# === Load Environment Variables ===
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
STORAGE_CHANNEL_id = os.environ["STORAGE_CHANNEL_ID"]
MAIN_CHANNEL_LINK = os.environ["MAIN_CHANNEL_LINK"]





app = Client("kdrama_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# === MongoDB Connection ===
MONGO_URI = os.environ["MONGO_URI"]
client = MongoClient(MONGO_URI)
db = client['kdrama']  # <-- YOUR DATABASE NAME
collection = db['shows']        # <-- YOUR COLLECTION NAME


# Load data grouped by category
def load_data():
    data = {"Hindi Dubbed": {}, "Regional": {}}
    for doc in collection.find():
        category = doc.get("category", "Hindi Dubbed")  # default fallback
        show_name = doc["show_name"]
        episodes = doc["episodes"]
        if category not in data:
            data[category] = {}
        data[category][show_name] = episodes
    return data

def save_data(data):
    collection.delete_many({})
    for category, shows in data.items():
        for show_name, episodes in shows.items():
            collection.insert_one({
                "category": category,
                "show_name": show_name,
                "episodes": episodes
            })

def slugify_show_name(name: str) -> str:
    return re.sub(r'\W+', '', name.lower().replace(' ', '_'))

# === Global State ===
upload_state = {}  # user_id: {"show": show_name, "season": season_number}

# One-time migration (run once to assign category to old shows)
def migrate_category():
    collection.update_many({"category": {"$exists": False}}, {"$set": {"category": "Hindi Dubbed"}})

# Call it once in main
migrate_category()

from urllib.parse import unquote

joined_users = set()  # In-memory per-session; use DB for permanent join tracking

from urllib.parse import unquote

@app.on_message(filters.command("start"))
async def start(client, message: Message):
    user_id = message.from_user.id
    data = load_data()
    args = message.text.split()
    slug = args[1] if len(args) > 1 else None

    # Force join for new users
    if not slug and user_id not in joined_users:
        keyboard = [
            [InlineKeyboardButton("📣 Join Main Channel", url=MAIN_CHANNEL_LINK)],
            [InlineKeyboardButton("✅ I Joined", callback_data="joined")]
        ]
        return await message.reply("🔒 Please join our channel to use the bot.", reply_markup=InlineKeyboardMarkup(keyboard))

    if slug and "__" in slug:
        try:
            category_part, show_part = slug.split("__", 1)
            # Normalize safely
            category = category_part.replace("_", " ").lower().strip()
            show_name = show_part.replace("_", " ").strip()

            # Match against lowercased category keys
            matched_category = None
            for existing_category in data:
                if existing_category.lower().strip() == category:
                    matched_category = existing_category
                    break

            if not matched_category or show_name not in data[matched_category]:
                return await message.reply("❌ Show not found or category mismatch.")
        except:
            return await message.reply("❌ Invalid link format.")

        # ✅ Build inline buttons
        buttons = [[InlineKeyboardButton(f"⭐ {show_name}", callback_data=f"show_{matched_category}_{show_name}")]]
        for s in sorted(data[matched_category]):
            if s != show_name:
                emoji = "🎞" if matched_category == "Hindi Dubbed" else "🌍"
                buttons.append([InlineKeyboardButton(f"{emoji} {s}", callback_data=f"show_{matched_category}_{s}")])

        title = "🎞 Hindi Dubbed Shows:" if matched_category == "Hindi Dubbed" else "🌍 Regional Shows:"
        joined_users.add(user_id)
        return await message.reply(title, reply_markup=InlineKeyboardMarkup(buttons))

    # Fallback: default main menu
    joined_users.add(user_id)
    keyboard = [
        [InlineKeyboardButton("📂 Hindi Dubbed", callback_data="category_hindi")],
        [InlineKeyboardButton("🌐 Regional", callback_data="category_regional")],
        [InlineKeyboardButton("❗ Report For Show/Episodes", callback_data="report")]
    ]
    return await message.reply("🎬 Welcome to K-Drama Bot! Choose a category:", reply_markup=InlineKeyboardMarkup(keyboard))


@app.on_callback_query(filters.regex("^category_hindi$"))
async def category_hindi(client, callback):
    data = load_data()
    if "Hindi Dubbed" not in data:
        return await callback.answer("❌ No shows found.")
    buttons = [
        [InlineKeyboardButton(f"🎞 {show}", callback_data=f"show_Hindi Dubbed_{show}")]
        for show in sorted(data["Hindi Dubbed"])
    ]
    await callback.message.edit("🎞 Hindi Dubbed Shows:", reply_markup=InlineKeyboardMarkup(buttons))


@app.on_callback_query(filters.regex("^category_regional$"))
async def category_regional(client, callback):
    data = load_data()
    if "Regional" not in data:
        return await callback.answer("❌ No shows found.")
    buttons = [
        [InlineKeyboardButton(f"🌍 {show}", callback_data=f"show_Regional_{show}")]
        for show in sorted(data["Regional"])
    ]
    await callback.message.edit("🌍 Regional Shows:", reply_markup=InlineKeyboardMarkup(buttons))



@app.on_callback_query(filters.regex("^joined$"))
async def joined_channel(client, callback: CallbackQuery):
    user_id = callback.from_user.id
    joined_users.add(user_id)

    keyboard = [
        [InlineKeyboardButton("📂 Hindi Dubbed", callback_data="category_hindi")],
        [InlineKeyboardButton("🌐 Regional", callback_data="category_regional")],
        [InlineKeyboardButton("❗ Report For Show/Episodes", callback_data="report")]
    ]
    await callback.message.edit("🎬 Welcome to K-Drama Bot! Choose a category:", reply_markup=InlineKeyboardMarkup(keyboard))

# ✅ Combined Add Show/Season Handler
@app.on_message(filters.command(["add", "add_hindi", "add_regional"]) & filters.user(ADMIN_ID))
async def add_show(client, message):
    cmd = message.command[0]
    if len(message.command) < 2:
        return await message.reply(
            "❗ Usage:\n"
            "/add Show Name\n"
            "/add Show Name > Category\n"
            "/add Show Name 1\n"
            "/add Show Name > Category 1"
        )

    args = message.text.split(" ", 1)[1].strip()
    category = "Hindi Dubbed"  # default
    show_name = None
    season_number = None

    if cmd == "add_hindi":
        category = "Hindi Dubbed"
    elif cmd == "add_regional":
        category = "Regional"

    # Check if category is included
    if ">" in args:
        try:
            show_part, rest = args.split(">", 1)
            show_part = show_part.strip()
            rest = rest.strip().split()

            show_name = show_part
            if rest and rest[-1].isdigit():
                season_number = rest[-1]
                category = " ".join(rest[:-1]).title() if len(rest) > 1 else category
            else:
                category = " ".join(rest).title()
        except:
            return await message.reply("❗ Invalid format. Use /add Show Name > Category 1")
    else:
        # Check for season number directly at the end
        parts = args.rsplit(" ", 1)
        if parts[-1].isdigit():
            show_name = parts[0].strip()
            season_number = parts[1].strip()
        else:
            show_name = args.strip()

    data = load_data()

    # Ensure category structure exists
    if category not in data:
        data[category] = {}

    # If show doesn't exist, add it
    if show_name not in data[category]:
        data[category][show_name] = {"episodes": []}
        await message.reply(f"✅ Added show: *{show_name}* under *{category}*")

    # If season is provided, add it to the show
    if season_number:
        if season_number in data[category][show_name]:
            return await message.reply("⚠️ Season already exists.")
        data[category][show_name][season_number] = []
        await message.reply(f"✅ Added *Season {season_number}* under *{show_name}*")

    save_data(data)

@app.on_message(filters.command(["upload", "upload_hindi", "upload_regional"]) & filters.user(ADMIN_ID))
async def upload_handler(client, message: Message):
    cmd = message.command[0]
    if len(message.command) < 2:
        return await message.reply("❗ Usage:\n"
                                   "/upload Show Name\n"
                                   "/upload Show Name 1\n"
                                   "/upload Show Name > Category\n"
                                   "/upload Show Name > Category 2")

    args = message.text.split(" ", 1)[1].strip()
    data = load_data()

    category = "Hindi Dubbed"
    show_name = None
    season_number = None

    # Detect shortcut commands
    if cmd == "upload_hindi":
        category = "Hindi Dubbed"
    elif cmd == "upload_regional":
        category = "Regional"

    # Handle '>' category format
    if ">" in args:
        try:
            show_part, rest = args.split(">", 1)
            show_part = show_part.strip()
            rest = rest.strip().split()

            show_name = show_part
            if rest and rest[-1].isdigit():
                season_number = rest[-1]
                category = " ".join(rest[:-1]).title() if len(rest) > 1 else category
            else:
                category = " ".join(rest).title()
        except:
            return await message.reply("❗ Invalid format. Use /upload Show Name > Category 1")
    else:
        # Handle legacy: Show Name 1
        parts = args.rsplit(" ", 1)
        if parts[-1].isdigit():
            show_name = parts[0].strip()
            season_number = parts[1].strip()
        else:
            show_name = args.strip()

    if category not in data or show_name not in data[category]:
        return await message.reply("❗ Show not found. Use /add command first.")

    # 🔐 Split Safety Check
    if season_number:
        if season_number not in data[category][show_name]:
            return await message.reply("❗ Season not found. Use /add to create it first.")
        episodes = data[category][show_name][season_number]
        if episodes and isinstance(episodes[-1], list):
            return await message.reply("⚠️ Last episode appears split. Use /upload_split to upload parts safely.")
        upload_state[message.from_user.id] = {
            "show": show_name,
            "season": season_number,
            "category": category
        }
        return await message.reply(f"📤 Send videos now for *{show_name}* Season *{season_number}*", quote=True)
    else:
        flat_eps = data[category][show_name]["episodes"]
        if flat_eps and isinstance(flat_eps[-1], list):
            return await message.reply("⚠️ Last episode appears split. Use /upload_split to upload parts safely.")
        upload_state[message.from_user.id] = {
            "show": show_name,
            "season": None,
            "category": category
        }
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
    category = state["category"]

    try:
        # ✅ Forward to storage channel
        fwd = await message.forward(STORAGE_CHANNEL_id, disable_notification=True)
        file_id = fwd.video.file_id

        # ✅ NEW: delete from storage channel to keep chat invisible
        await fwd.delete()

        # ✅ Save to MongoDB in correct category path
        data = load_data()

        if "split_index" in state:
            # 🔥 Uploading to a split episode
            split_index = state["split_index"]
            part = state["part"]

            episodes = data[category][show_name][season_number]
            if not isinstance(episodes[split_index], list):
                return await message.reply("❌ Target episode is not split. Use /split_episode first.")

            # Ensure list has space for two parts
            while len(episodes[split_index]) <= part:
                episodes[split_index].append(None)

            episodes[split_index][part] = file_id
            save_data(data)
            return await message.reply(f"✅ Uploaded part {part + 1} of split episode {split_index + 1} in *{show_name}* Season {season_number}")

        if season_number:
            data[category][show_name].setdefault(season_number, []).append(file_id)
        else:
            data[category][show_name].setdefault("episodes", []).append(file_id)

        save_data(data)

        await message.reply(
            f"✅ Uploaded and saved for *{show_name}*{' Season ' + season_number if season_number else ''}.",
            quote=True
        )

    except Exception as e:
        print("Upload error:", e)
        await message.reply("❌ Failed to upload. Please check bot permissions or channel ID.")
@app.on_callback_query(filters.regex("^multi_"))
async def multi_part_episode_menu(client, callback: CallbackQuery):
    try:
        _, category, show_name, season_or_key, index = callback.data.split("_", 4)
        index = int(index) - 1
        data = load_data()

        multi_parts = data[category][show_name][season_or_key][index]
        if not isinstance(multi_parts, list) or len(multi_parts) != 2:
            return await callback.answer("⚠️ Multi-part not found.")

        buttons = [
            [InlineKeyboardButton("▶️ Episode", callback_data=f"mp_ep_{category}_{show_name}_{season_or_key}_{index}_0")],
            [InlineKeyboardButton("▶️ Episode .5", callback_data=f"mp_ep_{category}_{show_name}_{season_or_key}_{index}_1")]
        ]

        await callback.message.edit(
            f"🎬 Parts for Episode {index + 1}",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    except Exception as e:
        print("Multi-part submenu error:", e)
        await callback.answer("❌ Error showing split parts.")

@app.on_callback_query(filters.regex("^mp_ep_"))
async def send_multi_part_episode(client, callback: CallbackQuery):
    try:
        _, category, show_name, season_or_key, index_str, part_str = callback.data.split("_", 5)
        index = int(index_str)
        part = int(part_str)
        data = load_data()

        file_id = data[category][show_name][season_or_key][index][part]
        caption = f"🎬 {show_name} - Episode {index + 1}{'.5' if part == 1 else ''}"
        sent = await client.send_video(chat_id=callback.from_user.id, video=file_id, caption=caption)

        await callback.answer("✅ Sent! Auto-delete in 3 mins.")
        await asyncio.sleep(180)
        await sent.delete()

    except Exception as e:
        print("Multi-part send error:", e)
        await callback.answer("❌ Couldn't send episode.")




@app.on_callback_query(filters.regex("^season_(.+?)_(.+?)_(.+)$"))
async def season_menu(client, callback_query):
    category, show_name, season_number = callback_query.data.split("_", 3)[1:]
    data = load_data()

    if show_name not in data.get(category, {}) or season_number not in data[category][show_name]:
        await callback_query.answer("Season not found.")
        return

    buttons = []
    for idx, file_id in enumerate(data[category][show_name][season_number], start=1):
        buttons.append([
            InlineKeyboardButton(
                f"Episode {idx}",
                callback_data=f"episode_{category}_{show_name}_{season_number}_{idx}"
            )
        ])

    await callback_query.message.edit(
        f"🎬 {show_name} - Season {season_number}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_callback_query(filters.regex("^show_(.+?)_(.+)$"))
async def show_menu(client, callback_query):
    category, show_name = callback_query.data.split("_", 2)[1:]
    data = load_data()

    if show_name not in data.get(category, {}):
        await callback_query.answer("Show not found.")
        return

    buttons = []
    episodes = data[category][show_name]

    # Handle flat episodes
    if "episodes" in episodes:
        for idx, ep in enumerate(episodes["episodes"], start=1):
            if isinstance(ep, list) and len(ep) == 2:
                # Split episode with two parts
                buttons.append([
                    InlineKeyboardButton(f"📁 Episode {idx} (split)", callback_data=f"multi_{category}_{show_name}_episodes_{idx}")
                ])
            else:
                buttons.append([
                    InlineKeyboardButton(f"Episode {idx}", callback_data=f"episode_{category}_{show_name}_episodes_{idx}")
                ])

    # Handle seasons
    for season in episodes:
        if season != "episodes":
            buttons.append([
                InlineKeyboardButton(f"📁 Season {season}", callback_data=f"season_{category}_{show_name}_{season}")
            ])

    if not buttons:
        buttons.append([InlineKeyboardButton("🚫 No videos uploaded yet", callback_data="noop")])

    await callback_query.message.edit(
        f"📺 Show: {show_name}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )



@app.on_message(filters.command("split_episode") & filters.user(ADMIN_ID))
async def split_episode_command(client, message: Message):
    if len(message.command) < 2:
        return await message.reply(
            "❗ Usage:\n"
            "/split_episode Show Name /Episode > Category\n"
            "/split_episode Show Name Season /Episode > Category"
        )

    try:
        args = message.text.split(" ", 1)[1].strip()
        if ">" not in args or "/" not in args:
            return await message.reply("❗ Format must include '/' for episode and '>' for category")

        show_and_season_part, right_part = args.split(">", 1)
        show_parts = show_and_season_part.strip().split("/")
        category = right_part.strip().title()

        episode_num = int(show_parts[1].strip())  # like /6 → 6
        episode_index = episode_num - 1

        show_tokens = show_parts[0].strip().split()
        show_name = " ".join(show_tokens[:-1]) if show_tokens[-1].isdigit() else " ".join(show_tokens)
        season_number = show_tokens[-1] if show_tokens[-1].isdigit() else None

        data = load_data()

        if category not in data or show_name not in data[category]:
            return await message.reply("❌ Show not found.")

        if season_number:  # season-based
            if season_number not in data[category][show_name]:
                return await message.reply("❌ Season not found.")

            episodes = data[category][show_name][season_number]
        else:  # flat
            if "episodes" not in data[category][show_name]:
                return await message.reply("❌ No flat episodes found in this show.")
            episodes = data[category][show_name]["episodes"]

        if episode_index < 0 or episode_index >= len(episodes):
            return await message.reply("❌ Episode index out of range.")

        original = episodes[episode_index]
        if isinstance(original, list):
            return await message.reply("⚠️ Episode is already split.")

        # Convert to [part1, None]
        episodes[episode_index] = [original, None]
        save_data(data)

        ep_label = f"{episode_num}" if not season_number else f"{episode_num} (Season {season_number})"
        return await message.reply(f"✅ Episode {ep_label} is now split into {episode_num} and {episode_num}.5")

    except Exception as e:
        print("[split_episode] error:", e)
        return await message.reply("❌ Failed to process split command.")



@app.on_message(filters.command("upload_split") & filters.user(ADMIN_ID))
async def upload_split(client, message: Message):
    try:
        args = message.text.split(" ", 1)[1]
        show_part, rest = args.split(">", 1)
        show_name = show_part.strip()
        parts = rest.strip().split()
        if len(parts) != 4:
            raise ValueError

        category = parts[0].title()
        season = parts[1]
        episode = int(parts[2])
        part_index = int(parts[3])  # 1 or 2

        if part_index not in (1, 2):
            return await message.reply("❌ Part must be 1 or 2.")

        data = load_data()

        if category not in data or show_name not in data[category]:
            return await message.reply("❌ Show not found.")
        if season not in data[category][show_name]:
            return await message.reply("❌ Season not found.")
        episodes = data[category][show_name][season]

        if episode < 1 or episode > len(episodes):
            return await message.reply("❌ Episode index out of range.")

        if not isinstance(episodes[episode - 1], list):
            return await message.reply("❌ Episode not split. Use /split_episode first.")

        upload_state[message.from_user.id] = {
            "show": show_name,
            "season": season,
            "category": category,
            "split_index": episode - 1,
            "part": part_index - 1  # for Python index
        }

        await message.reply(f"📤 Send video for *{show_name}* Season {season} Episode {episode} Part {part_index}")
    except:
        await message.reply("❗ Usage:\n/upload_split Show > Category Season EpisodeIndex Part")



@app.on_callback_query(filters.regex("^noop$"))
async def do_nothing(client, callback_query: CallbackQuery):
    await callback_query.answer("Nothing to show here.")


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

from urllib.parse import quote

@app.on_message(filters.command("get_links") & filters.user(ADMIN_ID))
async def get_links(client, message):
    data = load_data()
    if not data:
        return await message.reply("❌ No shows found.")

    bot_username = (await client.get_me()).username
    links = []
    added = set()

    for category in data:
        for show_name, content in data[category].items():
            # Skip if it's a nested season inside another show (not valid show)
            if not isinstance(content, dict):
                continue

            # Only generate once per unique show name
            if show_name in added:
                continue

            slug = f"{category.replace(' ', '_')}__{show_name.replace(' ', '_')}"
            link = f"https://t.me/{bot_username}?start={slug}"
            links.append(f"🔗 [{show_name} ({category})]({link})")
            added.add(show_name)

    await message.reply(
        "**🎬 Direct Show Links:**\n\n" + "\n".join(links),
        disable_web_page_preview=True
    )


# === Help Command (Updated) ===
@app.on_message(filters.command("help"))
async def help_command(client, message):
    is_admin = message.from_user.id == ADMIN_ID
    admin_help = """
Admin Help Commands:

📁 Add Shows/Seasons:
  /add Show Name
  /add Show Name > Hindi Dubbed
  /add Show Name > Regional
  /add Show Name 1
  /add Show Name > Hindi Dubbed 2
  /add_hindi Show Name
  /add_regional Show Name

📤 Upload Videos:
  /upload Show Name
  /upload Show Name 1
  /upload Show Name > Regional
  /upload Show Name > Hindi Dubbed 2
  /upload_hindi Show Name
  /upload_regional Show Name

🎬 Split & Upload Parts:
  /split_episode Show Name /4 > Hindi Dubbed
  /split_episode Show Name 2 /3 > Regional
  /upload_split Show Name > Regional 2 3 1
  /upload_split Show Name > Hindi Dubbed 1 4 2

🧹 Delete Shows/Episodes:
  /delete Show Name > Hindi Dubbed
  /delete Show Name > Regional 2
  /delete Show Name > Regional 2 3
  /delete_category hindi ShowName
  /delete_category regional ShowName Season
  /delete_category hindi ShowName Season Episode

🔗 Utilities:
  /get_links – Generate deep links to each show
  /list – Show structure of uploaded data
  /test_forward – Verify message forwarding works
"""

    user_help = """
User Commands:
- /start – Start bot
- /help – Show this help
- /report – Report missing episodes or shows
- Watch episodes using inline buttons.
- All videos auto-delete after 3 minutes.
"""
    await message.reply(admin_help if is_admin else user_help)

@app.on_message(filters.command(["delete", "delete_category"]) & filters.user(ADMIN_ID))
async def delete_content(client, message: Message):
    if len(message.command) < 2:
        return await message.reply("❗ Usage:\n"
                                   "/delete Show\n"
                                   "/delete Show > Category\n"
                                   "/delete Show > Category 2\n"
                                   "/delete Show > Category 2 3\n"
                                   "/delete Show/EpisodeIndex\n"
                                   "/delete_category hindi|regional ShowName\n"
                                   "/delete_category hindi|regional ShowName Season [Episode]")

    args = message.text.split(" ", 1)[1].strip()
    data = load_data()

    # ✅ Case 1: Flat format like ShowName/3
    if "/" in args:
        try:
            show_part, episode_str = args.rsplit("/", 1)
            episode_index = int(episode_str) - 1
        except:
            return await message.reply("❗ Episode must be a number after '/'.")
        for cat in data:
            if show_part in data[cat] and "episodes" in data[cat][show_part]:
                episodes = data[cat][show_part]["episodes"]
                if 0 <= episode_index < len(episodes):
                    del episodes[episode_index]
                    save_data(data)
                    return await message.reply(f"✅ Deleted episode {episode_index + 1} from *{show_part}* under *{cat}*")
        return await message.reply("❌ Show or episode not found.")

    # ✅ Case 2: delete_category shortcut
    if message.command[0] == "delete_category":
        try:
            parts = args.strip().split()
            if len(parts) < 2:
                return await message.reply("❗ Usage: /delete_category hindi|regional ShowName [Season] [Episode]")
            category_key = parts[0].lower()
            if category_key not in ["hindi", "regional"]:
                return await message.reply("❗ Category must be 'hindi' or 'regional'")
            category = "Hindi Dubbed" if category_key == "hindi" else "Regional"
            show_name = parts[1]
            season = parts[2] if len(parts) >= 3 else None
            episode_index = int(parts[3]) - 1 if len(parts) == 4 else None
        except:
            return await message.reply("❗ Invalid format for /delete_category")

        if show_name not in data.get(category, {}):
            return await message.reply("❌ Show not found in category.")

        if not season:
            del data[category][show_name]
            save_data(data)
            return await message.reply(f"✅ Deleted full show *{show_name}* from *{category}*")

        if season not in data[category][show_name]:
            return await message.reply("❌ Season not found.")
        if episode_index is not None:
            episodes = data[category][show_name][season]
            if 0 <= episode_index < len(episodes):
                del episodes[episode_index]
                save_data(data)
                return await message.reply(f"✅ Deleted episode {episode_index + 1} from *{show_name}* Season {season} under *{category}*")
            else:
                return await message.reply("❌ Episode index out of range.")
        else:
            del data[category][show_name][season]
            save_data(data)
            return await message.reply(f"✅ Deleted Season {season} from *{show_name}* under *{category}*")

    # ✅ Case 3: format with ">"
    show_name = None
    category = "Hindi Dubbed"
    season = None
    episode_index = None
    if ">" in args:
        try:
            left, right = args.split(">", 1)
            show_name = left.strip()
            right = right.strip().split()
            if len(right) == 1:
                category = right[0].title()
            elif len(right) == 2:
                category = right[0].title()
                season = right[1]
            elif len(right) == 3:
                category = right[0].title()
                season = right[1]
                episode_index = int(right[2]) - 1
        except:
            return await message.reply("❗ Invalid format for /delete.")
    else:
        show_name = args

    if category not in data or show_name not in data[category]:
        return await message.reply("❌ Show not found.")

    if season:
        if season not in data[category][show_name]:
            return await message.reply("❌ Season not found.")
        if episode_index is not None:
            episodes = data[category][show_name][season]
            if episode_index < 0 or episode_index >= len(episodes):
                return await message.reply("❌ Episode index out of range.")
            del episodes[episode_index]
            save_data(data)
            return await message.reply(f"✅ Deleted episode {episode_index + 1} from *{show_name}* Season {season} under *{category}*")
        else:
            del data[category][show_name][season]
            save_data(data)
            return await message.reply(f"✅ Deleted Season {season} from *{show_name}* under *{category}*")
    else:
        del data[category][show_name]
        save_data(data)
        return await message.reply(f"✅ Deleted full show *{show_name}* from *{category}*")
@app.on_callback_query(filters.regex("^episode_"))
async def send_episode(client, callback_query: CallbackQuery):
    try:
        # Remove the "episode_" prefix safely
        payload = callback_query.data[len("episode_"):]
        parts = payload.split("_")
        
        # We expect at least 4 parts: category, show_name (can be joined), season_or_key, index
        if len(parts) < 4:
            return await callback_query.answer("❌ Invalid episode data.")

        category = parts[0]
        index_str = parts[-1]
        season_or_key = parts[-2]
        show_name_parts = parts[1:-2]
        show_name = "_".join(show_name_parts)  # Reconstruct show name safely

        index = int(index_str) - 1
        data = load_data()

        if show_name not in data.get(category, {}):
            return await callback_query.answer("❌ Show not found.")

        if season_or_key not in data[category][show_name]:
            return await callback_query.answer("❌ Season or episode list not found.")

        episode_list = data[category][show_name][season_or_key]
        if index < 0 or index >= len(episode_list):
            return await callback_query.answer("❌ Episode index out of range.")

        file_id = episode_list[index]
        sent_msg = await client.send_video(
            chat_id=callback_query.from_user.id,
            video=file_id,
            caption=f"🎬 {show_name.replace('_', ' ')} - Episode {index + 1}"
        )

        await callback_query.answer("✅ Sent video. It will auto-delete in 3 minutes.")
        await asyncio.sleep(180)
        await sent_msg.delete()

    except Exception as e:
        print(f"[send_episode] ERROR: {e}")
        await callback_query.answer("⚠️ Error while sending episode.")

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