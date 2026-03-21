import os
import re
import io
import json
import logging
import requests
import asyncio
from bs4 import BeautifulSoup
from typing import Optional, List
from PIL import Image, ImageFilter
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────

PLATFORM_DOMAINS = {
    "pocket fm": "pocketfm.com",
    "kuku fm": "kukufm.com",
    "headfone": "headfone.co.in",
}

def _get_domain(platform: str) -> str:
    lower = platform.lower()
    for key, domain in PLATFORM_DOMAINS.items():
        if key in lower:
            return domain
    return platform.lower().replace(" ", "") + ".com"

def _serper_search(query: str, api_key: str, endpoint: str = "search") -> dict:
    """Synchronous Serper API call — run via asyncio.to_thread."""
    try:
        resp = requests.post(
            f"https://google.serper.dev/{endpoint}",
            json={"q": query, "gl": "in"},
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            timeout=6,
        )
        return resp.json()
    except Exception as e:
        logger.warning(f"Serper {endpoint} failed: {e}")
        return {}

def _fetch_html(url: str) -> Optional[str]:
    """Synchronous page fetch — run via asyncio.to_thread."""
    try:
        logger.info(f"Fetching URL: {url}")
        resp = requests.get(
            url,
            timeout=6,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RiyaBot/1.0)"},
        )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"Page fetch failed [{url}]: {e}")
        return None

def _extract_desc_from_html(html_text: str) -> Optional[str]:
    """Extract the best description from a page's HTML."""
    soup = BeautifulSoup(html_text, "html.parser")
    candidates: List[str] = []

    # 1. JSON ld+json blocks
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            obj = json.loads(script.string)
            def _walk(o):
                if isinstance(o, dict):
                    for k, v in o.items():
                        if k.lower() in ("description", "abstract", "summary") and isinstance(v, str) and len(v) > 30:
                            candidates.append(v)
                        _walk(v)
                elif isinstance(o, list):
                    for item in o:
                        _walk(item)
            _walk(obj)
        except Exception:
            pass

    # 2. __NEXT_DATA__ JSON block
    nd = soup.find("script", id="__NEXT_DATA__")
    if nd and nd.string:
        try:
            obj = json.loads(nd.string)
            def _walk2(o):
                if isinstance(o, dict):
                    for k, v in o.items():
                        if k.lower() in ("description", "synopsis", "summary") and isinstance(v, str) and len(v) > 30:
                            candidates.append(v)
                        _walk2(v)
                elif isinstance(o, list):
                    for item in o:
                        _walk2(item)
            _walk2(obj)
        except Exception:
            pass

    # 3. Meta tags
    for attr, prop in [("property", "og:description"), ("name", "description"), ("name", "twitter:description")]:
        tag = soup.find("meta", attrs={attr: prop})
        if tag and tag.get("content") and len(tag["content"]) > 20:
            candidates.append(tag["content"])

    # 4. Common content divs / paragraphs
    for selector in [
        {"class_": re.compile(r"description|synopsis|about|story[_-]?info|content|summary", re.I)},
    ]:
        for div in soup.find_all(["div", "p", "section"], **selector):
            t = div.get_text(separator=" ", strip=True)
            if len(t) > 40:
                candidates.append(t)

    # Pick best: longest that isn't obvious UI garbage
    best = None
    for c in candidates:
        c = re.sub(r"\s+", " ", c).strip()
        lo = c.lower()
        if any(skip in lo for skip in ("download app", "install app", "sign up", "log in")):
            continue
        if best is None or len(c) > len(best):
            best = c

    return best

def _extract_img_from_html(html_text: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html_text, "html.parser")

    # 1. og:image
    tag = soup.find("meta", property="og:image")
    if tag and tag.get("content"):
        return tag["content"]

    # 2. twitter:image
    tag = soup.find("meta", attrs={"name": "twitter:image"})
    if tag and tag.get("content"):
        return tag["content"]

    # 3. Largest <img> with keyword in src
    best_url = None
    for img in soup.find_all("img"):
        src = img.get("src", "") or img.get("data-src", "")
        if not src:
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = "/".join(base_url.split("/")[:3]) + src
        lo = src.lower()
        if any(kw in lo for kw in ("cover", "poster", "thumb", "art", "banner")):
            best_url = src
            break

    return best_url

