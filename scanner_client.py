import json
from telethon import TelegramClient
from config import API_ID, API_HASH, SESSION_NAME
from story_parser import extract_story

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

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

    await client.start()

    async for msg in client.iter_messages(channel):

        story = extract_story(msg)

        if not story:
            continue

        name = story["name"]

        if name not in db:

            db[name] = story

        else:

            # latest post logic
            if story["message_id"] > db[name]["message_id"]:

                db[name] = story

    save_db(db)

    return {"stories": len(db)}
