from pyrogram.types import InlineKeyboardButton

def paginate_items(items, page, items_per_page=10):
    """Paginate a list of items."""
    if not items:
        return [], 0
    total_pages = (len(items) + items_per_page - 1) // items_per_page
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    return items[start_idx:end_idx], total_pages

def build_pagination_buttons(cat_id, current_page, total_pages, star_id=None):
    """Build pagination button row."""
    nav_row = []
    if current_page > 1:
        prev_cb = f"page|{cat_id}|{current_page-1}"
        if star_id: prev_cb += f"|{star_id}"
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=prev_cb))
    
    nav_row.append(InlineKeyboardButton(f"📄 {current_page}/{total_pages}", callback_data="noop"))
    
    if current_page < total_pages:
        next_cb = f"page|{cat_id}|{current_page+1}"
        if star_id: next_cb += f"|{star_id}"
        nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=next_cb))
    
    return [nav_row] if nav_row else []
