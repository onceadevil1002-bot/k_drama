"""
Minimal Flask server for uptime monitoring (Koyeb, Render, UptimeRobot).
Runs in a background thread and does not interfere with the Telegram bot.
"""

import logging
import threading
from flask import Flask

logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def index():
    """Health check endpoint."""
    return "✅ K-Drama Bot is running", 200


@app.route("/health")
def health():
    """Extended health check endpoint."""
    return {"status": "ok", "service": "kdrama-bot"}, 200


def start_server(port=10000):
    """
    Start Flask server in a background thread.
    Safe to call multiple times (only starts once if already running).
    
    Args:
        port: Port to run the server on (default: 10000)
    """
    def run():
        try:
            logger.info(f"🌐 Starting keep-alive server on 0.0.0.0:{port}")
            app.run(
                host="0.0.0.0",
                port=port,
                debug=False,
                use_reloader=False,
                threaded=True
            )
        except Exception as e:
            logger.error(f"Keep-alive server error: {e}")
    
    # Start in daemon thread (won't block bot shutdown)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    logger.info(f"✅ Keep-alive server thread started (PID: {threading.current_thread().ident})")
