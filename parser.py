import re

# possible fields used in posts
NAME_PATTERNS = [
    r"name\s*[:\-]\s*(.+)",
    r"story\s*[:\-]\s*(.+)",
    r"title\s*[:\-]\s*(.+)",
    r"story name\s*[:\-]\s*(.+)",
]

LINK_PATTERN = r"https://t\.me/[^\s]+"

TYPE_PATTERN = re.compile(
    r"(Story Type|Type|Genre)\s*[:\-]\s*(.+)",
    re.IGNORECASE
)


def get_text(message):
    """
    Safely get text from Telethon message
    """
    if hasattr(message, "message") and message.message:
        return message.message

    if hasattr(message, "text") and message.text:
        return message.text

    if hasattr(message, "caption") and message.caption:
        return message.caption

    return None


def extract_name(text):
    """
    Try multiple patterns to detect story name
    """

    for pattern in NAME_PATTERNS:

        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            return match.group(1).strip()

    # fallback: first line of message
    lines = text.split("\n")

    if len(lines) > 0:
        first = lines[0].strip()

        if len(first) < 60:   # avoid long sentences
            return first

    return None


def extract_link(text):
    """
    Get telegram link from message
    """

    links = re.findall(LINK_PATTERN, text)

    if not links:
        return None

    # return latest link
    return links[-1]


def extract_story_type(text):
    """
    Detect story type / genre from full message text.
    """
    if not text:
        return None

    match = TYPE_PATTERN.search(text)

    if match:
        return match.group(2).strip()

    return None


def parse_story(message):

    text = get_text(message)

    if not text:
        return None

    name = extract_name(text)

    if not name:
        return None

    link = extract_link(text)

    if not link:
        return None

    story_type = extract_story_type(text)

    return {
        "name": name.lower(),
        "text": name,
        "link": link,
        "message_id": message.id,
        "caption": text,
        "story_type": story_type
    }
