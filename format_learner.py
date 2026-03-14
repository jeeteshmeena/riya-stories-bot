"""
format_learner.py
=================
Automatically analyses a sample Telegram post and produces a "learned template"
that describes how to extract story fields (title, link, status, description,
image) from similar future posts in the same channel.

The learned template is a plain dict stored in config_db.json under:
  bot_config["learned_formats"][str(channel_id)] = [template_dict, ...]

Template dict fields
--------------------
{
  "title_pattern": str | None,   # regex that captures group 1 = title
  "link_pattern":  str | None,   # regex that captures group 1 = link
  "status_pattern": str | None,  # regex that captures group 1 = status
  "desc_pattern":  str | None,   # regex that captures group 1 = description
  "has_media": bool,             # whether posts are expected to have a photo
  "required_keywords": [str],    # words that MUST appear for the post to match
  "sample_text": str,            # first 400 chars of the sample (for display)
  "label": str,                  # human-readable short name
}
"""

from __future__ import annotations

import re
from typing import Any

# ─── helpers ──────────────────────────────────────────────────────────────────

TELEGRAM_LINK_RE = re.compile(r"https?://t\.me/\S+", re.IGNORECASE)

# Common field labels found in structured story posts (case-insensitive)
_FIELD_LABELS = {
    "title":       ["story", "stories", "title", "name", "story name"],
    "status":      ["status", "type", "story type", "genre"],
    "description": ["description", "story description", "about", "summary", "detail"],
}

# Words that almost certainly indicate a structured story post
_STORY_KEYWORDS = [
    r"\bstory\b", r"\btitle\b", r"\blink\b", r"\bstatus\b",
    r"\bdescription\b", r"https://t\.me/", r"\bjoined?\b",
    r"\bpart\b", r"\bepisode\b", r"\bchapter\b",
]


def _get_text(message) -> str | None:
    """Extract plain text from a Telethon or PTB message object."""
    for attr in ("message", "text", "caption"):
        val = getattr(message, attr, None)
        if val:
            return str(val)
    return None


def _build_label_regex(labels: list[str]) -> str:
    """Build a regex that matches any of the given field labels."""
    escaped = [re.escape(l) for l in sorted(labels, key=len, reverse=True)]
    return "(?:" + "|".join(escaped) + ")"


def _find_field_pattern(text: str, labels: list[str]) -> str | None:
    """
    Search for lines like   "Story : Some Title"  or  "Status - Completed"
    and return a regex pattern (with one capture group) that can re-extract
    the value from similar posts.
    """
    label_re_src = _build_label_regex(labels)
    # Match:  <label>  <sep>  <value until end of line>
    search_re = re.compile(
        rf"^[ \t]*{label_re_src}[ \t]*[:\-–—][ \t]*(.+?)[ \t]*$",
        re.IGNORECASE | re.MULTILINE,
    )
    m = search_re.search(text)
    if not m:
        return None
    # Build a resilient pattern using the matched label verbatim
    matched_label = text[m.start():m.start() + (m.start(1) - m.start())].rstrip(": -–—").strip()
    sep_pattern = r"[ \t]*[:\-\u2013\u2014][ \t]*"
    return rf"^[ \t]*{re.escape(matched_label)}{sep_pattern}(.+?)[ \t]*$"


def _extract_required_keywords(text: str) -> list[str]:
    """
    Pick 2-4 unique words/patterns that must appear for a post to be
    considered matching.  We prefer field-label words because they're
    reliable structural markers.
    """
    keywords: list[str] = []
    for labels in _FIELD_LABELS.values():
        pattern = _build_label_regex(labels)
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            keywords.append(m.group(0).lower())
        if len(keywords) >= 3:
            break
    # Always require a telegram link if present
    if TELEGRAM_LINK_RE.search(text):
        keywords.append("t.me/")
    return list(dict.fromkeys(keywords))  # deduplicate, preserve order


