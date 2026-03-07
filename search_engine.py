from rapidfuzz import process
from database import load_db

def fuzzy_search(query):

    db = load_db()

    titles = list(db.keys())

    match = process.extractOne(query, titles)

    if not match:
        return None

    score = match[1]

    if score < 60:
        return None

    return db[match[0]]
