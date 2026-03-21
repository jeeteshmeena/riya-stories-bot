"""
advanced_scraper.py — Robust description + HD image fetcher
Platforms: Pocket FM, Kuku FM, Headfone, + generic fallback
"""

import os
import re
import io
import json
import logging
import requests
import asyncio
import urllib.parse
from bs4 import BeautifulSoup
from typing import Optional, List, Tuple
from PIL import Image, ImageFilter, ImageEnhance
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# PLATFORM CONFIG
# ─────────────────────────────────────────────────────────────

PLATFORM_DOMAINS = {
    "pocket fm": "pocketfm.com",
    "kuku fm":   "kukufm.com",
    "headfone":  "headfone.co.in",
}

# Known CDN/URL patterns for HD image extraction per platform
PLATFORM_PATTERNS = {
    "pocket fm": {
        # Pocket FM CDN: resize param in URL
        # e.g. https://img.pocketfm.com/xxxx?w=300&q=80  → w=2000&q=100
        "cdn_hosts": ["img.pocketfm.com", "cdn.pocketfm.com", "pocketfm"],
        "param_replacements": [
            (r"[?&]w=\d+", lambda m: m.group(0)[0] + "w=2000"),
            (r"[?&]q=\d+", lambda m: m.group(0)[0] + "q=100"),
            (r"[?&]h=\d+", lambda m: m.group(0)[0] + "h=2000"),
        ]
    },
    "kuku fm": {
        # Kuku FM CDN
        # e.g. https://cdn.kukufm.com/xxxx?width=300  → width=1500
        "cdn_hosts": ["cdn.kukufm.com", "kukufm", "kuku"],
        "param_replacements": [
            (r"[?&]width=\d+",  lambda m: m.group(0)[0] + "width=1500"),
            (r"[?&]height=\d+", lambda m: m.group(0)[0] + "height=1500"),
            (r"[?&]w=\d+",      lambda m: m.group(0)[0] + "w=1500"),
            (r"[?&]q=\d+",      lambda m: m.group(0)[0] + "q=100"),
        ]
    },
    "_generic": {
        # Generic param upgrade for any platform
        "cdn_hosts": [],
        "param_replacements": [
            (r"[?&]w=\d+",      lambda m: m.group(0)[0] + "w=2000"),
            (r"[?&]width=\d+",  lambda m: m.group(0)[0] + "width=2000"),
            (r"[?&]h=\d+",      lambda m: m.group(0)[0] + "h=2000"),
            (r"[?&]height=\d+", lambda m: m.group(0)[0] + "height=2000"),
            (r"[?&]q=\d+",      lambda m: m.group(0)[0] + "q=100"),
            (r"[?&]quality=\d+",lambda m: m.group(0)[0] + "quality=100"),
            (r"[?&]size=\d+x\d+", ""),
        ]
    },
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 11; Pixel 5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.5",
}

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _get_domain(platform: str) -> str:
    lo = platform.lower()
    for key, domain in PLATFORM_DOMAINS.items():
        if key in lo:
            return domain
    return lo.replace(" ", "") + ".com"

def _get_platform_key(platform: str) -> str:
    lo = platform.lower()
    if "pocket" in lo:
        return "pocket fm"
    if "kuku" in lo:
        return "kuku fm"
    return "_generic"

def _upscale_img_url(url: str, platform: str) -> str:
    """Apply platform-specific URL param replacements to get highest resolution."""
    pkey = _get_platform_key(platform)
    replacements = PLATFORM_PATTERNS.get(pkey, {}).get("param_replacements", [])
    # Always also apply generic ones
    generic_replacements = PLATFORM_PATTERNS["_generic"]["param_replacements"]
    all_replacements = replacements + [r for r in generic_replacements if r not in replacements]

    result = url
    for pattern, repl in all_replacements:
        if callable(repl):
            result = re.sub(pattern, repl, result)
        elif repl == "":
            result = re.sub(pattern, "", result)
        else:
            result = re.sub(pattern, repl, result)
    return result

