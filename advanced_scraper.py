import os
import re
import io
import time
import json
import requests
import asyncio
import logging
from bs4 import BeautifulSoup
from typing import Optional, List
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

async def extract_story_description(story_name: str, platform_name: str) -> Optional[str]:
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        return None
        
    query = f"{story_name} site:{platform_name.lower().replace(' ', '')}.com"
    if "headfone" in platform_name.lower():
        query = f"{story_name} site:headfone.co.in"
        
    payload = {"q": query, "gl": "in"}
    headers = {'X-API-KEY': api_key, 'Content-Type': 'application/json'}
    target_urls: List[str] = []
    
    try:
        resp = await asyncio.to_thread(requests.post, "https://google.serper.dev/search", json=payload, headers=headers, timeout=10)
        data = resp.json()
        organic = data.get("organic", [])
        for res in organic:
            if "link" in res:
                target_urls.append(res["link"])
    except Exception as e:
        logger.error(f"advanced_scraper Serper API failed: {e}")
        return None

    if not target_urls:
        return None

    best_desc = None
    best_log = ""

    for target_url in target_urls[:3]: # try up to 3 pages
        try:
            html_resp = await asyncio.to_thread(requests.get, target_url, timeout=15)
            html_resp.raise_for_status()
            soup = BeautifulSoup(html_resp.text, 'html.parser')
        except Exception:
            continue

        desc_candidates: List[tuple[str, str]] = []
        
        # 1. JSON Priority Extraction
        next_data = soup.find('script', id='__NEXT_DATA__')
        if next_data and next_data.string:
            try:
                data = json.loads(next_data.string)
                def find_desc_next(obj):
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k.lower() in ['description', 'summary', 'synopsis'] and isinstance(v, str):
                                desc_candidates.append(('next_data', v))
                            find_desc_next(v)
                    elif isinstance(obj, list):
                        for item in obj: find_desc_next(item)
                find_desc_next(data)
            except: pass
            
        for script in soup.find_all('script', type='application/ld+json'):
            if script.string:
                try:
                    data = json.loads(script.string)
                    def find_desc_ld(obj):
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                if k.lower() in ['description', 'abstract'] and isinstance(v, str):
                                    desc_candidates.append(('ld_json', v))
                                find_desc_ld(v)
                        elif isinstance(obj, list):
                            for item in obj: find_desc_ld(item)
                    find_desc_ld(data)
                except: pass
                
        # 2. Main HTML containers
        for div in soup.find_all('div', class_=re.compile(r'desc|synopsis|summary|story-info', re.I)):
            t = div.get_text(separator=' ', strip=True)
            if t: desc_candidates.append(('div_container', t))
            
        for p in soup.find_all('p'):
            t = p.get_text(separator=' ', strip=True)
            if t: desc_candidates.append(('p_tag', t))
            
        # 3. Meta Tags (Lowest Priority)
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            desc_candidates.append(('meta[name=description]', meta_desc['content'].strip()))
            
        og_desc = soup.find('meta', property='og:description')
        if og_desc and og_desc.get('content'):
            desc_candidates.append(('og:description', og_desc['content'].strip()))
            
        # Validation Logic (No arbitrary character limits used!)
        valid_desc = None
        for source, text in desc_candidates:
            text = re.sub(r'\s+', ' ', text).strip()
            if not text: continue
            
            lower_t = text.lower()
            if "read more" in lower_t or "read full" in lower_t:
                logger.info(f"Rejected {source}: Contains 'read more' truncation flag.")
                continue
            if "..." in text or text.endswith("..") or "…" in text:
                logger.info(f"Rejected {source}: Contains '...' truncation symbol.")
                continue
                
            # Sentence flow bounds mapping:
            valid_endings = ('.', '!', '?', '"', "'", '”', '’')
            if not text.endswith(valid_endings):
                logger.info(f"Rejected {source}: Abrupt sentence chunk ending without punctuation.")
                continue
                
            if "download" in lower_t and "app" in lower_t and len(text) < 100:
                continue # Generic spam
                
            valid_desc = text
            best_log = f"URL: {target_url} | Source Selector: {source}"
            break # Valid full block found! Strict priority respects order.
            
        if valid_desc:
            best_desc = valid_desc
            break
            
    if best_desc:
        logger.info(f"Final Explicit Auto-Desc Fetched: {best_log}")
        return best_desc
        
    return None

