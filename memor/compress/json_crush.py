from __future__ import annotations
import json
import re

def compress_json(text: str) -> str:
    """Compress JSON by keeping representative samples from large arrays."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text
    
    # Only process top-level arrays
    if not isinstance(data, list):
        return json.dumps(data, separators=(',', ':'))
    
    if len(data) <= 10:
        return json.dumps(data, separators=(',', ':'))
    
    # Pattern for error-ish content
    error_pattern = re.compile(r'(?i)(error|fail|exception|fatal|critical)', re.IGNORECASE)
    
    kept_items = []
    
    # Keep first 3
    kept_items.extend(data[:3])
    
    # Keep error-ish items from middle
    for item in data[3:-2]:
        item_str = json.dumps(item)
        if error_pattern.search(item_str):
            kept_items.append(item)
    
    # Keep last 2
    kept_items.extend(data[-2:])
    
    original_count = len(data)
    kept_count = len(kept_items)
    
    result = json.dumps(kept_items, separators=(',', ':'))
    note = f" /* memor: kept {kept_count} of {original_count} items */"
    
    return result + note
