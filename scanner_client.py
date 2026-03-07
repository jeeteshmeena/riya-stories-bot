from telethon import TelegramClient

from config import API_ID, API_HASH, SESSION_NAME
from parser import parse_story
from database import add_story


client = TelegramClient(
    SESSION_NAME,
    API_ID,
    API_HASH
)


async def scan_channel(channel_id):

    await client.start()

    total = 0
    stories = 0

    async for message in client.iter_messages(channel_id):

        total += 1

        story = parse_story(message)

        if story:

            add_story(story)

            stories += 1

            print("Story added:", story["name"])

        if total % 100 == 0:

            print(f"Scanned {total} messages | stories {stories}")

    await client.disconnect()

    return {
        "messages": total,
        "stories": stories
    }
