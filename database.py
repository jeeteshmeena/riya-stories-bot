import json
import os

DB_FILE = "stories_db.json"
CLAIMS_FILE = "claims_db.json"
REQUESTS_FILE = "requests_db.json"

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


# -----------------------
# Persistent claims / requests
# -----------------------

def load_claims():
    return _load_json(CLAIMS_FILE, {})


def save_claims(data):
    _save_json(CLAIMS_FILE, data)


def load_requests():
    return _load_json(REQUESTS_FILE, {"requests": {}, "chats": {}})


def save_requests(data):
    _save_json(REQUESTS_FILE, data)
