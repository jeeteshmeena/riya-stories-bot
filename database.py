import json
import os

DB_FILE = "stories_db.json"


def load_db():

    if os.path.exists(DB_FILE):

        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    return {}


def save_db(data):

    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
