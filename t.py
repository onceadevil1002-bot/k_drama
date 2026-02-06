import asyncio
import os

# Ensure an event loop exists before importing pyrogram.sync utilities
# which call `asyncio.get_event_loop()` at import time.
asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import filters
from pyrogram.client import Client
import dotenv
dotenv.load_dotenv()  # Load environment variables from .env file

app = Client(name="bot", api_id=int(os.environ["API_ID"]), api_hash=os.environ["API_HASH"], bot_token=os.environ["BOT_TOKEN"])

@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply("Bot is alive")

if __name__ == "__main__":
    app.run()
