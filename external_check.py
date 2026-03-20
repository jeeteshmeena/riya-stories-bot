import os
import requests
import asyncio
from typing import Optional, Dict

ALLOWED_DOMAINS = ["pocketfm.com", "kukufm.com", "pocketnovel.com", "headfone.co.in"]

def _do_search(query: str) -> Optional[Dict]:
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        return {"error": "API key not configured."}

    url = "https://google.serper.dev/search"
    sites = " OR ".join([f"site:{domain}" for domain in ALLOWED_DOMAINS])
    search_query = f"{query} {sites}"
    
    payload = {
        "q": search_query,
        "gl": "in"
    }
    
    headers = {
        'X-API-KEY': api_key,
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e)}

async def verify_story_external(query: str) -> dict:
    data = await asyncio.to_thread(_do_search, query)
    
    if not data or "error" in data:
        err_msg = data.get("error", "Unknown error") if isinstance(data, dict) else "Unknown error"
        return {"status": "error", "message": err_msg}
        
    organic_results = data.get("organic", [])
    banned_words = ["movie", "film", "trailer", "netflix", "download"]
    
    for result in organic_results:
        title = result.get("title", "").lower()
        link = result.get("link", "").lower()
        
        valid_domain = any(domain in link for domain in ALLOWED_DOMAINS)
        if not valid_domain:
            continue
            
        has_banned_word = any(word in title for word in banned_words)
        if has_banned_word:
            continue
            
        platform = "Unknown Platform"
        for domain in ALLOWED_DOMAINS:
            if domain in link:
                clean_domain = domain.replace(".com", "").replace(".co.in", "").capitalize()
                if clean_domain == "Pocketfm":
                    platform = "Pocket FM"
                elif clean_domain == "Kukufm":
                    platform = "Kuku FM"
                elif clean_domain == "Pocketnovel":
                    platform = "Pocket Novel"
                elif clean_domain == "Headfone":
                    platform = "Headfone"
                else:
                    platform = clean_domain
                break
                
        return {
            "status": "found",
            "title": result.get("title", ""),
            "platform": platform,
            "link": result.get("link", "")
        }
        
    return {"status": "not_found"}