def _serper_search(query: str, api_key: str, endpoint: str = "search") -> dict:
    try:
        resp = requests.post(
            f"https://google.serper.dev/{endpoint}",
            json={"q": query, "gl": "in", "num": 5},
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            timeout=8,
        )
        return resp.json()
    except Exception as e:
        logger.warning(f"[Serper:{endpoint}] Failed: {e}")
        return {}

def _fetch_html(url: str) -> Optional[str]:
    try:
        logger.info(f"[HTTP] GET {url}")
        r = requests.get(url, timeout=8, headers=_HEADERS)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.warning(f"[HTTP] Failed {url}: {e}")
        return None

def _download_image(url: str, timeout: int = 8) -> Optional[bytes]:
    """Download raw image bytes from a URL."""
    try:
        r = requests.get(url, timeout=timeout, headers=_HEADERS, stream=True)
        if r.status_code == 200:
            content_type = r.headers.get("Content-Type", "")
            if "image" in content_type or url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                data = r.content
                if len(data) > 5_000:  # at least 5KB
                    logger.info(f"[IMG] Downloaded {len(data):,} bytes from {url}")
                    return data
                else:
                    logger.warning(f"[IMG] Too small ({len(data)} bytes): {url}")
        else:
            logger.warning(f"[IMG] HTTP {r.status_code} for {url}")
    except Exception as e:
        logger.warning(f"[IMG] Download error [{url}]: {e}")
    return None

def _enhance_image(img_bytes: bytes, platform: str = "") -> bytes:
    """
    Enhance image quality:
    - Convert to RGB
    - Upscale to at least 1200px width using LANCZOS
    - Apply sharpening pass
    - Save at quality=97
    """
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        logger.info(f"[IMG] Original size: {w}x{h}")

        # Always upscale small images
        target_w = 1400
        if w < target_w:
            new_w = target_w
            new_h = int(h * (target_w / w))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            logger.info(f"[IMG] Upscaled to {new_w}x{new_h}")

        # Sharpen
        img = img.filter(ImageFilter.SHARPEN)

        # Slight contrast boost
        img = ImageEnhance.Contrast(img).enhance(1.05)

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=97, optimize=True)
        result = out.getvalue()
        logger.info(f"[IMG] Enhanced: {len(result):,} bytes")
        return result
    except Exception as e:
        logger.error(f"[IMG] Enhance failed: {e}")
        return img_bytes

