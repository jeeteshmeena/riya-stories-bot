import re

# possible fields used in posts - STRICT PATTERNS ONLY
NAME_PATTERNS = [
    # Bullet format with status: "- Story Title ( Completed )"
    r"^\s*-\s*(.+?)\s*\(\s*(Completed?|Complete|Ongoing|ongoing)\s*\)\s*$",
    # Name patterns with status requirement
    r"name\s*[:\-]\s*(.+?)\s*\(\s*(Completed?|Complete|Ongoing|ongoing)\s*\)",
    r"story\s*[:\-]\s*(.+?)\s*\(\s*(Completed?|Complete|Ongoing|ongoing)\s*\)",
    r"title\s*[:\-]\s*(.+?)\s*\(\s*(Completed?|Complete|Ongoing|ongoing)\s*\)",
    r"story name\s*[:\-]\s*(.+?)\s*\(\s*(Completed?|Complete|Ongoing|ongoing)\s*\)",
    # Strict title with status
    r"^\s*(.+?)\s*\(\s*(Completed?|Complete|Ongoing|ongoing)\s*\)\s*$",
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
    Try multiple patterns to detect story name (title).
    EXTREMELY STRICT - only match valid story formats.
    """

    for pattern in NAME_PATTERNS:

        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)

        if match:
            raw = match.group(1).strip()
            # drop any status in parentheses
            cleaned = re.sub(r"\(.*?\)", "", raw).strip()
            if cleaned and len(cleaned) > 2:  # Ensure meaningful title
                return cleaned or None

    # NO FALLBACK - if no pattern matches, it's not a story
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

    # normalised key for lookups
    key = re.sub(r"\s+", " ", name).strip().lower()

    return {
        "name": key,
        "text": name,
        "link": link,
        "message_id": message.id,
        "caption": text,
        "story_type": story_type
    }
