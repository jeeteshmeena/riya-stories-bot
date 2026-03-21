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

def _extract_imgs_from_html(html_text: str, base_url: str) -> List[str]:
    """Returns multiple possible image URLs (og, twitter, img tags)."""
    soup = BeautifulSoup(html_text, "html.parser")
    candidates = []

    # 1. og:image
    for tag in soup.find_all("meta", property="og:image"):
        if tag.get("content"):
            candidates.append(tag["content"])

    # 2. twitter:image
    for tag in soup.find_all("meta", attrs={"name": "twitter:image"}):
        if tag.get("content"):
            candidates.append(tag["content"])

    # 3. <img> tags
    for img in soup.find_all("img"):
        src = img.get("src", "") or img.get("data-src", "")
        if not src or src.startswith("data:"):
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = "/".join(base_url.split("/")[:3]) + src
        candidates.append(src)

    # 4. Filter and replace dimensions
    # Replace anything like w=X to w=1500 or h=X to h=1500+
    # Sort them by prioritizing "og:" and urls containing "cover", "poster"
    cleaned = []
    seen = set()
    for c in candidates:
        if not c.startswith("http"): continue
        # Upgrade resolution implicitly for known typical CDNs
        c = re.sub(r'w=\d+', 'w=1500', c)
        c = re.sub(r'h=\d+', 'h=1500', c)
        c = re.sub(r'q=\d+', 'q=100', c)
        
        if c not in seen:
            seen.add(c)
            cleaned.append(c)

    # Sort so that URLs with 'cover', 'poster', 'art' are first
    def _score(u):
        u_lo = u.lower()
        score = 0
        if "cover" in u_lo: score -= 10
        if "poster" in u_lo: score -= 10
        if "art" in u_lo: score -= 5
        if "thumbnail" in u_lo: score += 5
        if "icon" in u_lo: score += 10
        if "avatar" in u_lo: score += 10
        if "logo" in u_lo: score += 10
        return score

    cleaned.sort(key=_score)
    return cleaned

def _enhance_image(img_bytes: bytes) -> bytes:
    try:
        # User wants "upscaled in quality so that 'image not found' error doesn't occur"
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        # Aggressive upscale if it's too small
        if w < 1000:
            new_w = 1500
            new_h = int(h * (1500 / w))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            img = img.filter(ImageFilter.SHARPEN)
            logger.info(f"Image upscaled from {w}x{h} to {new_w}x{new_h} to improve quality.")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=100)
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

    def _fetch_first_html(api, q):
        import requests
        try:
            r = requests.post("https://google.serper.dev/search", json={"q": q, "gl": "in"}, headers={"X-API-KEY": api, "Content-Type": "application/json"}, timeout=3)
            r.raise_for_status()
            d = r.json()
            organic = d.get("organic", [])
            for res in organic:
                link = res.get("link", "")
                if link:
                    try:
                        # short timeout
                        p = requests.get(link, timeout=3, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                        if p.status_code == 200:
                            return p.text, link
                    except: pass
        except: pass
        return None, None
        
    def _fetch_serper_images(api, q):
        import requests
        try:
            r = requests.post("https://google.serper.dev/images", json={"q": q, "gl": "in"}, headers={"X-API-KEY": api, "Content-Type": "application/json"}, timeout=3)
            if r.status_code == 200:
                return r.json().get("images", [])
        except: pass
        return []

    # Let's fire both searches concurrently for speed
    html_task = asyncio.to_thread(_fetch_first_html, api_key, query)
    img_task = asyncio.to_thread(_fetch_serper_images, api_key, f"{story_name} {platform_name} HD cover art")
    
    html_res, serper_images = await asyncio.gather(html_task, img_task)
    
    candidates = []
    
    # Add Serper Image hits
    if serper_images:
        # Sort by resolution area
        imgs = sorted(serper_images, key=lambda x: x.get("imageWidth", 1) * x.get("imageHeight", 1), reverse=True)
        for im in imgs:
            u = im.get("imageUrl")
            if u: 
                candidates.append(re.sub(r'w=\d+', 'w=1500+', u))
                candidates.append(u)

    # Add HTML parse hits
    html_text, html_url = html_res
    if html_text and html_url:
        page_imgs = _extract_imgs_from_html(html_text, html_url)
        for u in page_imgs:
            if u not in candidates:
                candidates.append(re.sub(r'w=\d+', 'w=1500+', u))
                candidates.append(u)

    if not candidates:
        logger.warning("[IMG] No image candidates found")
        return None

    def _download(u: str) -> Optional[bytes]:
        try:
            r = requests.get(u, timeout=3, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            if r.status_code == 200 and len(r.content) > 2000: # Ensure valid size
                return r.content
        except: pass
        return None

    # Fetch largest valid image
    # We will try the candidates in order (they are mostly sorted by resolution / quality)
    # until one succeeds within a fast timeout.
    
    # Filter duplicates
    seen = set()
    unique_candidates = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique_candidates.append(c)

    # We try top 5
    for c in unique_candidates[:5]:
        logger.info(f"[IMG] Trying candidate: {c}")
        raw = await asyncio.to_thread(_download, c)
        if raw:
            logger.info(f"[IMG] Success: {len(raw)} bytes downloaded")
            enhanced = await asyncio.to_thread(_enhance_image, raw)
            return enhanced

    logger.warning("[IMG] All download attempts failed")
    return None
