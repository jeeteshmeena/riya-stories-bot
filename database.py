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
