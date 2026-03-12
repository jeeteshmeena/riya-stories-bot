from rapidfuzz import fuzz
from database import load_db


def fuzzy_search(query):
    """
    Search for stories with more accurate matching.
    Uses higher threshold and multiple matching strategies.
    """
    db = load_db()
    
    if not query or len(query.strip()) < 2:
        return None
        
    query = query.lower().strip()
    best = None
    best_score = 0
    
    for name, data in db.items():
        # Get the display text for comparison
        display_text = data.get("text", name).lower().strip()
        
        # Multiple matching strategies
        scores = []
        
        # 1. Exact match (highest priority)
        if query == display_text:
            scores.append(100)
        # 2. Contains match
        elif query in display_text or display_text in query:
            scores.append(85)
        # 3. Startswith match
        elif display_text.startswith(query) or query.startswith(display_text):
            scores.append(75)
        # 4. Fuzzy matching with higher threshold
        else:
            # Use both partial ratio and token set ratio
            partial_score = fuzz.partial_ratio(query, display_text)
            token_score = fuzz.token_set_ratio(query, display_text)
            
            # Only consider if both scores are reasonably high
            if partial_score >= 70 and token_score >= 70:
                scores.append((partial_score + token_score) / 2)
            elif partial_score >= 85:
                scores.append(partial_score)
                
        # Use the best score for this item
        if scores:
            item_score = max(scores)
            if item_score > best_score:
                best_score = item_score
                best = data
                
    # Higher threshold for returning results
    if best_score < 70:
        return None
        
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
