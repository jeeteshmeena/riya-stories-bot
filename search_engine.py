from rapidfuzz import fuzz
from database import load_db


def fuzzy_search(query):

    db = load_db()

    query = query.lower()

    best = None
    score = 0

    for name,data in db.items():

        s = fuzz.partial_ratio(query, name)

        if s > score:
            score = s
            best = data

    if score < 55:
        return None

    return best
