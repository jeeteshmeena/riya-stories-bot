from rapidfuzz import fuzz
from database import load_db

# We need to peek at the internal MT_TIME cache from database to refresh search cache
import database
import re


def clean_story(name):
    """Local copy of clean_story function to avoid circular imports."""
    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


# We will cache precomputed words, lowering fuzz loops exponentially
_search_cache = []
_search_cache_mtime = None
STOP_WORDS = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "se", "ke", "ki"}

def _get_cache():
    global _search_cache, _search_cache_mtime
    db = load_db()
    
    if not db:
        return []
    
    # Check if DB changed
    if _search_cache_mtime == database._DB_MTIME and len(_search_cache) == len(db):
        return _search_cache

    new_cache = []
    for name, data in db.items():
        story_name = clean_story(data.get("text", data.get("name", name))).lower()
        words = set(story_name.split()) - STOP_WORDS
        new_cache.append({
            "key": name,
            "data": data,
            "story_name": story_name,
            "words": words
        })
        
    _search_cache = new_cache
    _search_cache_mtime = database._DB_MTIME
    return _search_cache


def fuzzy_search(query):
    query = clean_story(query).lower()
    if not query or len(query) < 2:
        return None
    
    query_words = set(query.split()) - STOP_WORDS
    
    cache = _get_cache()
    
    # Filter candidates via set intersection (O(1) lookups)
    candidates = []
    if query_words:
        for item in cache:
            if item["words"] & query_words:
                candidates.append(item)
    
    # Fallback to full list if no tokens matched (e.g. single small word searches)
    if not candidates:
        candidates = cache
        
    best = None
    score = 0
    
    for item in candidates:
        story_name = item["story_name"]
        
        partial_ratio = fuzz.partial_ratio(query, story_name)
        token_sort_ratio = fuzz.token_sort_ratio(query, story_name)
        token_set_ratio = fuzz.token_set_ratio(query, story_name)
        combined_score = max(partial_ratio, token_sort_ratio, token_set_ratio)
        
        if combined_score > score and combined_score >= 90:
            story_words = item["words"]
            if query_words and story_words:
                overlap = len(query_words & story_words) / len(query_words)
                if overlap >= 0.7:
                    score = combined_score
                    best = item["data"]
            elif not query_words and combined_score >= 95:
                # small queries fallback
                score = combined_score
                best = item["data"]
                
    return best


def fuzzy_search_contains(query, limit=10):
    """
    Search for stories containing the query (for suggestions).
    Uses stricter matching to avoid unrelated results.
    """
    if not query or len(query.strip()) < 2:
        return []
        
    query = query.lower().strip()
    results = []
    
    for item in _get_cache():
        display_text = item["story_name"]
        
        # Only include if there's a meaningful match
        if (query in display_text or display_text in query or
            fuzz.partial_ratio(query, display_text) >= 75):
            results.append(item["data"].get("text", item["key"]))
            
    # Sort by relevance and limit
    results.sort(key=lambda x: fuzz.partial_ratio(query, x.lower()), reverse=True)
    return results[:limit]

