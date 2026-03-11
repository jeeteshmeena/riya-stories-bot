import json
import os
import time
from pathlib import Path

DB_FILE = "stories_db.json"
CLAIMS_FILE = "claims_db.json"
REQUESTS_FILE = "requests_db.json"
SEARCH_INDEX_FILE = "search_index.json"
STORY_INDEX_FILE = "story_index.json"

_DB_CACHE = None
_DB_MTIME = None


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_db():
    """Load main stories database with simple mtime-based caching."""
    global _DB_CACHE, _DB_MTIME

    if not os.path.exists(DB_FILE):
        _DB_CACHE = {}
        _DB_MTIME = None
        return _DB_CACHE

    try:
        mtime = os.path.getmtime(DB_FILE)
    except OSError:
        mtime = None

    if _DB_CACHE is not None and _DB_MTIME == mtime:
        return _DB_CACHE

    _DB_CACHE = _load_json(DB_FILE, {})
    _DB_MTIME = mtime
    return _DB_CACHE


def save_db(data):
    global _DB_CACHE, _DB_MTIME

    _save_json(DB_FILE, data)
    _DB_CACHE = data
    try:
        _DB_MTIME = os.path.getmtime(DB_FILE)
    except OSError:
        _DB_MTIME = None


def add_story(story):
    db = load_db()

    name = story["name"]

    if name not in db:
        db[name] = story
    else:
        # Update if the new story has a higher message_id OR if the link has changed
        # This handles cases where posts are deleted and reposted with corrections
        existing_story = db[name]
        if (story.get("message_id", 0) > existing_story.get("message_id", 0) or 
            story.get("link") != existing_story.get("link")):
            db[name] = story

    save_db(db)


def get_story(name):
    db = load_db()
    return db.get(name)


def remove_stories_not_in(keys_to_keep):
    """Remove from DB any story whose key is not in keys_to_keep. Used after scan to drop deleted posts."""
    global _DB_CACHE
    db = load_db()
    keys_set = set(keys_to_keep)
    removed = [k for k in list(db.keys()) if k not in keys_set]
    for k in removed:
        del db[k]
    if removed:
        save_db(db)
        _DB_CACHE = db
        
        # Also clean up search and story indexes
        search_index = load_search_index()
        story_index = load_story_index()
        
        # Remove from search index
        keys_to_remove = []
        for key, value in search_index.items():
            if value not in keys_to_keep and value not in [db.get(k, {}).get('text', '') for k in keys_to_keep]:
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del search_index[key]
            
        # Remove from story index
        story_index = [name for name in story_index if name in [db.get(k, {}).get('text', '') for k in keys_to_keep]]
        
        # Save updated indexes
        save_search_index(search_index)
        save_story_index(story_index)


# -----------------------
# Persistent claims / requests
# -----------------------

def load_claims():
    return _load_json(CLAIMS_FILE, {})


def save_claims(data):
    _save_json(CLAIMS_FILE, data)


def load_requests():
    raw = _load_json(REQUESTS_FILE, {"requests": {}, "chats": {}})
    requests_raw = raw.get("requests", {})
    chats = raw.get("chats", {})
    requests = {}
    for k, v in requests_raw.items():
        requests[k] = set(v) if isinstance(v, list) else set()
    return {"requests": requests, "chats": chats}


def save_requests(data):
    requests = data.get("requests", {})
    # Convert sets to lists for JSON
    serializable = {}
    for k, v in requests.items():
        serializable[k] = list(v) if isinstance(v, set) else v
    _save_json(REQUESTS_FILE, {"requests": serializable, "chats": data.get("chats", {})})


# -----------------------
# Persistent search index (survives restarts)
# -----------------------

def load_search_index():
    return _load_json(SEARCH_INDEX_FILE, {})


def save_search_index(data):
    _save_json(SEARCH_INDEX_FILE, data)


def load_story_index():
    return _load_json(STORY_INDEX_FILE, [])


def save_story_index(data):
    _save_json(STORY_INDEX_FILE, data)


def load_scan_state():
    """Load the last scan state to support incremental scanning."""
    return _load_json("scan_state.json", {"last_message_id": 0, "last_scan_time": 0})


def save_scan_state(state):
    """Save scan state for incremental scanning."""
    _save_json("scan_state.json", state)


def load_bot_stats():
    """Load bot statistics for status tracking."""
    return _load_json("bot_stats.json", {
        "start_time": time.time(),
        "total_messages_sent": 0,
        "total_requests_received": 0,
        "total_copyright_claims": 0,
        "downtime_start": None,
        "total_downtime": 0
    })


def save_bot_stats(stats):
    """Save bot statistics."""
    _save_json("bot_stats.json", stats)
