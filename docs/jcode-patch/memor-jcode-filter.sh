#!/bin/sh
# jcode post_tool filter -> memor's compressor.
#
# jcode hands the raw tool output on stdin and takes the replacement from
# stdout. Silence plus exit 0 means "no opinion", which is what makes memor's
# own safety declines (read/edit, failed commands, small payloads) compose with
# jcode's fail-open contract: in every declined case jcode keeps the original.
#
# MEMOR_PYTHON lets an editable checkout override the installed interpreter.
: "${MEMOR_PYTHON:=$(command -v python3)}"
exec "$MEMOR_PYTHON" "$(dirname "$0")/memor-jcode-filter.py"