async def extract_hd_image(story_name: str, platform_name: str) -> Optional[bytes]:
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key: return None
        
    query = f"{story_name} site:{platform_name.lower().replace(' ', '')}.com"
    if "headfone" in platform_name.lower(): query = f"{story_name} site:headfone.co.in"
    
    payload = {"q": query, "gl": "in"}
    headers = {'X-API-KEY': api_key, 'Content-Type': 'application/json'}
    target_urls: List[str] = []
    try:
        resp = await asyncio.to_thread(requests.post, "https://google.serper.dev/search", json=payload, headers=headers, timeout=10)
        data = resp.json()
        for res in data.get("organic", []):
            if "link" in res: target_urls.append(res["link"])
    except: pass
    
    img_urls: List[str] = []
    if target_urls:
        try:
            html_resp = await asyncio.to_thread(requests.get, target_urls[0], timeout=10)
            if html_resp.status_code == 200:
                soup = BeautifulSoup(html_resp.text, 'html.parser')
                
                og_img = soup.find('meta', property='og:image')
                if og_img and og_img.get('content'): img_urls.append(og_img['content'])
                
                tw_img = soup.find('meta', attrs={'name': 'twitter:image'})
                if tw_img and tw_img.get('content'): img_urls.append(tw_img['content'])
                
                for img in soup.find_all('img'):
                    src = img.get('src')
                    if src and ('cover' in src.lower() or 'poster' in src.lower() or 'art' in src.lower()):
                        if src.startswith('//'): src = 'https:' + src
                        elif src.startswith('/'): src = '/'.join(target_urls[0].split('/')[:3]) + src
                        img_urls.append(src)
        except: pass
        
    img_fallback_query = f"{story_name} {platform_name} cover image"
    payload_img = {"q": img_fallback_query + " site:pocketfm.com OR site:kukufm.com OR site:headfone.co.in", "gl": "in"}
    try:
        resp = await asyncio.to_thread(requests.post, "https://google.serper.dev/images", json=payload_img, headers=headers, timeout=10)
        data = resp.json()
        for img in data.get("images", []):
            if "imageUrl" in img: img_urls.append(img["imageUrl"])
    except: pass
    
    for base_u in img_urls[:10]:
        if not base_u: continue
        variants = [
            re.sub(r'w=\d+', 'w=2000', re.sub(r'width=\d+', 'width=2000', re.sub(r'q=\d+', 'q=100', base_u))),
            base_u
        ]
        if '?' in base_u: variants.append(base_u.split('?')[0])
        
        for u in variants:
            if not u.startswith('http'): continue
            try:
                resp = await asyncio.to_thread(requests.get, u, timeout=10)
                if resp.status_code == 200:
                    return await asyncio.to_thread(_enhance_image, resp.content)
            except: continue
        
    return None

def _enhance_image(img_bytes: bytes) -> bytes:
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        width, height = img.size
        # Scaled properly, skip entirely
        if width >= 800:
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=100)
            return out.getvalue()
            
        # Lightweight Resize+Sharpen ONLY natively
        new_width = 1200
        new_height = int(height * (1200 / width))
        img = img.resize((new_width, new_height), Image.LANCZOS)
        img = img.filter(ImageFilter.SHARPEN)
        
        logger.info("Image Resized and Sharpened cleanly via strictly PIL rules mapping.")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=100)
        return out.getvalue()
    except Exception as e:
        logger.error(f"Image scaling failed: {e}")
        return img_bytes
