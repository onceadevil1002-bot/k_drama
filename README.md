# K-Drama Telegram Bot

A Pyrogram-based Telegram bot for browsing and managing drama content with inline navigation, deep links, admin upload tools, required-channel verification, reports, favorites, history, and profile/admin utilities.

## Features

### User Features

- Browse dramas by category with inline buttons.
- Open shows from deep links: `/start category__show_slug`.
- View seasons, episodes, quality options, and split parts.
- Receive video/document/link episodes in private chat.
- Favorites list and watch history.
- Recent updates and request command.
- Inline search support with deep links back into the bot.
- Group/global search command support.

### Admin Features

- Add shows and seasons.
- Import episodes by command and then send video/document/link.
- Add posters for shows.
- Delete shows, seasons, episodes, qualities, and split parts.
- Report search and report management helpers.
- User search, profile inspection, profile photo history, name history, group list, watch history, and common chat views.
- Ban list and unban flow.
- Loading sticker configuration.

## Required Channel Verification

The bot supports one or more required channels through `REQUIRED_CHANNELS`.

Current behavior:

- `/start` performs a live Telegram membership check.
- Live checks are cached per user for 2 minutes to reduce Telegram API usage.
- The `I Joined` button bypasses that cache and verifies live immediately.
- Join buttons are built from channel metadata resolved at startup for faster responses.
- Telegram channel member events are tracked when Telegram sends them.
- A startup scan and midnight scan sync required-channel membership into MongoDB.

Repeated leave handling:

- Warning is sent at 3 leaves.
- Ban is applied at 4 leaves.
- Leave counts are tracked per required channel.
- If Telegram does not send a leave event, `/start` live verification can detect the leave and update the database.

Note: because `/start` uses a 2-minute per-user cache, a user who leaves immediately after a successful `/start` check may not be rechecked by `/start` until that user's cache expires. The `I Joined` button always rechecks live.

## Inline Search Throttling

Telegram sends inline queries on nearly every keystroke. To reduce load:

- Inline search starts only after 3 characters.
- For the same user and same typing run, a new real search is run only after about 3 characters of change.
- Skipped keystrokes are answered quickly without hitting the search service.

## Project Structure

```text
bot/
  database/              MongoDB connection and indexes
  handlers/              User, admin, callback, inline, report, group handlers
  services/              Shows, verification, uploads, users, updates, search
  utils/                 Cache, IDs, UI, logging, backup, slug helpers
  config.py              Environment configuration
  keep_alive.py          HTTP health server
  main.py                Bot entry point

requirements.txt
runtime.txt
Procfile
Dockerfile
```

## Requirements

- Python 3.10+
- Telegram bot token from BotFather
- Telegram API ID and API hash from my.telegram.org
- MongoDB database
- Bot added to:
  - required channel(s)
  - storage channel
- Bot should be admin where it needs to read channel members or manage storage media.

## Environment Variables

Create `bot/.env` or configure these variables in your hosting provider:

```env
API_ID=123456
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token

MONGO_URI=your_mongodb_uri
# mongo_url is also accepted as fallback if MONGO_URI is not set

STORAGE_CHANNEL_ID=-1001234567890

ADMIN_ID=123456789
SECONDARY_ADMIN_ID=987654321
ADMIN_IDS=111111111,222222222

REQUIRED_CHANNELS=@MainChannel,@SecondChannel

PORT=10000
```

Notes:

- `ADMIN_ID`, `SECONDARY_ADMIN_ID`, and comma-separated `ADMIN_IDS` are combined and deduplicated.
- `REQUIRED_CHANNELS` supports comma-separated channel usernames/links supported by Pyrogram `get_chat`.
- Keep `.env` out of git.

## Install

```bash
pip install -r requirements.txt
```

## Run Locally

```bash
python -m bot.main
```

## Deployment

The repo includes common deployment files:

- `Procfile`
- `runtime.txt`
- `Dockerfile`

The bot starts a small health server using `PORT`, then runs the Pyrogram client.

Before deploying:

- Set all required environment variables.
- Confirm MongoDB is reachable.
- Confirm the bot can resolve the storage channel.
- Confirm the bot can resolve every required channel.
- Confirm the bot has enough permissions to read channel membership.

## MongoDB Collections

The bot uses MongoDB for:

- shows and episode metadata
- users and profile/history data
- favorites
- reports
- recent updates
- required-channel membership tracking
- banned users
- config/settings

Indexes are created on startup through the database module.

## Important Commands

User commands include:

```text
/start
/search <name>
/favorites
/recent_updates
/request <name>
/history
/help
```

Admin commands include content import, show management, poster management, report tools, user search/profile tools, ban tools, and settings commands. See `bot/handlers/admin_cmds.py` and `bot/handlers/admin_data_entry.py` for the full command set.

## Verification Checklist

After setup:

1. Start the bot and check logs for successful required-channel resolution.
2. Check logs for successful storage channel resolution.
3. Send `/start` as a new user and confirm join buttons appear.
4. Join all required channels and press `I Joined`.
5. Open a show and confirm navigation works.
6. Test inline search with at least 3 characters.
7. As admin, test one upload/import flow.
8. Leave a required channel and run `/start` after cache expiry to confirm join gate appears.

## Security

- Never commit `.env`.
- Never expose bot token, API hash, or MongoDB URI.
- Keep storage and required channels private/admin-controlled as needed.
- Rotate credentials if they are exposed.

## Status

Production-oriented and actively maintained.

## License

Private / custom use.
