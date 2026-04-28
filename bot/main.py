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
from bot.handlers.channel_events import register_channel_handlers
from bot.utils.logger import logger, time_block
from bot.utils.ids import warm_hash_cache
from bot.services.verification import resolve_required_channels, scan_all_channels
from bot.keep_alive import start_server
import os

START_UP_TIME = time.perf_counter()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Suppress noisy pymongo server-selection INFO logs during initial Atlas connection
logging.getLogger("pymongo").setLevel(logging.WARNING)
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

        # Step 1: Resolve required channel peers
        # Must happen before any channel membership checks
        await resolve_required_channels(self)

        # Step 2: Pre-warm hash cache
        # After this, make_id/resolve_id are pure in-memory (~0ms)
        await warm_hash_cache()

        # Step 3: Resolve storage channel peer
        # Private channels can't be resolved by numeric ID alone on a cold
        # session because Pyrogram needs an access hash in its peer cache.
        # For bots that are admins/members of the channel, Telegram accepts
        # access_hash=0 via raw API, which seeds the session's peer cache.
        from bot.config import STORAGE_CHANNEL_ID
        _storage_ok = False
        try:
            await self.get_chat(STORAGE_CHANNEL_ID)
            _storage_ok = True
        except Exception:
            pass

        if not _storage_ok:
            try:
                from pyrogram.raw.functions.channels import GetChannels
                from pyrogram.raw.types import InputChannel
                # Strip -100 prefix to get raw channel ID
                raw_id = int(str(STORAGE_CHANNEL_ID).replace("-100", ""))
                await self.invoke(
                    GetChannels(id=[InputChannel(channel_id=raw_id, access_hash=0)])
                )
                # Now the peer is cached in the session — get_chat will work
                await self.get_chat(STORAGE_CHANNEL_ID)
                _storage_ok = True
            except Exception as e:
                logger.warning(f"Raw API storage channel resolution failed: {e}")

        if _storage_ok:
            logger.info(f"Storage channel peer resolved: {STORAGE_CHANNEL_ID}")
        else:
            logger.critical(
                f"STORAGE CHANNEL UNREACHABLE: {STORAGE_CHANNEL_ID}. "
                "Photo uploads will fail until this is resolved. "
                "Verify STORAGE_CHANNEL_ID env var and bot membership."
            )

        # Step 4: Create indexes
        try:
            await db.create_indexes()
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

        # Step 5: Start background loops
        asyncio.create_task(self.midnight_refresh_loop())
        asyncio.create_task(self.midnight_channel_scan_loop())

        # Defer startup channel scan by 5s so the HTTP health endpoint can
        # respond before we hit Telegram API with bulk get_chat_members calls.
        # This prevents Render/Koyeb from marking the service as unhealthy.
        async def _deferred_scan():
            await asyncio.sleep(5)
            await scan_all_channels(self)
        asyncio.create_task(_deferred_scan())

        from bot.utils.backup import midnight_backup_loop
        asyncio.create_task(midnight_backup_loop(self))

        latency = (time.perf_counter() - START_UP_TIME) * 1000
        logger.info(f"PERF: Bot startup completed in {latency:.2f}ms")
        logger.info("Bot started successfully")

    async def midnight_channel_scan_loop(self):
        """Rescan all required channels at midnight to sync member DB."""
        while True:
            try:
                now = datetime.now()
                next_run = (
                    now.replace(hour=0, minute=0, second=0, microsecond=0)
                    + timedelta(days=1)
                )
                wait_seconds = (next_run - now).total_seconds()
                logger.info(f"Next channel scan in {wait_seconds/3600:.2f} hours.")
                await asyncio.sleep(wait_seconds)
                await scan_all_channels(self)
            except Exception as e:
                logger.error(f"Midnight channel scan error: {e}")
                await asyncio.sleep(3600)

    async def midnight_refresh_loop(self):
        """Wait until midnight and then refresh all profiles daily."""
        while True:
            try:
                now = datetime.now()
                next_run = (
                    now.replace(hour=0, minute=0, second=0, microsecond=0)
                    + timedelta(days=1)
                )
                wait_seconds = (next_run - now).total_seconds()
                logger.info(f"Next profile refresh scheduled in {wait_seconds/3600:.2f} hours.")
                await asyncio.sleep(wait_seconds)
                await refresh_all_profiles(self)
            except Exception as e:
                logger.error(f"Midnight refresh loop error: {e}")
                await asyncio.sleep(3600)

    async def stop(self, *args):
        await super().stop()
        logger.info("Bot stopped")


app = Bot()

register_user_handlers(app)
register_admin_handlers(app)
register_admin_data_handlers(app)
register_report_handlers(app)
register_trending_handlers(app)
register_group_handlers(app)
register_callback_handlers(app)
register_inline_handlers(app)
register_channel_handlers(app)

if __name__ == "__main__":
    start_server(port=int(os.environ.get("PORT", 10000)))
    app.run()
