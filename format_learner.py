"""
format_learner.py
=================
Automatically analyses a sample Telegram post and produces a "learned template"
that describes how to extract story fields (title, link, status, description, episode, owner,
image) from similar future posts in the same channel.
"""

from __future__ import annotations

import re
from typing import Any

# ─── helpers ──────────────────────────────────────────────────────────────────

TELEGRAM_LINK_RE = re.compile(r"https?://t\.me/\S+", re.IGNORECASE)

# Field labels found in structured story posts (case-insensitive)
_FIELD_LABELS = {
    "title":       ["story name", "story", "stories", "title", "name", "series"],
    "status":      ["status", "type", "story type", "genre"],
    "description": ["description", "story description", "about", "summary", "detail"],
    "episode":     ["episode", "part", "chapter"],
    "owner":       ["owner", "writer", "author", "credit"]
}

def _get_text(message) -> str | None:
    """Extract plain text from a Telethon or PTB message object."""
    # First check text, then caption, then message (for mock objects)
    for attr in ("text", "caption", "message"):
        val = getattr(message, attr, None)
        if val:
            return str(val)
    return None

def _get_button_url(message) -> str | None:
    """Extract the first t.me/ URL from inline buttons (supports both PTB and Telethon)."""
    # 1. Try PTB
    try:
        rm = getattr(message, "reply_markup", None)
        if rm and hasattr(rm, "inline_keyboard"):
            for row in rm.inline_keyboard:
                for btn in row:
                    url = getattr(btn, "url", None)
                    if url and "t.me/" in url:
                        return url
    except Exception:
        pass
    # 2. Try Telethon
    try:
        rm = getattr(message, "reply_markup", None)
        if rm and hasattr(rm, "rows"):
            for row in rm.rows:
                for btn in getattr(row, "buttons", []):
                    url = getattr(btn, "url", None)
                    if url and "t.me/" in url:
                        return url
    except Exception:
        pass
    return None


def _build_label_regex(labels: list[str]) -> str:
    escaped = [re.escape(l) for l in sorted(labels, key=len, reverse=True)]
    return "(?:" + "|".join(escaped) + ")"


def _find_field_pattern(text: str, labels: list[str], is_multiline: bool = False) -> str | None:
    """
    Search for a field labeled with one of `labels` (ignoring complex symbols/emojis before it).
    """
    label_re = _build_label_regex(labels)
    all_labels_re = _build_label_regex([l for ls in _FIELD_LABELS.values() for l in ls])
    
    if is_multiline:
        # Match label, then capture text on the same or following lines, until the next recognized label or end of string.
        pattern = rf"^.*?({label_re})[^\w\n]*[:\-—=]*\s*(.*?)(?=\n^[^\w\n]*(?:{all_labels_re})[^\w\n]*[:\-—=]|\Z)"
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if m:
            matched_label = m.group(1)
            reg = rf"^[^\w\n]*{re.escape(matched_label)}[^\w\n]*[:\-—=]*\s*(.*?)(?=\n^[^\w\n]*(?:" + all_labels_re + r")[^\w\n]*[:\-—=]|\Z)"
            return reg
    else:
        # Single-line match
        pattern = rf"^.*?({label_re})[^\w\n]*[:\-—=]*[ \t]*(.+?)$"
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if m:
            matched_label = m.group(1)
            reg = rf"^[^\w\n]*{re.escape(matched_label)}[^\w\n]*[:\-—=]*[ \t]*(.+?)$"
            return reg
    return None


def _extract_required_keywords(text: str) -> list[str]:
    keywords: list[str] = []
    for labels in _FIELD_LABELS.values():
        pattern = _build_label_regex(labels)
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            keywords.append(m.group(0).lower())
        if len(keywords) >= 3:
            break
    # Link is handled separately (we don't strictly require "t.me/" in text if there's an inline button URL)
    return list(dict.fromkeys(keywords)) 


def learn_format(message, channel_id: int | str) -> dict[str, Any]:
    """
    Analyse *message* and return a learned template dict with specific regexes.
    Returns a template even if only partial information was found.
    """
    text = _get_text(message)
    has_media = bool(getattr(message, "photo", None))
    sample_text = (text or "")[:500]

    if not text:
        return {
            "title_pattern": None,
            "link_pattern": None,
            "status_pattern": None,
            "desc_pattern": None,
            "episode_pattern": None,
            "owner_pattern": None,
            "has_media": has_media,
            "required_keywords": [],
            "sample_text": sample_text,
            "label": f"ch{channel_id}_fmt",
            "channel_id": str(channel_id),
        }

    title_pattern   = _find_field_pattern(text, _FIELD_LABELS["title"])
    status_pattern  = _find_field_pattern(text, _FIELD_LABELS["status"])
    desc_pattern    = _find_field_pattern(text, _FIELD_LABELS["description"], is_multiline=True)
    episode_pattern = _find_field_pattern(text, _FIELD_LABELS["episode"])
    owner_pattern   = _find_field_pattern(text, _FIELD_LABELS["owner"])

    # Attempt to find link straight away
    link_pattern = TELEGRAM_LINK_RE.pattern if TELEGRAM_LINK_RE.search(text) else None
    button_url = _get_button_url(message)
    if not link_pattern and button_url:
        # We know there's a button URL for the sample at least.
        # We don't save a regex for it; extract_with_template will just check buttons dynamically.
        pass

    required_keywords = _extract_required_keywords(text)

    label = f"ch{channel_id}_fmt_{abs(hash(sample_text[:80])) % 9999:04d}"

    return {
        "title_pattern":     title_pattern,
        "link_pattern":      link_pattern,
        "status_pattern":    status_pattern,
        "desc_pattern":      desc_pattern,
        "episode_pattern":   episode_pattern,
        "owner_pattern":     owner_pattern,
        "has_media":         has_media,
        "required_keywords": required_keywords,
        "sample_text":       sample_text,
        "label":             label,
        "channel_id":        str(channel_id),
    }


