import json
from telethon import TelegramClient
from telethon.sessions import StringSession
from parser import parse_story
from config import API_ID, API_HASH, SESSION_STRING

DB_FILE = "database.json"


def load_db():

    try:

        with open(DB_FILE) as f:
            return json.load(f)

    except:
        return {}


def save_db(data):

    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)


async def scan_channel(channel):

    db = load_db()

    client = TelegramClient(
        StringSession(SESSION_STRING),
        API_ID,
        API_HASH
    )

    await client.start()

    total = 0

    async for msg in client.iter_messages(channel):

        story = parse_story(msg)

        if not story:
            continue

        name = story["name"]

        total += 1

        if name not in db:

            db[name] = story

        else:

            # latest post logic
            if msg.id > db[name]["message_id"]:

                db[name] = story

    save_db(db)

    return {"stories": len(db), "messages": total}
