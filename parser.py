import re

def parse_story(message):

    text = None

    # Telethon message
    if hasattr(message, "text") and message.text:
        text = message.text

    # Telegram Bot API message
    elif hasattr(message, "caption") and message.caption:
        text = message.caption

    if not text:
        return None

    name = None
    story_type = None
    link = None

    name_match = re.search(r"Name\s*[:-]\s*(.*)", text, re.IGNORECASE)
    type_match = re.search(r"Story\s*Type\s*[:-]\s*(.*)", text, re.IGNORECASE)
    link_match = re.search(r"https://t\.me/\S+", text)

    if name_match:
        name = name_match.group(1).strip()

    if type_match:
        story_type = type_match.group(1).strip()

    if link_match:
        link = link_match.group(0)

    if not name or not link:
        return None

    return {
        "name": name,
        "type": story_type if story_type else "Unknown",
        "link": link
    }
