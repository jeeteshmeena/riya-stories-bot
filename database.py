
import sqlite3
from rapidfuzz import process

DB="riya.db"

def init_db():
    con=sqlite3.connect(DB)
    cur=con.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS stories(
        id INTEGER PRIMARY KEY,
        name TEXT,
        type TEXT,
        link TEXT
    )
    ''')
    con.commit()
    con.close()

def get_stats():
    con=sqlite3.connect(DB)
    cur=con.cursor()
    cur.execute("SELECT COUNT(*) FROM stories")
    count=cur.fetchone()[0]
    con.close()
    return count

def search_story_ai(query):
    con=sqlite3.connect(DB)
    cur=con.cursor()
    cur.execute("SELECT name,type,link FROM stories")
    rows=cur.fetchall()
    con.close()

    if not rows:
        return None

    names=[r[0] for r in rows]
    match=process.extractOne(query,names,score_cutoff=55)

    if not match:
        return None

    name=match[0]

    for r in rows:
        if r[0]==name:
            return r

    return None