def _enhance_image(img_bytes: bytes) -> bytes:
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        if w < 800:
            new_w = 1200
            new_h = int(h * (1200 / w))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            img = img.filter(ImageFilter.SHARPEN)
            logger.info("Image resized + sharpened (PIL only, not AI).")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=95)
        return out.getvalue()
    except Exception as e:
        logger.error(f"Image enhancement failed: {e}")
        return img_bytes

# ─────────────────────────────────────────────
# PUBLIC ASYNC FUNCTIONS
# ─────────────────────────────────────────────

async def extract_story_description(story_name: str, platform_name: str) -> Optional[str]:
    logger.info(f"[DESC] Starting extraction: '{story_name}' on '{platform_name}'")

    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        logger.warning("[DESC] SERPER_API_KEY not set — skipping auto description fetch")
        return None

    domain = _get_domain(platform_name)
    query = f"{story_name} site:{domain}"
    logger.info(f"[DESC] Serper query: {query}")

    data = await asyncio.to_thread(_serper_search, query, api_key)
    urls = [r["link"] for r in data.get("organic", []) if "link" in r]

    if not urls:
        logger.warning(f"[DESC] No URLs found from Serper for: {query}")
        return None

    logger.info(f"[DESC] Got {len(urls)} URLs — trying top 2")

    for url in urls[:2]:
        html = await asyncio.to_thread(_fetch_html, url)
        if not html:
            continue
        desc = await asyncio.to_thread(_extract_desc_from_html, html)
        if desc:
            logger.info(f"[DESC] Found description ({len(desc)} chars) from: {url}")
            return desc
        else:
            logger.info(f"[DESC] No description extracted from: {url}")

    logger.warning("[DESC] All URLs exhausted — no description found")
    return None


async def extract_hd_image(story_name: str, platform_name: str) -> Optional[bytes]:
    logger.info(f"[IMG] Starting image extraction: '{story_name}' on '{platform_name}'")

    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        logger.warning("[IMG] SERPER_API_KEY not set — skipping auto image fetch")
        return None

    domain = _get_domain(platform_name)
    query = f"{story_name} site:{domain}"

    data = await asyncio.to_thread(_serper_search, query, api_key)
    urls = [r["link"] for r in data.get("organic", []) if "link" in r]

    img_url: Optional[str] = None

    if urls:
        html = await asyncio.to_thread(_fetch_html, urls[0])
        if html:
            img_url = await asyncio.to_thread(_extract_img_from_html, html, urls[0])
            if img_url:
                logger.info(f"[IMG] Found image URL from page HTML: {img_url}")

    # Fallback: Serper image search
    if not img_url:
        logger.info("[IMG] Falling back to Serper image search...")
        img_query = f"{story_name} {platform_name} cover poster"
        img_data = await asyncio.to_thread(_serper_search, img_query, api_key, endpoint="images")
        imgs = img_data.get("images", [])
        if imgs:
            img_url = imgs[0].get("imageUrl")
            logger.info(f"[IMG] Serper image fallback found: {img_url}")

    if not img_url:
        logger.warning("[IMG] No image URL found from any source")
        return None

    # Try high-res variant first, then original
    variants = [
        re.sub(r"w=\d+", "w=2000", re.sub(r"q=\d+", "q=100", img_url)),
        img_url,
    ]

    def _download(u: str) -> Optional[bytes]:
        try:
            r = requests.get(u, timeout=7, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and len(r.content) > 1000:
                return r.content
        except Exception as e:
            logger.warning(f"[IMG] Download failed [{u}]: {e}")
        return None

    for v in variants:
        raw = await asyncio.to_thread(_download, v)
        if raw:
            logger.info(f"[IMG] Downloaded {len(raw)} bytes from {v}")
            enhanced = await asyncio.to_thread(_enhance_image, raw)
            return enhanced

    logger.warning("[IMG] Image download failed for all variants")
    return None
