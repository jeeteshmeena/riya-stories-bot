import re


def parse_story(message):

    # Telethon message text safely read
    text = message.message

    if not text:
        return None

    # story name patterns
    patterns = [

        r"Name\s*[:-]\s*(.+)",
        r"Story\s*[:-]\s*(.+)",
        r"Title\s*[:-]\s*(.+)"

    ]

    name = None

    for pattern in patterns:

        match = re.search(pattern, text, re.IGNORECASE)

        if match:

            name = match.group(1).strip()
            break

    if not name:
        return None

    # find telegram links
    links = re.findall(r"https://t\.me/[^\s]+", text)

    if not links:
        return None

    return {

        "name": name.lower(),
        "text": name,
        "link": links[-1],
        "message_id": message.id

    }