def _extract_image_urls_from_html(html_text: str, base_url: str, platform: str) -> List[str]:
    """
    Extract candidate image URLs from page HTML.
    Returns list ordered by preference (best first).
    """
    soup = BeautifulSoup(html_text, "html.parser")
    candidates: List[Tuple[int, str]] = []  # (priority, url)

    def _fix_url(src: str) -> str:
        if not src:
            return ""
        src = src.strip()
        if src.startswith("//"):
            return "https:" + src
        if src.startswith("/"):
            base = "/".join(base_url.split("/")[:3])
            return base + src
        if src.startswith("http"):
            return src
        return ""

    # Priority 1: og:image (most reliable)
    for attr, val in [("property", "og:image"), ("property", "og:image:url")]:
        tag = soup.find("meta", attrs={attr: val})
        if tag and tag.get("content"):
            url = _fix_url(tag["content"])
            if url:
                candidates.append((1, url))
                break

    # Priority 2: twitter:image
    for name in ["twitter:image", "twitter:image:src"]:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            url = _fix_url(tag["content"])
            if url:
                candidates.append((2, url))
                break

    # Priority 3: JSON-LD image
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            obj = json.loads(script.string)
            def _find_img(o):
                if isinstance(o, dict):
                    for k in ("image", "thumbnailUrl", "logo"):
                        v = o.get(k)
                        if isinstance(v, str) and v.startswith("http"):
                            candidates.append((3, v))
                        elif isinstance(v, dict):
                            u = v.get("url", "")
                            if u.startswith("http"):
                                candidates.append((3, u))
                    for v in o.values():
                        _find_img(v)
                elif isinstance(o, list):
                    for item in o:
                        _find_img(item)
            _find_img(obj)
        except Exception:
            pass

    # Priority 4: __NEXT_DATA__ image fields
    nd = soup.find("script", id="__NEXT_DATA__")
    if nd and nd.string:
        try:
            obj = json.loads(nd.string)
            raw = json.dumps(obj)
            # Grab all http image URLs
            found = re.findall(r'"(https?://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', raw, re.IGNORECASE)
            for f in found[:10]:
                lo = f.lower()
                if any(kw in lo for kw in ("cover", "poster", "thumbnail", "art", "image", "banner", "show")):
                    candidates.append((4, f))
        except Exception:
            pass

    # Priority 5: <img> tags with cover/poster keywords in src
    for img_tag in soup.find_all("img"):
        src = _fix_url(img_tag.get("src", "") or img_tag.get("data-src", "") or img_tag.get("data-lazy-src", ""))
        if not src:
            continue
        lo = src.lower()
        if any(kw in lo for kw in ("cover", "poster", "banner", "thumb", "art", "show", "series")):
            # Get dimensions if present
            try:
                w = int(img_tag.get("width", 0) or 0)
                h = int(img_tag.get("height", 0) or 0)
                area = w * h
                candidates.append((5 if area > 10000 else 6, src))
            except:
                candidates.append((6, src))

    # Deduplicate preserving order, sorted by priority
    seen = set()
    result = []
    for prio, url in sorted(candidates, key=lambda x: x[0]):
        if url not in seen:
            seen.add(url)
            result.append(url)

    logger.info(f"[IMG] Found {len(result)} candidate image URLs from HTML")
    return result


# ─────────────────────────────────────────────────────────────
# PUBLIC ASYNC FUNCTIONS
# ─────────────────────────────────────────────────────────────

async def extract_story_description(story_name: str, platform_name: str) -> Optional[str]:
    logger.info(f"[DESC] Starting: '{story_name}' / '{platform_name}'")

    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        logger.warning("[DESC] SERPER_API_KEY not set")
        return None

    domain = _get_domain(platform_name)
    query = f"{story_name} site:{domain}"
    logger.info(f"[DESC] Query: {query}")

    data = await asyncio.to_thread(_serper_search, query, api_key)
    urls = [r["link"] for r in data.get("organic", []) if "link" in r]

    if not urls:
        logger.warning("[DESC] No URLs from Serper")
        return None

    for url in urls[:3]:
        html_text = await asyncio.to_thread(_fetch_html, url)
        if not html_text:
            continue
        desc = await asyncio.to_thread(_extract_desc_from_html, html_text)
        if desc:
            logger.info(f"[DESC] Got {len(desc)} chars from {url}")
            return desc

    logger.warning("[DESC] No description found")
    return None


