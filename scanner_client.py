import os
import re
import json
import asyncio
from telethon import TelegramClient, events
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_NAME = os.getenv("SESSION_NAME")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

DB_FILE = "stories_db.json"

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2)

def extract_story_data(text):
    name_match = re.search(r"Name\s*:-\s*(.*)", text)
    type_match = re.search(r"Story Type\s*:-\s*(.*)", text)
    link_match = re.search(r"https://t\.me/[^\s]+", text)

    if not name_match or not link_match:
        return None

    return {
        "name": name_match.group(1).strip(),
        "type": type_match.group(1).strip() if type_match else "Unknown",
        "link": link_match.group(0)
    }

async def start_scanner():
    db = load_db()

    @client.on(events.NewMessage(chats=CHANNEL_ID))
    async def handler(event):
        text = event.raw_text
        data = extract_story_data(text)

        if data:
            key = data["name"].lower()
            db[key] = data
            save_db(db)

    await client.start()
    await client.run_until_disconnected()

def get_story(name):
    db = load_db()
    return db.get(name.lower())
