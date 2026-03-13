import json
import os

# Paths - use DATA_DIR from config for VPS (load_dotenv runs when config is imported first)
def _data_path(name):
    data_dir = os.getenv("DATA_DIR", ".")
    path = os.path.join(data_dir, name)
    dir_path = os.path.dirname(path)
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)
    return path

DB_FILE = _data_path("stories_db.json")
CLAIMS_FILE = _data_path("claims_db.json")
REQUESTS_FILE = _data_path("requests_db.json")
SEARCH_INDEX_FILE = _data_path("search_index.json")
STORY_INDEX_FILE = _data_path("story_index.json")
LANG_FILE = _data_path("languages_db.json")
COOLDOWN_FILE = _data_path("cooldowns_db.json")
LINK_FLAGS_FILE = _data_path("link_flags.json")
CONFIG_FILE = _data_path("config_db.json")

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


import threading

def _save_json(path, data):
    try:
        serialized = json.dumps(data, indent=2, ensure_ascii=False)
    except Exception:
        # Fallback if json conversion fails or data is weird
        return
    def _write(p, s):
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(s)
        except Exception:
            pass
    threading.Thread(target=_write, args=(path, serialized), daemon=True).start()


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
        # latest message logic
        if story.get("message_id", 0) > db[name].get("message_id", 0):
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


# -----------------------
# Persistent claims / requests
# -----------------------

def load_claims():
    return _load_json(CLAIMS_FILE, {})


def save_claims(data):
    _save_json(CLAIMS_FILE, data)


def load_requests():
    """
    Load requests.
    Legacy format:
      {"requests": {"story": [user_ids]}, "chats": {"story": chat_id}}
    New format:
      {"requests": {"story": {"chat_id": [user_ids], ...}}}
    """
    raw = _load_json(REQUESTS_FILE, {"requests": {}, "chats": {}})
    requests_raw = raw.get("requests", {})
    chats_raw = raw.get("chats", {})
    requests = {}

    for story, value in requests_raw.items():
        if isinstance(value, list):
            # legacy: single chat per story
            chat_id = chats_raw.get(story)
            if chat_id is None:
                continue
            requests[story] = {str(chat_id): set(value)}
        elif isinstance(value, dict):
            inner = {}
            for chat_id, users in value.items():
                inner[str(chat_id)] = set(users) if isinstance(users, list) else set()
            if inner:
                requests[story] = inner

    return {"requests": requests}


def save_requests(data):
    """
    Save requests in new format:
      {"requests": {"story": {"chat_id": [user_ids], ...}}}
    """
    requests = data.get("requests", {})
    serializable = {}
    for story, chats in requests.items():
        ser_chats = {}
        for chat_id, users in chats.items():
            ser_chats[str(chat_id)] = list(users) if isinstance(users, set) else list(users or [])
        serializable[story] = ser_chats
    _save_json(REQUESTS_FILE, {"requests": serializable})


# -----------------------
# Persistent search index (survives restarts)
# -----------------------

def load_search_index():
    """Load search index: {lowercase_key -> original_name}."""
    raw = _load_json(SEARCH_INDEX_FILE, {})
    if isinstance(raw, dict):
        return raw
    return {}


def save_search_index(data):
    _save_json(SEARCH_INDEX_FILE, data)


def load_story_index():
    """Load ordered list of story names for /stories pagination."""
    raw = _load_json(STORY_INDEX_FILE, [])
    if isinstance(raw, list):
        return raw
    return []


def save_story_index(data):
    _save_json(STORY_INDEX_FILE, data)


# -----------------------
# Per-chat language settings
# -----------------------

def load_languages():
    """Return mapping chat_id(str) -> language code ('en' or 'hi')."""
    raw = _load_json(LANG_FILE, {})
    return raw if isinstance(raw, dict) else {}


def save_languages(data):
    _save_json(LANG_FILE, data)


# -----------------------
# Persistent cooldowns (copyright, etc.)
# -----------------------

def load_cooldowns():
    """Return mapping user_id(str) -> {'until': float, 'reason': str}."""
    raw = _load_json(COOLDOWN_FILE, {})
    return raw if isinstance(raw, dict) else {}


def save_cooldowns(data):
    _save_json(COOLDOWN_FILE, data)


# -----------------------
# Link flags (broken reports, etc.)
# -----------------------

def load_link_flags():
    """Return mapping story_key -> {'broken': bool, 'link': str, 'voters': [int], 'chats': [int]}."""
    raw = _load_json(LINK_FLAGS_FILE, {})
    return raw if isinstance(raw, dict) else {}


def save_link_flags(data):
    _save_json(LINK_FLAGS_FILE, data)


# -----------------------
# Bot config (sources, panel settings, etc.)
# -----------------------

def load_config():
    """
    Load high-level bot configuration.
    Keys:
      start_text: str
      force_sub_channels: list
      moderators: list
      auto_delete: dict
      sources: list of extra channel ids
      formats: dict channel_id -> list of format dicts
    """
    default = {
        "start_text": "",
        "force_sub_channels": [],
        "moderators": [],
        "auto_delete": {},
        "sources": [],
        "formats": {},
    }
    raw = _load_json(CONFIG_FILE, default)
    if not isinstance(raw, dict):
        return default
    for k, v in default.items():
        raw.setdefault(k, v)
    return raw


def save_config(data):
    _save_json(CONFIG_FILE, data)


# -----------------------
# User Settings & Features
# -----------------------

USER_SETTINGS_FILE = _data_path("user_settings.json")
LIBRARY_FILE = _data_path("library_db.json")
SUBSCRIPTIONS_FILE = _data_path("subscriptions_db.json")
TRENDING_FILE = _data_path("trending_db.json")

def load_user_settings():
    return _load_json(USER_SETTINGS_FILE, {})

def save_user_settings(data):
    _save_json(USER_SETTINGS_FILE, data)

def load_library():
    return _load_json(LIBRARY_FILE, {})

def save_library(data):
    _save_json(LIBRARY_FILE, data)

def load_subscriptions():
    return _load_json(SUBSCRIPTIONS_FILE, {})

def save_subscriptions(data):
    _save_json(SUBSCRIPTIONS_FILE, data)

def load_trending():
    # mapping of story_key -> hits
    return _load_json(TRENDING_FILE, {})

def save_trending(data):
    _save_json(TRENDING_FILE, data)

