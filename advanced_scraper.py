import os
import io
import urllib.parse
import urllib.request
import logging
import asyncio
from typing import Optional
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 11; Pixel 5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Mobile Safari/537.36"
    )
}

# ─────────────────────────────────────────────
# INTERNAL API FETCHERS
# ─────────────────────────────────────────────

def _search_pocketfm(query: str) -> dict:
    """Fetch structured show data from Pocket FM public search API."""
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://pocketfm.com/api/v17/search?text={encoded}&page=0&type=show"
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=6) as resp:
            import json
            data = json.loads(resp.read())
            
        shows = data.get("data", {}).get("shows") or data.get("shows") or data.get("result") or []
        if shows and isinstance(shows, list):
            first = shows[0]
            desc = first.get("description") or first.get("synopsis") or first.get("show_description")
            img_url = first.get("image_url") or first.get("cover_image") or first.get("thumbnail_url")
            return {"desc": desc, "img_url": img_url}
    except Exception as e:
        logger.debug(f"PocketFM fetch failed for '{query}': {e}")
    return {}

def _search_kukufm(query: str) -> dict:
    """Fetch structured show data from Kuku FM public search API."""
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://kukufm.com/api/v2.3/channels/search/?q={encoded}&page=1"
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=6) as resp:
            import json
            data = json.loads(resp.read())
            
        results = data.get("results", [])
        if results and isinstance(results, list):
            first = results[0]
            desc = first.get("description") or first.get("synopsis")
            img_url = first.get("image") or first.get("cover_image")
            return {"desc": desc, "img_url": img_url}
    except Exception as e:
        logger.debug(f"KukuFM fetch failed for '{query}': {e}")
    return {}

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _enhance_image(img_bytes: bytes) -> bytes:
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        # Aggressive upscale if it's too small
        if w < 1000:
            new_w = 1500
            new_h = int(h * (1500 / w))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            img = img.filter(ImageFilter.SHARPEN)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=100)
        return out.getvalue()
    except Exception as e:
        logger.error(f"Image enhancement failed: {e}")
        return img_bytes

def _download_image(u: str) -> Optional[bytes]:
    try:
        import requests
        r = requests.get(u, timeout=5, headers=_HEADERS)
        if r.status_code == 200 and len(r.content) > 2000:
            return r.content
    except: pass
    return None

# ─────────────────────────────────────────────
# PUBLIC ASYNC FUNCTIONS
# ─────────────────────────────────────────────

async def extract_story_description(story_name: str, platform_name: str) -> Optional[str]:
    """
    Intelligently fetch the official description using reliable platform APIs
    rather than scraping Google.
    """
    plat_lower = platform_name.lower()
    data = {}
    
    # Try PocketFM API if appropriate
    if "pocket fm" in plat_lower or "pocketfm" in plat_lower or "all" in plat_lower:
        data = await asyncio.to_thread(_search_pocketfm, story_name)
        
    # Try KukuFM API if appropriate
    if not data.get("desc") and ("kuku fm" in plat_lower or "kukufm" in plat_lower or "all" in plat_lower):
        data = await asyncio.to_thread(_search_kukufm, story_name)

    desc = data.get("desc")
    if desc and len(desc) > 30:
        import html
        return html.unescape(desc).strip()
        
    logger.warning(f"[DESC] Could not reliably auto-fetch description for: {story_name}")
    return None


async def extract_hd_image(story_name: str, platform_name: str) -> Optional[bytes]:
    """
    Intelligently fetch the official cover art using reliable platform APIs.
    """
    plat_lower = platform_name.lower()
    data = {}
    
    if "pocket fm" in plat_lower or "pocketfm" in plat_lower or "all" in plat_lower:
        data = await asyncio.to_thread(_search_pocketfm, story_name)
        
    if not data.get("img_url") and ("kuku fm" in plat_lower or "kukufm" in plat_lower or "all" in plat_lower):
        data = await asyncio.to_thread(_search_kukufm, story_name)

    img_url = data.get("img_url")
    if img_url:
        import re
        # Request highest quality if it's parameterized
        img_url = re.sub(r'w=\d+', 'w=1500', img_url)
        img_url = re.sub(r'h=\d+', 'h=1500', img_url)
        
        raw_bytes = await asyncio.to_thread(_download_image, img_url)
        if raw_bytes:
            return await asyncio.to_thread(_enhance_image, raw_bytes)

    logger.warning(f"[IMG] Could not reliably auto-fetch cover for: {story_name}")
    return None
