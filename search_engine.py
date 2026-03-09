from rapidfuzz import fuzz
from database import load_db


def fuzzy_search(query):

    db = load_db()

    query = query.lower()

    best_match = None
    best_score = 0

    for name, data in db.items():

        score = fuzz.ratio(query, name)

        if score > best_score:

            best_score = score
            best_match = data

    if best_score < 60:
        return None

    return best_match
