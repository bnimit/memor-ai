from __future__ import annotations
import json
import re

def detect_content_type(text: str) -> str:
    """Detect content type: json | log | search | text"""
    
    # Try JSON first
    try:
        json.loads(text)
        return "json"
    except (json.JSONDecodeError, ValueError):
        pass
    
    lines = [line for line in text.split('\n') if line.strip()]
    
    # Check for search results (≥2 lines matching file:line: pattern with actual file extensions)
    # Pattern: filename with extension, colon, number, colon
    search_pattern = re.compile(r'^[^:]+\.\w+:\d+:')
    search_matches = sum(1 for line in lines if search_pattern.match(line))
    if search_matches >= 2:
        return "search"
    
    # Check for log format (≥3 timestamp-like or log-level tokens)
    log_indicators = 0
    log_pattern = re.compile(r'\b(INFO|DEBUG|WARN(ING)?|ERROR|FATAL|CRITICAL|TRACE)\b', re.IGNORECASE)
    timestamp_pattern = re.compile(r'\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}')
    
    for line in lines:
        if log_pattern.search(line) or timestamp_pattern.search(line):
            log_indicators += 1
            if log_indicators >= 3:
                return "log"
    
    return "text"
