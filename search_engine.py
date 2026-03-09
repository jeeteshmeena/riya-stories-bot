import json
from rapidfuzz import fuzz


DB_FILE = "database.json"


def load_db():

    try:

        with open(DB_FILE) as f:
            return json.load(f)

    except:
        return {}


def fuzzy_search(query):

    db = load_db()

    best = None
    best_score = 0

    for name, data in db.items():

        score = fuzz.ratio(query.lower(), name)

        if score > best_score and score > 70:

            best_score = score
            best = data

    return best