async def extract_hd_image(story_name: str, platform_name: str) -> Optional[bytes]:
    """
    Multi-source HD image extraction:
    1. Serper site-specific search → parse og:image / twitter:image / JSON-LD / img tags
    2. Serper image search fallback
    3. For each candidate URL: try upscaled variant first, then original
    4. Enhance (upscale + sharpen) before returning
    """
    logger.info(f"[IMG] Starting HD fetch: '{story_name}' / '{platform_name}'")

    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        logger.warning("[IMG] SERPER_API_KEY not set")
        return None

    domain = _get_domain(platform_name)
    img_url: Optional[str] = None
    all_candidate_urls: List[str] = []

    # ── Source 1: Serper site:domain search → extract from page HTML ──────────
    query = f"{story_name} site:{domain}"
    logger.info(f"[IMG] Serper query: {query}")
    data = await asyncio.to_thread(_serper_search, query, api_key)
    page_urls = [r["link"] for r in data.get("organic", []) if "link" in r]

    for page_url in page_urls[:3]:  # try top 3 results
        html_text = await asyncio.to_thread(_fetch_html, page_url)
        if not html_text:
            continue
        img_candidates = await asyncio.to_thread(
            _extract_image_urls_from_html, html_text, page_url, platform_name
        )
        if img_candidates:
            logger.info(f"[IMG] Got {len(img_candidates)} candidates from {page_url}")
            all_candidate_urls.extend(img_candidates)
            break  # Got candidates — proceed

    # ── Source 2: Serper image search fallback ────────────────────────────────
    if not all_candidate_urls:
        logger.info("[IMG] No candidates from site HTML — trying Serper image search")
        img_query = f"{story_name} {platform_name} cover poster"
        img_data = await asyncio.to_thread(_serper_search, img_query, api_key, endpoint="images")
        for img_obj in img_data.get("images", [])[:5]:
            u = img_obj.get("imageUrl") or img_obj.get("thumbnailUrl")
            if u:
                all_candidate_urls.append(u)
                logger.info(f"[IMG] Serper image: {u}")

    # ── Source 3: Serper general search, pick any image URL from snippets ─────
    if not all_candidate_urls:
        logger.info("[IMG] Trying general search for any image link")
        gen_query = f"{story_name} {platform_name} audio story cover image"
        gen_data = await asyncio.to_thread(_serper_search, gen_query, api_key)
        for r in gen_data.get("organic", []):
            img_from_og = r.get("imageUrl") or r.get("image")
            if img_from_og:
                all_candidate_urls.append(img_from_og)

    if not all_candidate_urls:
        logger.warning("[IMG] No image candidates found from any source")
        return None

    logger.info(f"[IMG] Total candidates: {len(all_candidate_urls)} — trying each")

    # ── Try each candidate: upscaled variant first, then original ────────────
    def _try_download_best(url: str) -> Optional[bytes]:
        """Try upscaled URL variant, then original. Return raw bytes or None."""
        upscaled = _upscale_img_url(url, platform_name)
        attempts = [upscaled]
        if upscaled != url:
            attempts.append(url)  # original as fallback

        for attempt_url in attempts:
            raw = _download_image(attempt_url)
            if raw:
                return raw
        return None

    for candidate_url in all_candidate_urls[:8]:  # cap at 8 to avoid infinite loop
        raw = await asyncio.to_thread(_try_download_best, candidate_url)
        if raw:
            logger.info(f"[IMG] Successfully downloaded from: {candidate_url}")
            # Enhance: upscale + sharpen
            enhanced = await asyncio.to_thread(_enhance_image, raw, platform_name)
            return enhanced

    logger.warning("[IMG] All candidates failed download")
    return None


# ─── kept for internal use by description fetch ──────────────────────────────
def _extract_desc_from_html(html_text: str) -> Optional[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    candidates: List[str] = []

    # 1. JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            obj = json.loads(script.string)
            def _walk(o):
                if isinstance(o, dict):
                    for k, v in o.items():
                        if k.lower() in ("description", "abstract", "synopsis") and isinstance(v, str) and len(v) > 30:
                            candidates.append(v)
                        _walk(v)
                elif isinstance(o, list):
                    for item in o:
                        _walk(item)
            _walk(obj)
        except Exception:
            pass

    # 2. __NEXT_DATA__
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

    # 4. Content divs
    for div in soup.find_all(["div", "p", "section"],
                             class_=re.compile(r"description|synopsis|about|story.info|content|summary", re.I)):
        t = div.get_text(separator=" ", strip=True)
        if len(t) > 40:
            candidates.append(t)

    best = None
    for c in candidates:
        c = re.sub(r"\s+", " ", c).strip()
        lo = c.lower()
        if any(skip in lo for skip in ("download app", "install app", "sign up", "log in")):
            continue
        if best is None or len(c) > len(best):
            best = c

    return best
