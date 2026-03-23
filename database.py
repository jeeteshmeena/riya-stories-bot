import json
import os
import unicodedata

def normalize_text(text):
    if not text:
        return ""
    
    # Manual un-fancy for small-caps which bypass standard Unicode decomposition
    small_caps = {
        'ꜱ':'s', 'ɪ':'i', 'ʟ':'l', 'ᴇ':'e', 'ɴ':'n', 'ᴛ':'t', 'ᴏ':'o', 'ᴠ':'v',
        'ᴀ':'a', 'ʙ':'b', 'ᴄ':'c', 'ᴅ':'d', 'ꜰ':'f', 'ɢ':'g', 'ʜ':'h', 'ᴊ':'j',
        'ᴋ':'k', 'ᴍ':'m', 'ᴘ':'p', 'ǫ':'q', 'ʀ':'r', 'ᴜ':'u', 'ᴡ':'w', 'ʏ':'y', 'ᴢ':'z'
    }
    
    t = str(text)
    for k, v in small_caps.items():
        t = t.replace(k, v)
        
    # Convert unicode fancy fonts to normal (NFKD handles most Math Bold, script, fraktur)
    t = unicodedata.normalize('NFKD', t).lower()
    
    # Keep only alphanumeric and spaces
    t = ''.join(c for c in t if c.isalnum() or c.isspace())
    return ' '.join(t.split())

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
VOTING_FILE = _data_path("voting_db.json")
SEARCH_INDEX_FILE = _data_path("search_index.json")
STORY_INDEX_FILE = _data_path("story_index.json")
LANG_FILE = _data_path("languages_db.json")
COOLDOWN_FILE = _data_path("cooldowns_db.json")
LINK_FLAGS_FILE = _data_path("link_flags.json")
CONFIG_FILE = _data_path("config_db.json")
FAVORITES_FILE = _data_path("favorites_db.json")
STATS_FILE = _data_path("stats_db.json")
SUBS_FILE = _data_path("subs_db.json")
FORMATS_FILE = _data_path("learned_formats.json")

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

    # Ensure normalized_name is fully up-to-date with our fancy fonts logic
    changed = False
    for k, v in _DB_CACHE.items():
        correct_norm = normalize_text(v.get("name", k))
        if v.get("normalized_name") != correct_norm:
            v["normalized_name"] = correct_norm
            changed = True
    
    if changed:
        _save_json(DB_FILE, _DB_CACHE)
        try:
            _DB_MTIME = os.path.getmtime(DB_FILE)
        except OSError:
            pass

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
    # Ensure normalized_name is saved
    story["normalized_name"] = normalize_text(name)

    if name not in db:
        db[name] = story
    else:
        # latest message logic
        if story.get("message_id", 0) > db[name].get("message_id", 0):
            existing_aliases = db[name].get("aliases", [])
            if existing_aliases:
                story["aliases"] = existing_aliases
            db[name] = story
        else:
            # Even if we don't update the story, ensure any new aliases from somewhere are saved? 
            # Well, scanning doesn't bring new aliases.
            pass

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
# New Features Databases
# -----------------------

def load_favorites():
    raw = _load_json(FAVORITES_FILE, {})
    return raw if isinstance(raw, dict) else {}

def save_favorites(data):
    _save_json(FAVORITES_FILE, data)

def load_stats():
    raw = _load_json(STATS_FILE, {"searches": {}, "users": {}, "trending": {}})
    return raw if isinstance(raw, dict) else {"searches": {}, "users": {}, "trending": {}}

def save_stats(data):
    _save_json(STATS_FILE, data)

def load_subs():
    raw = _load_json(SUBS_FILE, [])
    return raw if isinstance(raw, list) else []

def save_subs(data):
    _save_json(SUBS_FILE, data)


# -----------------------
# Learned formats (per-channel auto-detected templates)
# -----------------------

def load_learned_formats() -> dict:
    """
    Returns: { str(channel_id): [template_dict, ...], ... }
    """
    raw = _load_json(FORMATS_FILE, {})
    return raw if isinstance(raw, dict) else {}


def save_learned_formats(data: dict):
    _save_json(FORMATS_FILE, data)


# -----------------------
# Voting system persistence
# -----------------------

def load_voting_db() -> dict:
    """
    Return voting db: {
        "queue": [{"name": str, "requesters": {str(chat_id): [user_ids]}}, ...],
        "polls": {
            str(poll_id): {
                "message_id": int,
                "chat_id": int,
                "options": [str], # story names in order
                "votes": {str(option_index): [user_ids]},
                "created_at": float
            }
        }
    }
    """
    return _load_json(VOTING_FILE, {"queue": [], "polls": {}})

def save_voting_db(data: dict):
    _save_json(VOTING_FILE, data)

