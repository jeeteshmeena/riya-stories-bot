import re

# possible fields used in posts
NAME_PATTERNS = [
    # Bullet format with status: "- Story Title ( Completed )"
    r"^\s*-\s*(.+?)\s*\(\s*(Completed?|Complete|Ongoing|ongoing)\s*\)\s*$",
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
    Try multiple patterns to detect story name (title).
    We normalise out status markers like (Completed)/(Ongoing).
    """

    for pattern in NAME_PATTERNS:

        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)

        if match:
            raw = match.group(1).strip()
            # drop any status in parentheses
            cleaned = re.sub(r"\(.*?\)", "", raw).strip()
            return cleaned or None

    # fallback: first line of message, but only if it looks like a story title
    lines = text.split("\n")

    if len(lines) > 0:
        first = lines[0].strip()

        # ignore obvious non-story lines
        bad_keywords = [
            "telegram support",
            "copyright",
            "method batao",
            "looking for",
            "new stories chat group",
            "https://",
            "http://",
            "t.me/",
        ]

        lowered = first.lower()
        if any(k in lowered for k in bad_keywords):
            return None

        # require a status marker like (Completed) / (Ongoing) to accept as title
        if re.search(r"\(\s*(completed?|complete|ongoing)\s*\)", first, re.IGNORECASE):
            cleaned = re.sub(r"\(.*?\)", "", first).strip()
            return cleaned or None

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
