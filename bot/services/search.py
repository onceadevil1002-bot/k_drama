from typing import List, Dict, Any
from bot.database.mongo import db
from bot.utils.logger import logger, track_performance

@track_performance("search_drama")
async def search_drama(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Enhanced search for dramas with priority-based matching.
    """
    q = (query or "").strip().lower()
    if not q or len(q) < 1:
        return []

    scored_results = {}  # key = show_name, value = (doc, score)
    
    try:
        # Get all shows from MongoDB
        all_shows = await db.shows.find(
            {"show_name": {"$exists": True}},
            {"category": 1, "show_name": 1, "poster": 1, "description": 1}
        ).to_list(length=1000)
        
        if not all_shows:
            return []
        
        # Priority 1: EXACT word match
        for doc in all_shows:
            show_name = doc.get("show_name", "").lower()
            category = doc.get("category", "").lower()
            key = f"{show_name}_{category}"
            
            if show_name == q:
                scored_results[key] = (doc, 100)
            elif q in show_name.split():
                scored_results[key] = (doc, 90)
            elif category == q:
                scored_results[key] = (doc, 85)
        
        # Priority 2: PARTIAL/SUBSTRING matches
        if len(scored_results) < limit:
            for doc in all_shows:
                show_name = doc.get("show_name", "").lower()
                category = doc.get("category", "").lower()
                key = f"{show_name}_{category}"
                
                if key not in scored_results:
                    if q in show_name and len(q) >= 2:
                        scored_results[key] = (doc, 80)
                    elif show_name.startswith(q) and len(q) >= 2:
                        scored_results[key] = (doc, 75)
        
        # Priority 3: FUZZY matching (using difflib as default, or rapidfuzz if available)
        if len(scored_results) < limit:
            try:
                from rapidfuzz import process, fuzz
                doc_list = [doc for doc in all_shows if f"{doc.get('show_name', '').lower()}_{doc.get('category', '').lower()}" not in scored_results]
                
                # We can fuzzy match against "Show Name (Category)" to make each unique
                choices = {f"{doc.get('show_name', '')} ({doc.get('category', '')})": doc for doc in doc_list}
                if choices:
                    matches = process.extract(q, choices.keys(), scorer=fuzz.token_set_ratio, limit=limit)
                    for key_str, score, _ in matches:
                        if score >= 70:
                            doc = choices[key_str]
                            key = f"{doc.get('show_name', '').lower()}_{doc.get('category', '').lower()}"
                            scored_results[key] = (doc, int(score))
            except ImportError:
                from difflib import SequenceMatcher
                for doc in all_shows:
                    show_name = doc.get("show_name", "").lower()
                    category = doc.get("category", "").lower()
                    key = f"{show_name}_{category}"
                    
                    if key not in scored_results:
                        ratio = SequenceMatcher(None, q, show_name).ratio()
                        if ratio >= 0.70:
                            scored_results[key] = (doc, int(ratio * 100))
        
        # Sort by score and return
        sorted_results = sorted(scored_results.values(), key=lambda x: x[1], reverse=True)
        return [doc for doc, _ in sorted_results[:limit]]
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []
