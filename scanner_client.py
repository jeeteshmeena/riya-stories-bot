from telegram import Bot
from config import BOT_TOKEN
from parser import parse_story
from database import add_story

bot = Bot(BOT_TOKEN)


async def scan_channel(channel_id):

    total = 0
    stories = 0

    async for message in bot.get_chat_history(channel_id):

        total += 1

        story = parse_story(message)

        if story:

            add_story(story)
            stories += 1

    return {
        "messages": total,
        "stories": stories
    }
