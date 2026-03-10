import json
import os

DB_FILE = "stories_db.json"

_DB_CACHE = None
_DB_MTIME = None


def load_db():

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

    with open(DB_FILE, "r", encoding="utf-8") as f:
        _DB_CACHE = json.load(f)
        _DB_MTIME = mtime
        return _DB_CACHE


def save_db(data):
    global _DB_CACHE, _DB_MTIME

    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

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
        if story["message_id"] > db[name]["message_id"]:
            db[name] = story

    save_db(db)


def get_story(name):

    db = load_db()
    return db.get(name)
