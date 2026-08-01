from __future__ import annotations
import re

def compress_log(text: str) -> str:
    """Compress log content by keeping important lines and context."""
    lines = text.split('\n')
    
    if len(lines) <= 15:
        return text
    
    # Pattern for important log lines
    important_pattern = re.compile(
        r'(?i)(error|fatal|traceback|exception|failed|CRITICAL)',
        re.IGNORECASE
    )
    
    important_indices = set()
    
    # Find important lines
    for i, line in enumerate(lines):
        if important_pattern.search(line):
            # Keep the important line and ±2 lines of context
            for offset in range(-2, 3):
                idx = i + offset
                if 0 <= idx < len(lines):
                    important_indices.add(idx)
    
    # Always keep first 5 and last 5 lines
    for i in range(min(5, len(lines))):
        important_indices.add(i)
    for i in range(max(0, len(lines) - 5), len(lines)):
        important_indices.add(i)
    
    # Build result
    if not important_indices:
        # No important lines found, keep first/last and sample middle
        result_lines = lines[:5]
        if len(lines) > 10:
            result_lines.append("... [memor: dropped repetitive middle lines] ...")
            result_lines.extend(lines[-5:])
        else:
            result_lines.extend(lines[5:])
        return '\n'.join(result_lines)
    
    # Sort indices to maintain order
    sorted_indices = sorted(important_indices)
    
    result_lines = []
    prev_idx = -2
    
    for idx in sorted_indices:
        if idx > prev_idx + 1:
            # Gap detected
            result_lines.append("... [memor: dropped repetitive INFO/DEBUG lines] ...")
        result_lines.append(lines[idx])
        prev_idx = idx
    
    return '\n'.join(result_lines)
