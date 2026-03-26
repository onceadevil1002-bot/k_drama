import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Memory-based short-lived sessions for group interactions
_active_sessions = {}

def create_group_session(chat_id, user_id, context_data, ttl=180):
    """Create a short-lived session for group viewers."""
    expires = datetime.now() + timedelta(seconds=ttl)
    _active_sessions[(chat_id, user_id)] = {
        "data": context_data,
        "expires": expires
    }

def get_group_session(chat_id, user_id):
    """Retrieve and validate a group session."""
    session = _active_sessions.get((chat_id, user_id))
    if not session:
        return None
        
    if datetime.now() > session["expires"]:
        del _active_sessions[(chat_id, user_id)]
        return None
        
    return session["data"]

def clear_expired_sessions():
    """Cleanup routine for expired sessions."""
    now = datetime.now()
    to_del = [k for k, v in _active_sessions.items() if now > v["expires"]]
    for k in to_del:
        del _active_sessions[k]
