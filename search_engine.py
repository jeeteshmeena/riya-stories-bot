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
        
        # Require higher score for better accuracy
        if combined_score > score and combined_score >= 75:
            # Additional check: ensure at least some word overlap
            query_words = set(query.split())
            story_words = set(story_name.split())
            
            # Remove common stop words
            stop_words = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "se", "ke", "ki"}
            query_words -= stop_words
            story_words -= stop_words
            
            # Require at least 30% word overlap for meaningful matches
            if query_words and story_words:
                overlap = len(query_words & story_words) / len(query_words)
                if overlap >= 0.3:
                    score = combined_score
                    best = data
    
    return best
