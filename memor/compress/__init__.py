from __future__ import annotations
from memor.compress.types import CompressResult
from memor.compress.detect import detect_content_type
from memor.compress.logs import compress_log
from memor.compress.json_crush import compress_json
from memor.compress.search import compress_search
from memor.compress.text import compress_plain_text
from memor.tokencount import count_tokens

def compress_text(
    text: str, *, content_type: str | None = None, file_path: str | None = None
) -> CompressResult:
    """Compress text based on content type.

    ``file_path`` makes detection evidence-based rather than a guess — see
    ``detect_content_type``.
    """

    tokens_before = count_tokens(text)

    # Detect content type if not provided
    if content_type is None:
        content_type = detect_content_type(text, file_path)
    
    try:
        # Apply appropriate compressor
        if content_type == "log":
            compressed = compress_log(text)
        elif content_type == "json":
            compressed = compress_json(text)
        elif content_type == "search":
            compressed = compress_search(text)
        elif content_type == "text" and not file_path:
            # Only pathless payloads — shell output, API responses. A payload
            # with a filename is a file, and files are returned byte-exact:
            # trailing whitespace is a hard line break in Markdown, so tidying
            # one would silently change how it renders.
            compressed = compress_plain_text(text)
        else:  # source or unknown — never touched
            compressed = text
        
        tokens_after = count_tokens(compressed)
        
        return CompressResult(
            text=compressed,
            content_type=content_type,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            passthrough=False,
            ccr_id=None
        )
    
    except Exception:
        # On any exception, return original with passthrough=True
        return CompressResult(
            text=text,
            content_type=content_type,
            tokens_before=tokens_before,
            tokens_after=tokens_before,
            passthrough=True,
            ccr_id=None
        )

__all__ = ['compress_text', 'CompressResult', 'detect_content_type']
