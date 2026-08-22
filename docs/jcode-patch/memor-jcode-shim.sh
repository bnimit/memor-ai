#!/bin/sh
# memor shell shim for jcode — compress tool output without patching jcode.
#
# Why this exists: jcode ignores ANTHROPIC_BASE_URL under OAuth, and its
# post_tool hook is a detached observer whose stdout is discarded, so a
# subscription user has no supported way to shrink tool output. Patching the
# binary works but forfeits upstream upgrades.
#
# jcode's bash tool spawns TokioCommand::new("bash") and StdCommand::new("bash"),
# both resolved through PATH rather than an absolute path. Putting this earlier
# on PATH intercepts tool output while leaving the official binary untouched and
# upgradable.
#
# Install:
#   mkdir -p ~/.memor/shim && cp this ~/.memor/shim/bash && chmod +x ~/.memor/shim/bash
#   PATH="$HOME/.memor/shim:$PATH" jcode
#
# Set MEMOR_PYTHON if memor lives in a virtualenv. MEMOR_SHIM_OFF=1 disables it
# without editing PATH.

REAL="${MEMOR_REAL_BASH:-/bin/bash}"

# Only the two shapes jcode's tool uses are intercepted. Everything else, and
# anything interactive, goes straight through: a shim that mangles a login
# shell is far worse than one that compresses nothing.
case "$1" in
  -c|-lc) ;;
  *) exec "$REAL" "$@" ;;
esac

if [ -n "$MEMOR_SHIM_OFF" ]; then
  exec "$REAL" "$@"
fi

FILTER="${MEMOR_JCODE_FILTER:-$(dirname "$0")/memor-jcode-filter.sh}"
if [ ! -x "$FILTER" ]; then
  exec "$REAL" "$@"
fi

# A sentinel preserves trailing newlines: $(...) strips them, and printf '%s'
# cannot put them back, so without this every captured output silently loses
# its final newline. That is a real corruption -- `wc -l` drops by one, and a
# file read back through the shim differs from the file on disk.
out=$("$REAL" "$@" 2>&1; rc=$?; printf 'X'; exit $rc)
rc=$?
out=${out%X}

tmp=$(mktemp) || { printf '%s' "$out"; exit "$rc"; }
printf '%s' "$out" | JCODE_HOOK_TOOL_NAME=bash "$FILTER" >"$tmp" 2>/dev/null

# Empty output means memor declined (source code, small payload, failed
# command). Emit the original byte-for-byte in that case.
if [ -s "$tmp" ]; then cat "$tmp"; else printf '%s' "$out"; fi
rm -f "$tmp"
exit "$rc"
