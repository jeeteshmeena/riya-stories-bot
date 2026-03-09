import re


def parse_story(message):

    text = message.text or message.caption

    if not text:
        return None

    patterns = [

        r"name\s*:-\s*(.+)",
        r"name\s*:\s*(.+)",
        r"story\s*:-\s*(.+)",
        r"story\s*:\s*(.+)",

    ]

    name = None

    for p in patterns:

        m = re.search(p, text, re.IGNORECASE)

        if m:

            name = m.group(1).strip()

            break

    if not name:
        return None

    links = re.findall(r"https://t\.me/[^\s,]+", text)

    if not links:
        return None

    return {

        "name": name.lower(),
        "text": name,
        "link": links[-1],
        "message_id": message.id

    }
