# 🎬 K-Drama Telegram Bot

A high-performance Telegram bot for browsing, streaming, and managing K-Drama content with structured categories, episode navigation, and admin control tools.

---

## 🚀 Features

### 👤 User Features

* Browse shows by category
* View episodes with pagination
* Stream or download episodes
* Support for multi-quality episodes
* Clean UI with inline buttons

### 🔧 Admin Features

* Add shows and seasons
* Upload episodes directly
* Add or update posters
* Delete shows or specific content
* Report handling system

---

## 🧠 System Highlights

* Optimized MongoDB-based storage
* Smart caching layer for fast responses
* Safe file handling (no local storage dependency)
* Persistent media via Telegram storage channel
* Auto cleanup and error-safe execution

---

## 📁 Project Structure

```
bot/
  handlers/        # Command and callback handlers
  services/        # Core logic (shows, uploads, users, etc.)
  utils/           # Helpers (cache, logger, UI, etc.)
  database/        # MongoDB connection
  main.py          # Entry point
  keep_alive.py    # Web server for uptime

requirements.txt
```

---

## ⚙️ Setup

### 1. Clone the repository

```
git clone https://github.com/Devilaiger/k_drama.git
cd k_drama
```

---

### 2. Install dependencies

```
pip install -r requirements.txt
```

---

### 3. Configure environment

Create a `.env` file:

```
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
MONGO_URI=your_mongodb_uri
STORAGE_CHANNEL_ID=your_channel_id
```

---

### 4. Run the bot

```
python -m bot.main
```

---

## ☁️ Deployment

Supports:

* Koyeb
* Render
* VPS

Ensure:

* Environment variables are set
* Bot is admin in storage channel
* `keep_alive.py` is active for uptime

---

## 🔒 Security Notes

* Never commit `.env` files
* Never expose bot token or database URI
* Rotate credentials if exposed

---

## 🧪 Stability

This bot is designed for:

* Long uptime without updates
* Minimal maintenance
* High reliability under load

---

## 📌 Status

✅ Production-ready
✅ Stable release
🔄 Actively maintained

---

## 👤 Author

Developed and maintained by **Devilaiger**

---

## 📜 License

Private / Custom Use
