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


# We caching everything inside dicts for O(1) alias/exact lookups
_exact_cache = {}    # clean_query -> data
_alias_cache = {}    # clean_alias -> data
_list_cache = []     # for partial/did_you_mean containing original titles
_search_cache_mtime = None

def _get_cache():
    global _exact_cache, _alias_cache, _list_cache, _search_cache_mtime
    db = load_db()
    
    if not db:
        return {}
    
    if _search_cache_mtime == database._DB_MTIME and len(_exact_cache) == len(db):
        return _exact_cache

    new_exact = {}
    new_alias = {}
    new_list = []
    
    for name, data in db.items():
        # name is already clean and lowered in DB key
        story_name_clean = name 
        new_exact[story_name_clean] = data
        new_list.append(data.get("text", name))
        
        # aliases
        aliases = data.get("aliases", [])
        for al in aliases:
            al_clean = clean_story(al).lower()
            if al_clean:
                new_alias[al_clean] = data
                
    _exact_cache = new_exact
    _alias_cache = new_alias
    _list_cache = new_list
    _search_cache_mtime = database._DB_MTIME
    return _exact_cache


def search_story_exact_or_alias(query):
    """
    1. Exact story title match
    2. Alias match
    """
    _get_cache() # ensure cache is warm
    q = clean_story(query).lower()
    if not q:
        return None
        
    if q in _exact_cache:
        return _exact_cache[q]
        
    if q in _alias_cache:
        return _alias_cache[q]
        
    return None

def get_suggestions(query, limit=5):
    """
    Return similar titles from the database — only shown when the query is
    genuinely close to a known title.

    Rules (ALL must be met for a suggestion to appear):
      - The query must be at least 3 characters long.
      - Either:
          a) Fuzzy similarity (WRatio) is >= 72, OR
          b) The cleaned query is a substring of the title AND the query is
             at least half the length of the title (prevents "secret" matching
             "A Secret Love Story Between Two Strangers").
    """
    _get_cache()
    if not query or len(query.strip()) < 3:
        return []

    q = query.lower().strip()
    qc = clean_story(q).lower()

    results = []
    for title in _list_cache:
        title_lower = title.lower()
        title_clean = clean_story(title).lower()

        # Substring check — only accept if the query covers at least 50% of the title
        # (prevents short common words triggering suggestions for long titles)
        if qc in title_clean:
            if len(qc) >= len(title_clean) * 0.5:
                results.append((title, 100))
                continue

        # High-bar fuzzy similarity
        ratio = fuzz.ratio(qc, title_clean)
        partial = fuzz.partial_ratio(qc, title_clean)
        token_set = fuzz.token_set_ratio(qc, title_clean)
        best = max(ratio, partial, token_set)

        if best >= 72:
            results.append((title, best))

    # Sort by best score descending
    results.sort(key=lambda x: x[1], reverse=True)

    # Remove duplicates, keeping order
    seen = set()
    final = []
    for r, _ in results:
        if r not in seen:
            seen.add(r)
            final.append(r)

    return final[:limit]

