"""
Central show name normalization utility.
ALL admin commands (import, delete, poster, search) MUST use this module
for show lookup so that 'Lighter Princess', 'lighter_princess', and
'Lighter_Princess' all resolve to the same database entry.
"""
import re

def normalize_slug(text: str) -> str:
    """
    Convert any show name or user input into a canonical slug for comparison.
    Rules:
    - Lowercase
    - Strip leading/trailing whitespace
    - Replace underscores with spaces (normalize both forms)
    - Collapse multiple spaces
    IMPORTANT: This is for COMPARISON only. The display name in the DB is
    never changed. Only the lookup key is normalized.
    """
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text.strip().lower().replace("_", " "))


def find_show_in_data(data: dict, category: str, query: str) -> str | None:
    """
    Find the canonical show_name (with original casing) inside `data[category]`
    matching the given query using slug normalization. 
    Returns the original show_name from DB, or None if not found.
    
    This is the SINGLE source of truth for show lookups across:
    - import commands
    - delete commands  
    - poster commands
    - search
    """
    if not data or category not in data:
        return None
    
    query_slug = normalize_slug(query)
    
    for show_name in data[category]:
        if normalize_slug(show_name) == query_slug:
            return show_name
    
    return None


def find_category_for_show(data: dict, query: str) -> tuple[str | None, str | None]:
    """
    Search across ALL categories for a show matching query.
    Returns (category, show_name) or (None, None) if not found.
    Use this only when category is ambiguous.
    """
    query_slug = normalize_slug(query)
    for category, shows in data.items():
        for show_name in shows:
            if normalize_slug(show_name) == query_slug:
                return category, show_name
    return None, None
