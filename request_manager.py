import json
import os

FILE = "requests.json"


def load():

    if not os.path.exists(FILE):
        return {}

    with open(FILE) as f:
        return json.load(f)


def save(data):

    with open(FILE,"w") as f:
        json.dump(data,f,indent=2)


def add_request(story,user):

    db = load()

    story = story.lower()

    if story not in db:

        db[story] = {
            "count":0,
            "users":[]
        }

    if user.id in db[story]["users"]:
        return "duplicate"

    db[story]["users"].append(user.id)
    db[story]["count"] += 1

    save(db)

    return "ok"
