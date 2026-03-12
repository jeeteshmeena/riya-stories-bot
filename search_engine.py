from rapidfuzz import fuzz
from database import load_db
import re


def fuzzy_search(query):
    """Improved fuzzy search with stricter matching."""
    db = load_db()
    if not db:
        return None
        
    query = query.lower().strip()
    if len(query) < 3:
        return None
    
    # First try exact match
    if query in db:
        return db[query]
    
    # Try exact match on cleaned titles
    for name, data in db.items():
        title = data.get("text", "").lower().strip()
        if query == title:
            return data
    
    # Fuzzy matching with higher threshold
    best = None
    score = 0
    
    for name, data in db.items():
        # Check both DB key and text field
        text_field = data.get("text", "").lower().strip()
        
        # Calculate scores for both fields
        name_score = fuzz.partial_ratio(query, name)
        text_score = fuzz.partial_ratio(query, text_field)
        
        # Take the higher score
        current_score = max(name_score, text_score)
        
        # Additional check: ensure at least 50% of query words are present
        query_words = set(query.split())
        name_words = set(name.split())
        text_words = set(text_field.split())
        
        word_match_ratio = len(query_words & name_words) / len(query_words) if query_words else 0
        text_word_match_ratio = len(query_words & text_words) / len(query_words) if query_words else 0
        
        # Require both high fuzzy score AND word overlap
        if (current_score > score and 
            current_score >= 70 and  # Higher threshold
            max(word_match_ratio, text_word_match_ratio) >= 0.5):  # At least 50% word overlap
            score = current_score
            best = data
    
    # Only return if we have a good match
    if score >= 70 and best:
        return best
        
    return None
