from telethon import TelegramClient
from config import API_ID, API_HASH, SESSION_NAME
from parser import parse_story
from database import add_story

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

async def scan_channel(channel):

    await client.start()

    async for msg in client.iter_messages(channel, limit=None):

        story = parse_story(msg)

        if story:
            add_story(story)

    await client.disconnect()
