#!/usr/bin/env bash
#
# Install the Trackly pre-push hook into this repository's .git/hooks.
# Idempotent and safe: backs up any existing pre-push hook once.
#
#   bash jira/ci/install-hooks.sh
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
HOOK_SRC="$ROOT/jira/ci/git-hooks/pre-push"
HOOKS_DIR="$(git rev-parse --git-path hooks)"
HOOK_DST="$HOOKS_DIR/pre-push"

chmod +x "$ROOT/jira/ci/run-local-ci.sh" "$HOOK_SRC" 2>/dev/null || true
mkdir -p "$HOOKS_DIR"

if [ -e "$HOOK_DST" ] && [ ! -L "$HOOK_DST" ]; then
  cp "$HOOK_DST" "$HOOK_DST.backup.$(date +%s 2>/dev/null || echo bak)"
  echo "• Backed up existing pre-push hook to $HOOK_DST.backup.*"
fi

# Prefer a symlink so edits to the tracked hook take effect immediately;
# fall back to a copy on filesystems without symlink support.
if ln -sf "$HOOK_SRC" "$HOOK_DST" 2>/dev/null; then
  echo "✓ Linked pre-push hook -> $HOOK_SRC"
else
  cp "$HOOK_SRC" "$HOOK_DST"
  chmod +x "$HOOK_DST"
  echo "✓ Copied pre-push hook -> $HOOK_DST"
fi

echo
echo "Trackly local CI will now run automatically before each 'git push' that"
echo "touches jira/. Try it without pushing:  jira/ci/run-local-ci.sh"
echo "Bypass once (not recommended):           git push --no-verify"
