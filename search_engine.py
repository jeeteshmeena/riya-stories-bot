from rapidfuzz import fuzz
from database import load_db
import re


def clean_story(name):
    """Local copy of clean_story function to avoid circular imports."""
    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def fuzzy_search(query):
    db = load_db()
    if not db:
        return None
    
    query = clean_story(query).lower()
    if not query or len(query) < 2:
        return None
    
    best = None
    score = 0
    
    for name, data in db.items():
        # Compare against the actual story name, not just the key
        story_name = clean_story(data.get("text", data.get("name", name))).lower()
        
        # Calculate multiple similarity scores for better matching
        partial_ratio = fuzz.partial_ratio(query, story_name)
        token_sort_ratio = fuzz.token_sort_ratio(query, story_name)
        token_set_ratio = fuzz.token_set_ratio(query, story_name)
        
        # Use the best of the three scores
        combined_score = max(partial_ratio, token_sort_ratio, token_set_ratio)
        
        # Require very high score for accuracy
        if combined_score > score and combined_score >= 85:
            # Additional check: ensure at least some word overlap
            query_words = set(query.split())
            story_words = set(story_name.split())
            
            # Remove common stop words
            stop_words = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "se", "ke", "ki"}
            query_words -= stop_words
            story_words -= stop_words
            
            # Require at least 50% word overlap for meaningful matches
            if query_words and story_words:
                overlap = len(query_words & story_words) / len(query_words)
                if overlap >= 0.5:
                    score = combined_score
                    best = data
    
    return best


def fuzzy_search_contains(query, limit=10):
    """
    Search for stories containing the query (for suggestions).
    Uses stricter matching to avoid unrelated results.
    """
    db = load_db()
    
    if not query or len(query.strip()) < 2:
        return []
        
    query = query.lower().strip()
    results = []
    
    for name, data in db.items():
        display_text = data.get("text", name).lower().strip()
        
        # Only include if there's a meaningful match
        if (query in display_text or display_text in query or
            fuzz.partial_ratio(query, display_text) >= 75):
            results.append(data.get("text", name))
            
    # Sort by relevance and limit
    results.sort(key=lambda x: fuzz.partial_ratio(query, x.lower()), reverse=True)
    return results[:limit]
