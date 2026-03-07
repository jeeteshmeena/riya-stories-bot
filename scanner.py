from config import CHANNEL_ID
from parser import parse_story
from database import add_story

async def scan_channel(bot):

    async for msg in bot.get_chat_history(CHANNEL_ID):

        story = parse_story(msg)

        if story:
            add_story(story)
