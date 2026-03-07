import json

DB_FILE = "stories_db.json"

def load_db():
    try:
        with open(DB_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

def add_story(story):
    db = load_db()
    db[story["name"].lower()] = story
    save_db(db)

def search_story(query):
    db = load_db()

    for title, data in db.items():
        if query in title:
            return data

    return None