def learn_format(message, channel_id: int | str) -> dict[str, Any]:
    """
    Analyse *message* (a Telethon message) and return a learned template dict.
    Returns a template even if only partial information was found; callers
    should check `template["title_pattern"]` at minimum.
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
            "has_media": has_media,
            "required_keywords": [],
            "sample_text": sample_text,
            "label": f"ch{channel_id}_fmt",
            "channel_id": str(channel_id),
        }

    title_pattern  = _find_field_pattern(text, _FIELD_LABELS["title"])
    status_pattern = _find_field_pattern(text, _FIELD_LABELS["status"])
    desc_pattern   = _find_field_pattern(text, _FIELD_LABELS["description"])

    # Link: just use the standard t.me pattern
    link_pattern = TELEGRAM_LINK_RE.pattern if TELEGRAM_LINK_RE.search(text) else None

    required_keywords = _extract_required_keywords(text)

    # Also look for button-entity links if text lacks explicit t.me line
    if not link_pattern and has_media:
        # Media posts sometimes only have the link in a reply-button; we still
        # record the media flag and rely on the scanner to pull from entities.
        pass

    label = f"ch{channel_id}_fmt_{abs(hash(sample_text[:80])) % 9999:04d}"

    return {
        "title_pattern":   title_pattern,
        "link_pattern":    link_pattern,
        "status_pattern":  status_pattern,
        "desc_pattern":    desc_pattern,
        "has_media":       has_media,
        "required_keywords": required_keywords,
        "sample_text":     sample_text,
        "label":           label,
        "channel_id":      str(channel_id),
    }


def extract_with_template(text: str, template: dict[str, Any]) -> dict[str, Any] | None:
    """
    Apply a learned *template* to *text*.  Returns a dict with detected
    fields, or None if the text does not appear to match the template.

    Matching rules (ALL must pass):
      1. Every required keyword must be present (case-insensitive substring).
      2. title_pattern must produce a non-empty capture group.
    """
    if not text or not template:
        return None

    # ── 1. Required keywords guard ──────────────────────────────────────────
    for kw in template.get("required_keywords", []):
        if kw.lower() not in text.lower():
            return None

    # ── 2. Extract title ────────────────────────────────────────────────────
    title: str | None = None
    tp = template.get("title_pattern")
    if tp:
        m = re.search(tp, text, re.IGNORECASE | re.MULTILINE)
        if m and m.groups():
            # Clean up common formatting
            raw = m.group(1).strip()
            raw = re.sub(r"\(.*?\)", "", raw).strip()  # remove (status in parens)
            title = raw or None

    # Title is mandatory — if we can't find it, this post doesn't match
    if not title:
        return None

    # ── 3. Extract link ─────────────────────────────────────────────────────
    link: str | None = None
    lp = template.get("link_pattern")
    if lp:
        m2 = re.search(lp, text, re.IGNORECASE)
        if m2:
            link = m2.group(0).strip() if not m2.groups() else m2.group(1).strip()

    # ── 4. Extract status ────────────────────────────────────────────────────
    status: str | None = None
    sp = template.get("status_pattern")
    if sp:
        m3 = re.search(sp, text, re.IGNORECASE | re.MULTILINE)
        if m3 and m3.groups():
            status = m3.group(1).strip()

    # ── 5. Extract description ───────────────────────────────────────────────
    description: str | None = None
    dp = template.get("desc_pattern")
    if dp:
        m4 = re.search(dp, text, re.IGNORECASE | re.MULTILINE)
        if m4 and m4.groups():
            description = m4.group(1).strip()

    # Normalised key
    key = re.sub(r"\s+", " ", title).strip().lower()

    return {
        "name":        key,
        "text":        title,
        "link":        link,
        "status":      status,
        "description": description,
    }


def matches_template(text: str, template: dict[str, Any]) -> bool:
    """Quick check: does *text* pass the template's matching rules?"""
    return extract_with_template(text, template) is not None


def build_preview(template: dict[str, Any], sample_text: str | None = None) -> str:
    """
    Build a human-readable preview string of the learned template,
    suitable for sending in a Telegram message.
    """
    lines = [
        "<b>★ Learned Format Preview</b>",
        "━━━━━━━━━━━━━━━━\n",
    ]
    label = template.get("label", "—")
    lines.append(f"✦ <b>Label:</b> <code>{label}</code>")
    lines.append(f"✦ <b>Has Media:</b> {'Yes' if template.get('has_media') else 'No'}")

    def _field(name: str, key: str):
        val = template.get(key)
        if val:
            # Show just a short excerpt of the regex
            excerpt = (val[:60] + "…") if len(val) > 60 else val
            lines.append(f"✦ <b>{name}:</b> <code>{excerpt}</code>")
        else:
            lines.append(f"✧ <b>{name}:</b> <i>not detected</i>")

    _field("Title Pattern",  "title_pattern")
    _field("Link Pattern",   "link_pattern")
    _field("Status Pattern", "status_pattern")
    _field("Desc Pattern",   "desc_pattern")

    kws = template.get("required_keywords", [])
    if kws:
        lines.append(f"✦ <b>Keywords:</b> {', '.join(kws[:5])}")

    if sample_text:
        excerpt = (sample_text[:200] + "…") if len(sample_text) > 200 else sample_text
        lines.append(f"\n<b>✧ Sample Text:</b>\n<blockquote>{excerpt}</blockquote>")

    return "\n".join(lines)


def build_test_result(text: str, template: dict[str, Any]) -> str:
    """
    Apply template to text and show a nicely formatted result for
    the admin's Test Format feature.
    """
    result = extract_with_template(text, template)
    if result is None:
        return (
            "<b>☆ Test Result: No Match</b>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "✧ <i>This message does not match the stored template.</i>\n"
            "✧ <i>It would be ignored during scan.</i>"
        )

    lines = [
        "<b>★ Test Result: Matched</b>",
        "━━━━━━━━━━━━━━━━\n",
        f"✦ <b>Title:</b> {result.get('text') or '—'}",
        f"✦ <b>Link:</b> {result.get('link') or '<i>not found</i>'}",
        f"✦ <b>Status:</b> {result.get('status') or '—'}",
        f"✦ <b>Description:</b> {result.get('description') or '—'}",
    ]
    return "\n".join(lines)
