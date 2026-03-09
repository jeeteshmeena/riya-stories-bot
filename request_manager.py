import json
import os

REQUEST_FILE = "requests.json"


def load_requests():

    if os.path.exists(REQUEST_FILE):

        with open(REQUEST_FILE, "r") as f:
            return json.load(f)

    return {}


def save_requests(data):

    with open(REQUEST_FILE, "w") as f:
        json.dump(data, f, indent=2)


def add_request(story, user_id):

    db = load_requests()

    if story not in db:

        db[story] = {
            "count": 0,
            "users": []
        }

    if user_id not in db[story]["users"]:

        db[story]["users"].append(user_id)
        db[story]["count"] += 1

    save_requests(db)

    return db[story]["count"]
