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


def extract_light_pro_format(message):
    """Detect and parse Light Pro format (has 🎬 Episodes field)."""
    text = get_text(message)
    if not text:
        return None

    has_photo = bool(getattr(message, "photo", None))
    # Light Pro has all Light markers PLUS 🎬
    if not (all(x in text for x in ["\u2668\ufe0f", "\ud83d\udd30", "\ud83d\udda5", "\ud83c\udfac"]) and ("\ud83e\udde9" in text or "\ud83d\uddd3" in text) and has_photo):
        return None

    name_match    = re.search(r"^\u2668\ufe0f(?!.*Description).*?:\s*(.+)", text, re.MULTILINE)
    status_match  = re.search(r"\ud83d\udd30.*?:\s*(.+)", text)
    platform_match= re.search(r"\ud83d\udda5.*?:\s*(.+)", text)
    genre_match   = re.search(r"(?:\ud83e\udde9|\ud83d\uddd3).*?:\s*(.+)", text)
    episodes_match= re.search(r"\ud83c\udfac.*?:\s*(.+)", text)

    if not name_match:
        return None

    def _clean(s):
        return re.sub(r"<[^>]+>", "", s).strip()

    name     = _clean(name_match.group(1))
    status   = _clean(status_match.group(1))  if status_match  else "Unknown"
    platform = _clean(platform_match.group(1)) if platform_match else "Unknown"
    genre    = _clean(genre_match.group(1))   if genre_match   else "Unknown"
    episodes = _clean(episodes_match.group(1)) if episodes_match else ""

    # Parse description
    lines = text.split("\n")
    desc_started, desc_lines = False, []
    for l in lines:
        if "Description" in l or "\U0001f4dd" in l:
            desc_started = True
            continue
        if desc_started:
            stripped = l.strip()
            if stripped.startswith(">"):
                desc_lines.append(stripped.lstrip("> ").strip())
            elif stripped:
                desc_lines.append(stripped)
    description = "\n".join(desc_lines).strip()

    # Extract Play Now link only
    link = None
    if getattr(message, "reply_markup", None):
        try:
            if hasattr(message.reply_markup, "inline_keyboard"):
                for row in message.reply_markup.inline_keyboard:
                    for btn in row:
                        btn_txt = btn.text or ""
                        if "play" in btn_txt.lower() or "\u1d18\u029f\u1d00\u028f" in btn_txt:
                            link = btn.url
                            break
                    if link:
                        break
            elif hasattr(message.reply_markup, "rows"):
                for row in message.reply_markup.rows:
                    for btn in row.buttons:
                        btn_text = getattr(btn, "text", "") or ""
                        if "play" in btn_text.lower() or "\u1d18\u029f\u1d00\u028f" in btn_text:
                            link = btn.url
                            break
                    if link:
                        break
        except Exception:
            link = None

    key = re.sub(r"\s+", " ", name).strip().lower()
    return {
        "name": key, "text": name,
        "status": status, "platform": platform,
        "genre": genre, "episodes": episodes,
        "description": description, "link": link, "image": None,
        "format": "LIGHT_PRO",
        "message_id": getattr(message, "id", 0),
        "caption": text, "story_type": genre
    }


def extract_light_format(message):
    text = get_text(message)
    if not text:
        return None

    # photo: works for Telethon (photo object) and any truthy value
    has_photo = bool(getattr(message, "photo", None))
    if not (all(x in text for x in ["♨️", "🔰", "🖥"]) and ("🧩" in text or "🗓" in text) and has_photo):
        return None

    # Name: first ♨️ line ONLY — must NOT be the "Story Description" line
    name_match = re.search(r"^♨️(?!.*Description).*?:\s*(.+)", text, re.MULTILINE)
    status_match = re.search(r"🔰.*?:\s*(.+)", text)
    platform_match = re.search(r"🖥.*?:\s*(.+)", text)
    genre_match = re.search(r"(?:🧩|🗓).*?:\s*(.+)", text)

    if not name_match:
        return None

    def _clean(s):
        """Strip HTML tags and extra whitespace from extracted field."""
        return re.sub(r"<[^>]+>", "", s).strip()

    name     = _clean(name_match.group(1))
    status   = _clean(status_match.group(1)) if status_match else "Unknown"
    platform = _clean(platform_match.group(1)) if platform_match else "Unknown"
    genre    = _clean(genre_match.group(1)) if genre_match else "Unknown"

    lines = text.split("\n")
    desc_started = False
    desc_lines = []

    for l in lines:
        if "Description" in l or "\U0001f4dd" in l:
            desc_started = True
            continue

        if desc_started:
            stripped = l.strip()
            if stripped.startswith(">"):
                desc_lines.append(stripped.lstrip("> ").strip())
            elif stripped:  # also collect plain lines after Description header
                desc_lines.append(stripped)

    description = "\n".join(desc_lines).strip()

    # Extract ONLY the "Play Now" button link; ignore Backup and others
    link = None
    if getattr(message, "reply_markup", None):
        try:
            if hasattr(message.reply_markup, "inline_keyboard"):
                for row in message.reply_markup.inline_keyboard:
                    for btn in row:
                        btn_txt = btn.text or ""
                        if "play" in btn_txt.lower() or "ᴘʟᴀʏ" in btn_txt:
                            link = btn.url
                            break
                    if link:
                        break
            elif hasattr(message.reply_markup, "rows"):
                for row in message.reply_markup.rows:
                    for btn in row.buttons:
                        btn_text = getattr(btn, "text", "") or ""
                        if "play" in btn_text.lower() or "ᴘʟᴀʏ" in btn_text:
                            link = btn.url
                            break
                    if link:
                        break
        except Exception:
            link = None

    key = re.sub(r"\s+", " ", name).strip().lower()

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

    # Try Light Pro first (superset of Light — must come before Light check)
    light_pro_data = extract_light_pro_format(message)
    if light_pro_data:
        if not light_pro_data.get("link"):
            light_pro_data["link"] = extract_link(message)
        if not light_pro_data.get("link"):
            return None
        print("[PARSER] Saved Light Pro story:", light_pro_data.get("text"), "| link:", light_pro_data.get("link"))
        return light_pro_data

    # Try Light format next
    light_data = extract_light_format(message)
    if light_data:
        if not light_data.get("link"):
            light_link = extract_link(message)
            light_data["link"] = light_link
        if not light_data.get("link"):
            return None
        print("[PARSER] Saved Light story:", light_data.get("text"), "| link:", light_data.get("link"))
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
