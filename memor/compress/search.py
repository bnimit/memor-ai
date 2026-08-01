from __future__ import annotations
import re

def compress_search(text: str) -> str:
    """Compress search results by keeping error/fail lines or top results."""
    lines = text.split('\n')
    
    if len(lines) <= 80:
        return text
    
    # Pattern for error/fail lines
    error_pattern = re.compile(r'(?i)\b(error|fail)\b', re.IGNORECASE)
    
    # Collect error/fail lines
    error_lines = [line for line in lines if error_pattern.search(line)]
    
    if error_lines:
        # Keep error lines (capped at 80)
        return '\n'.join(error_lines[:80])
    
    # Otherwise, keep top 20 lines by length uniqueness heuristic
    # Use length as a proxy for uniqueness/importance
    scored_lines = [(len(line.strip()), line) for line in lines if line.strip()]
    scored_lines.sort(reverse=True)
    
    # Take top 20 by length
    top_lines = [line for _, line in scored_lines[:20]]
    
    # Cap at 80 lines
    return '\n'.join(top_lines[:80])
