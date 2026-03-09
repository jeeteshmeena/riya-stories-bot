import re


def parse_story(message):

    text = message.text or message.caption

    if not text:
        return None

    text_lower = text.lower()

    # story name patterns
    patterns = [

        r"name\s*:-\s*(.+)",
        r"name\s*:\s*(.+)",
        r"story\s*:-\s*(.+)",
        r"story\s*:\s*(.+)",

    ]

    story_name = None

    for pattern in patterns:

        match = re.search(pattern, text, re.IGNORECASE)

        if match:

            story_name = match.group(1).strip()

            break

    if not story_name:
        return None

    # find telegram link
    link_match = re.findall(r"https://t\.me/[^\s,]+", text)

    if not link_match:
        return None

    link = link_match[-1]  # latest link

    return {

        "name": story_name.lower(),
        "link": link,
        "text": story_name

    }
