"""jcode post_tool filter -> memor's compressor.

jcode hands raw tool output on stdin and reads the replacement from stdout.
memor's hook speaks the Claude Code PostToolUse JSON contract, so this
translates between the two. Silence plus exit 0 means "no opinion", which is
what jcode needs in order to keep the original.
"""
import os, sys
text = sys.stdin.read()
try:
    from memor.posttool_compress import build_response
    out = build_response({"tool_name": os.environ.get("JCODE_HOOK_TOOL_NAME", ""),
                          "tool_response": {"stdout": text, "exit_code": 0}})
    if out:
        sys.stdout.write(out["hookSpecificOutput"]["updatedToolOutput"]["stdout"])
except Exception:
    pass
