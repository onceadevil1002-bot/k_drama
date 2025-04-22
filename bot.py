import json
import re
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

API_ID = 123456  # Replace with your actual API ID
API_HASH = "your_api_hash"
BOT_TOKEN = "your_bot_token"
ADMIN_ID = 6244759828  # Replace with your actual admin ID

DATA_FILE = "data.json"

def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def slugify_show_name(name: str) -> str:
    return re.sub(r'\W+', '', name.lower().replace(' ', '_'))

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start(client, message: Message):
    keyboard = [[
        InlineKeyboardButton("Main Channel", url="https://t.me/KDRAMAAVIL"),
        InlineKeyboardButton("Report For Show/Episodes", callback_data="report_show")
    ]]
    await message.reply("Welcome to K_DRAAMAA_Bot!\nChoose an option below:", reply_markup=InlineKeyboardMarkup(keyboard))

@app.on_message(filters.command("add_show_name"))
async def add_show_name(client, message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split(" ", 1)
    if len(parts) < 2:
        await message.reply("Usage: /add_show_name Show Name")
        return

    show_name = parts[1]
    data = load_data()
    slug = slugify_show_name(show_name)
    if slug in data:
        await message.reply("This show already exists!")
    else:
        data[slug] = {"name": show_name, "media": []}
        save_data(data)
        await message.reply(f"Show '{show_name}' added successfully!")

@app.on_message(filters.command("upload"))
async def upload_file(client, message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.caption.split(" ", 1) if message.caption else None
    if not parts or len(parts) < 2:
        await message.reply("Usage: /upload Show Name (in caption)")
        return

    show_name = parts[1]
    slug = slugify_show_name(show_name)
    data = load_data()

    if slug not in data:
        await message.reply("This show does not exist. Use /add_show_name first.")
        return

    file_id = message.video.file_id if message.video else message.document.file_id
    if not file_id:
        await message.reply("Only video or document files supported.")
        return

    data[slug]["media"].append(file_id)
    save_data(data)
    await message.reply(f"Uploaded and saved to '{show_name}' successfully!")

@app.on_message(filters.command("list"))
async def list_shows(client, message: Message):
    data = load_data()
    if not data:
        await message.reply("No shows found.")
        return

    buttons = [[InlineKeyboardButton(show["name"], callback_data=f"show_{slug}")]
               for slug, show in data.items()]
    await message.reply("Available Shows:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query()
async def handle_callback(client, callback):
    data = load_data()
    if callback.data.startswith("show_"):
        slug = callback.data.split("show_")[1]
        if slug in data:
            media_list = data[slug].get("media", [])
            if not media_list:
                await callback.message.reply("No media found for this show.")
                return

            for file_id in media_list:
                await client.send_video(callback.message.chat.id, file_id)
            await callback.answer()

    elif callback.data == "report_show":
        await callback.message.reply("Please type the name of the missing show or episode. We'll try to add it in 1-2 days (if officially dubbed).")
        await callback.answer()

@app.on_message(filters.text & filters.private)
async def forward_report(client, message: Message):
    if message.text.startswith("/"):
        return  # ignore commands here
    await client.send_message(ADMIN_ID, f"[USER REPORT]\nFrom: {message.from_user.mention()}\nText: {message.text}")
    await message.reply("Thank you for your report! We'll review it soon.")

@app.on_message(filters.command("delete_all"))
async def delete_specific_show(client, message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split(" ", 1)
    if len(parts) < 2:
        await message.reply("Usage: /delete_all Show Name")
        return

    show_name = parts[1]
    slug = slugify_show_name(show_name)
    data = load_data()

    if slug not in data:
        await message.reply("Show not found.")
        return

    del data[slug]
    save_data(data)
    await message.reply(f"Show '{show_name}' and its media have been deleted.")

print("Bot is running...")
app.run()