def extract_with_template(message, template: dict[str, Any]) -> dict[str, Any] | None:
    """
    Apply a learned *template* to *message*.
    Returns a dict with detected fields, or None if the text does not appear to match the template.
    """
    if not message or not template:
        return None

    text = _get_text(message)
    if not text:
        return None

    # 1. Required keywords guard (must match all learned explicit labels)
    for kw in template.get("required_keywords", []):
        if kw.lower() not in text.lower():
            return None

    # 2. Extract Title
    title: str | None = None
    tp = template.get("title_pattern")
    if tp:
        m = re.search(tp, text, re.IGNORECASE | re.MULTILINE)
        if m and m.groups():
            raw = m.group(1).strip()
            raw = re.sub(r"\(.*?\)", "", raw).strip()
            title = raw or None

    if not title:
        return None  # Title is mandatory

    # 3. Extract Link
    link: str | None = None
    # Check text first
    lp = template.get("link_pattern")
    if lp:
        m2 = re.search(lp, text, re.IGNORECASE)
        if m2:
            link = m2.group(0).strip() if not m2.groups() else m2.group(1).strip()
    # Check inline buttons if no link found in text
    if not link:
        link = _get_button_url(message)

    # 4. Extract Status
    status: str | None = None
    sp = template.get("status_pattern")
    if sp:
        m3 = re.search(sp, text, re.IGNORECASE | re.MULTILINE)
        if m3 and m3.groups():
            status = m3.group(1).strip()

    # 5. Extract Description
    description: str | None = None
    dp = template.get("desc_pattern")
    if dp:
        m4 = re.search(dp, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if m4 and m4.groups():
            description = m4.group(1).strip()

    # 6. Extract Episode
    episode: str | None = None
    ep = template.get("episode_pattern")
    if ep:
        m5 = re.search(ep, text, re.IGNORECASE | re.MULTILINE)
        if m5 and m5.groups():
            episode = m5.group(1).strip()

    # 7. Extract Owner
    owner: str | None = None
    op = template.get("owner_pattern")
    if op:
        m6 = re.search(op, text, re.IGNORECASE | re.MULTILINE)
        if m6 and m6.groups():
            owner = m6.group(1).strip()

    key = re.sub(r"\s+", " ", title).strip().lower()

    return {
        "name":        key,
        "text":        title,
        "link":        link,
        "status":      status,
        "description": description,
        "episode":     episode,
        "owner":       owner
    }


def build_preview(template: dict[str, Any], sample_text: str | None = None) -> str:
    """
    Build a human-readable preview displaying ONLY fields that were extracted/detected.
    """
    lines = [
        "<b>★ Learned Format Preview</b>",
        "━━━━━━━━━━━━━━━━\n",
    ]
    label = template.get("label", "—")
    lines.append(f"✦ <b>Label:</b> <code>{label}</code>")
    lines.append(f"✦ <b>Has Media:</b> {'Yes' if template.get('has_media') else 'No'}")

    # Helper for conditional display
    def _field(name: str, key: str):
        val = template.get(key)
        if val:
            excerpt = (val[:60] + "…") if len(val) > 60 else val
            lines.append(f"✦ <b>{name}:</b> <code>{excerpt}</code>")

    _field("Title Pattern",   "title_pattern")
    _field("Status Pattern",  "status_pattern")
    _field("Episode Pattern", "episode_pattern")
    _field("Owner Pattern",   "owner_pattern")
    _field("Desc Pattern",    "desc_pattern")
    _field("Link Pattern",    "link_pattern")

    if not any(template.get(k) for k in ["link_pattern", "title_pattern", "status_pattern", "desc_pattern", "episode_pattern", "owner_pattern"]):
        lines.append("\n<i>✧ Note: No exact structured fields were detected. The format may not match cleanly.</i>")

    kws = template.get("required_keywords", [])
    if kws:
        lines.append(f"✦ <b>Keywords Added:</b> {', '.join(kws[:5])}")

    if sample_text:
        excerpt = (sample_text[:200] + "…") if len(sample_text) > 200 else sample_text
        lines.append(f"\n<b>✧ Sample Text:</b>\n<blockquote>{excerpt}</blockquote>")

    return "\n".join(lines)


def build_test_result(message, template: dict[str, Any]) -> str:
    """
    Apply template to the test message and display ONLY detected fields.
    """
    result = extract_with_template(message, template)
    if result is None:
        return (
            "<b>☆ Test Result: No Match</b>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "✧ <i>This message does not match the stored template.</i>\n"
            "✧ <i>It would be ignored during scan.</i>"
        )

    lines = [
        "<b>★ Test Result: Matched</b>",
        "━━━━━━━━━━━━━━━━\n"
    ]
    
    # helper for showing data gracefully
    def apd(label: str, val: str | None):
        if val:
            lines.append(f"✦ <b>{label}:</b> {val}")
    
    apd("Title", result.get("text"))
    apd("Episode", result.get("episode"))
    apd("Owner", result.get("owner"))
    apd("Status", result.get("status"))
    
    # Link might be long, so keep it short if needed but usually standard length is fine
    l = result.get("link")
    lines.append(f"✦ <b>Link:</b> {l if l else '<i>not found</i>'}")
    
    # Description might be multi-line
    desc = result.get("description")
    if desc:
        desc_short = (desc[:100] + "…") if len(desc) > 100 else desc
        lines.append(f"✦ <b>Description:</b>\n<blockquote>{desc_short}</blockquote>")
        
    return "\n".join(lines)
