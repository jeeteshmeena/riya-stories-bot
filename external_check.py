"""
external_check.py — /check command backend.

Strategy (no external API key required):
1. Search the local stories DB first (instant).
2. If not found locally, try a lightweight HTTP-based search
   on known platforms via their public search endpoints / URLs.
"""

import asyncio
import re
import urllib.parse
import urllib.request
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

PLATFORM_SEARCH_URLS = {
    "Pocket FM": "https://pocketfm.com/search?q={query}",
    "Kuku FM":   "https://kukufm.com/search?query={query}",
    "Pocket Novel": "https://pocketnovel.com/api/search?keyword={query}",
}

ALLOWED_DOMAINS = [
    "pocketfm.com", "kukufm.com",
    "pocketnovel.com", "headfone.co.in"
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 11; Pixel 5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Mobile Safari/537.36"
    )
}


# ── 1. Local DB lookup ─────────────────────────────────────────────────────────

def _local_lookup(query: str) -> Optional[Dict]:
    """Check the bot's own stories database first."""
    try:
        from database import load_db
        from search_engine import get_suggestions, search_story_exact_or_alias
    except Exception:
        return None

    # Exact/alias match
    result = search_story_exact_or_alias(query)
    if result and result.get("link"):
        platform = result.get("platform") or _detect_platform(result["link"])
        return {
            "status": "found",
            "title": result.get("text") or result.get("name", query),
            "platform": platform,
            "link": result["link"],
            "source": "local_db",
        }

    # Fuzzy suggestions — return the best match if confident enough
    suggestions = get_suggestions(query, limit=1)
    if suggestions:
        db = load_db()
        # Normalise suggestion key
        key = re.sub(r"\s+", " ", suggestions[0]).strip().lower()
        story = db.get(key)
        if story and story.get("link"):
            platform = story.get("platform") or _detect_platform(story["link"])
            return {
                "status": "found",
                "title": story.get("text") or story.get("name", query),
                "platform": platform,
                "link": story["link"],
                "source": "local_db_fuzzy",
            }

    return None


def _detect_platform(link: str) -> str:
    link_lower = link.lower()
    if "pocketfm.com" in link_lower:
        return "Pocket FM"
    if "kukufm.com" in link_lower:
        return "Kuku FM"
    if "pocketnovel.com" in link_lower:
        return "Pocket Novel"
    if "headfone.co.in" in link_lower:
        return "Headfone"
    return "Unknown Platform"


# ── 2. HTTP fallback: Pocket FM search ────────────────────────────────────────

def _http_search_pocketfm(query: str) -> Optional[Dict]:
    """
    Pocket FM has a public search API that doesn't require auth.
    Returns the first matching show if found.
    """
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://pocketfm.com/api/v17/search?text={encoded}&page=0&type=show"
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            import json
            data = json.loads(resp.read())

        shows = (
            data.get("data", {}).get("shows")
            or data.get("shows")
            or data.get("result")
            or []
        )

        if isinstance(shows, list) and shows:
            first = shows[0]
            # Build canonical URL
            show_id = first.get("show_id") or first.get("id", "")
            slug = first.get("show_name_url") or first.get("slug", "")
            if slug:
                link = f"https://pocketfm.com/show/{slug}"
            elif show_id:
                link = f"https://pocketfm.com/show/{show_id}"
            else:
                return None

            title = (
                first.get("show_name")
                or first.get("name")
                or first.get("title")
                or query
            )
            return {
                "status": "found",
                "title": title,
                "platform": "Pocket FM",
                "link": link,
                "source": "pocketfm_api",
            }
    except Exception as e:
        logger.debug("Pocket FM search failed: %s", e)
    return None


def _http_search_kukufm(query: str) -> Optional[Dict]:
    """Kuku FM public search endpoint."""
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://kukufm.com/api/v2.3/channels/search/?q={encoded}&page=1"
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            import json
            data = json.loads(resp.read())

        results = data.get("results", [])
        if results:
            first = results[0]
            slug = first.get("slug", "")
            title = first.get("title") or first.get("name") or query
            link = f"https://kukufm.com/channel/{slug}/" if slug else ""
            if link:
                return {
                    "status": "found",
                    "title": title,
                    "platform": "Kuku FM",
                    "link": link,
                    "source": "kukufm_api",
                }
    except Exception as e:
        logger.debug("Kuku FM search failed: %s", e)
    return None


# ── 3. Serper fallback (only if SERPER_API_KEY is set) ────────────────────────

def _serper_search(query: str) -> Optional[Dict]:
    import os, json, urllib.request
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        return None
    try:
        sites = " OR ".join([f"site:{d}" for d in ALLOWED_DOMAINS])
        payload = json.dumps({"q": f"{query} {sites}", "gl": "in"}).encode()
        req = urllib.request.Request(
            "https://google.serper.dev/search",
            data=payload,
            headers={**_HEADERS, "X-API-KEY": api_key, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        banned = {"movie", "film", "trailer", "netflix", "download"}
        for r in data.get("organic", []):
            link = r.get("link", "").lower()
            title_lower = r.get("title", "").lower()
            if not any(d in link for d in ALLOWED_DOMAINS):
                continue
            if any(w in title_lower for w in banned):
                continue
            return {
                "status": "found",
                "title": r.get("title", query),
                "platform": _detect_platform(link),
                "link": r.get("link", ""),
                "source": "serper",
            }
    except Exception as e:
        logger.debug("Serper search failed: %s", e)
    return None


# ── Public entry point ─────────────────────────────────────────────────────────

def _do_full_search(query: str) -> dict:
    # 1. Local DB (fastest, no network)
    local = _local_lookup(query)
    if local:
        return local

    # 2. Serper — most accurate (Google-powered), runs first if key is configured
    serper = _serper_search(query)
    if serper:
        return serper

    # 3. Pocket FM public API (fallback if no Serper key)
    pfm = _http_search_pocketfm(query)
    if pfm:
        return pfm

    # 4. Kuku FM public API (last fallback)
    kfm = _http_search_kukufm(query)
    if kfm:
        return kfm

    return {"status": "not_found"}


async def verify_story_external(query: str) -> dict:
    try:
        return await asyncio.to_thread(_do_full_search, query)
    except Exception as e:
        logger.error("verify_story_external error: %s", e)
        return {"status": "error", "message": str(e)}
