import logging
import asyncio
import time
from datetime import datetime, timedelta
from pyrogram import Client, idle
from bot.config import API_ID, API_HASH, BOT_TOKEN
from bot.database.mongo import db
from bot.services.users import refresh_all_profiles
from bot.handlers.user_cmds import register_user_handlers
from bot.handlers.admin_cmds import register_admin_handlers
from bot.handlers.admin_data_entry import register_admin_data_handlers
from bot.handlers.report_handlers import register_report_handlers
from bot.handlers.trending_handlers import register_trending_handlers
from bot.handlers.group_viewer import register_group_handlers
from bot.handlers.callbacks import register_callback_handlers
from bot.handlers.inline import register_inline_handlers
from bot.utils.logger import logger, time_block
from bot.keep_alive import start_server
import os

# Record start time for latency audit
START_UP_TIME = time.perf_counter()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class Bot(Client):
    def __init__(self):
        super().__init__(
            name="k_drama_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            plugins=dict(root="bot/handlers")
        )

    async def start(self):
        with time_block("Bot.start()"):
            await super().start()
        
        # Resolve critical peers so they are always available
        try:
            from bot.config import STORAGE_CHANNEL_ID
            await self.get_chat(STORAGE_CHANNEL_ID)
            logger.info(f"Storage channel peer resolved: {STORAGE_CHANNEL_ID}")
        except Exception as e:
            logger.warning(f"Could not resolve storage channel: {e}")
        
        # Start background tasks
        asyncio.create_task(self.midnight_refresh_loop())
        from bot.utils.backup import midnight_backup_loop
        asyncio.create_task(midnight_backup_loop(self))        
        latency = (time.perf_counter() - START_UP_TIME) * 1000
        logger.info(f"PERF: Bot startup completed in {latency:.2f}ms")
        logger.info("Bot started successfully")

    async def midnight_refresh_loop(self):
        """Wait until midnight and then refresh all profiles daily."""
        while True:
            try:
                now = datetime.now()
                # Calculate seconds until next midnight
                next_run = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                wait_seconds = (next_run - now).total_seconds()
                
                logger.info(f"Next profile refresh scheduled in {wait_seconds/3600:.2f} hours.")
                await asyncio.sleep(wait_seconds)
                
                await refresh_all_profiles(self)
            except Exception as e:
                logger.error(f"Midnight refresh loop error: {e}")
                await asyncio.sleep(3600) # Wait an hour before retry on error

    async def stop(self, *args):
        await super().stop()
        logger.info("Bot stopped")

app = Bot()

# Register handlers (alternative to plugins if manual registration is preferred)
register_user_handlers(app)
register_admin_handlers(app)
register_admin_data_handlers(app)
register_report_handlers(app)
register_trending_handlers(app)
register_group_handlers(app)
register_callback_handlers(app)
register_inline_handlers(app)

if __name__ == "__main__":
    start_server(port=int(os.environ.get("PORT", 10000)))
    app.run()
