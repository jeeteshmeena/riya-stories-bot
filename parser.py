import re

# possible fields used in posts - relaxed to index more stories
NAME_PATTERNS = [
    # Bullet format with status: "- Story Title ( Completed )"
    r"^\s*-\s*(.+?)\s*\(\s*(Completed?|Complete|Ongoing|ongoing)\s*\)\s*$",
    # Name patterns with or without status
    r"name\s*[:\-]\s*([^\n\(]+)",
    r"story\s*[:\-]\s*([^\n\(]+)",
    r"title\s*[:\-]\s*([^\n\(]+)",
    r"story name\s*[:\-]\s*([^\n\(]+)",
    r"♨️Story\s*[:\-]\s*([^\n\(]+)",
    # Strict title with status (fallback)
    r"^\s*(.+?)\s*\(\s*(Completed?|Complete|Ongoing|ongoing)\s*\)\s*$",
]

LINK_PATTERN = r"https://t\.me/[^\s]+"

TYPE_PATTERN = re.compile(
    r"(Story Type|Type|Genre|🗓Genre)\s*[:\-]\s*(.+)",
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
            # Drop any dangling leading symbols
            cleaned = cleaned.lstrip(":-_!~|> \t")
            if cleaned and len(cleaned) > 2:  # Ensure meaningful title
                return cleaned or None

    # NO FALLBACK - if no pattern matches, it's not a story
    return None


def extract_link(message):
    """
    Get telegram link from message text OR inline keyboard
    """
    text = get_text(message)
    links = []
    if text:
        links = re.findall(LINK_PATTERN, text)
    
    # Check inline keyboard (Telethon)
    if hasattr(message, 'reply_markup') and message.reply_markup:
        try:
            from telethon.tl.types import ReplyInlineMarkup, KeyboardButtonUrl
            if isinstance(message.reply_markup, ReplyInlineMarkup):
                for row in message.reply_markup.rows:
                    for button in row.buttons:
                        if isinstance(button, KeyboardButtonUrl):
                            links.append(button.url)
        except Exception:
            pass

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


def extract_light_format(message):
    text = get_text(message)
    if not text:
        return None

    # Needs photo presence Check - we don't always have photo directly on message depending on framework, 
    # but the requirement states "AND message has photo". Telethon has `message.photo`. 
    if not getattr(message, "photo", None):
        return None

    if not ("♨️" in text and "🗓" in text and "🔰" in text and ":" in text):
        return None

    name_match = re.search(r"♨️.*?:\s*(.+)", text)
    status_match = re.search(r"🔰.*?:\s*(.+)", text)
    platform_match = re.search(r"🖥.*?:\s*(.+)", text)
    genre_match = re.search(r"🗓.*?:\s*(.+)", text)

    if not name_match:
        return None

    name = name_match.group(1).strip()
    status = status_match.group(1).strip() if status_match else "Unknown"
    platform = platform_match.group(1).strip() if platform_match else "Unknown"
    genre = genre_match.group(1).strip() if genre_match else "Unknown"

    description = ""
    if "Story Description:-" in text:
        desc_block = text.split("Story Description:-")[-1]
    elif "Story Description" in text:
        desc_block = text.split("Story Description")[-1]
    else:
        desc_block = ""

    lines = desc_block.split("\n")
    clean_lines = []
    for l in lines:
        if l.strip().startswith(">"):
            clean_lines.append(l.replace(">", "", 1).strip())

    description = " ".join(clean_lines) if clean_lines else desc_block.strip()

    link = None
    if hasattr(message, "reply_markup") and message.reply_markup:
        try:
            from telethon.tl.types import ReplyInlineMarkup, KeyboardButtonUrl
            if isinstance(message.reply_markup, ReplyInlineMarkup):
                for row in message.reply_markup.rows:
                    for btn in row.buttons:
                        if isinstance(btn, KeyboardButtonUrl):
                            link = btn.url
                            break
                    if link:
                        break
        except Exception:
            pass

    key = re.sub(r"\s+", " ", name).strip().lower()

    # Image is handled by scanner_client but we can extract it if possible
    image = None
    
    return {
        "name": key,
        "text": name,
        "status": status,
        "platform": platform,
        "genre": genre,
        "description": description,
        "link": link,
        "image": image,
        "format": "LIGHT",
        "message_id": getattr(message, "id", 0),
        "caption": text,
        "story_type": genre
    }


def parse_story(message):

    # Try Light format first
    light_data = extract_light_format(message)
    if light_data:
        # Require link from somewhere if not in button? Actually light_data might just not have link initially, that's fine
        if not light_data.get("link"):
            light_link = extract_link(message)
            light_data["link"] = light_link
        
        # If no link at all and it's practically required, could return None, but parser shouldn't arbitrarily fail. 
        # But we must have a link to create correct DB entry
        if not light_data.get("link"):
            return None
        return light_data

    text = get_text(message)

    if not text:
        return None

    name = extract_name(text)

    if not name:
        return None

    link = extract_link(message)

    if not link:
        return None

    story_type = extract_story_type(text)

    # normalised key for lookups
    key = re.sub(r"\s+", " ", name).strip().lower()

    return {
        "name": key,
        "text": name,
        "link": link,
        "message_id": getattr(message, "id", 0),
        "caption": text,
        "story_type": story_type
    }
