#!/usr/bin/env bash
# Pre-commit hook: never commit anything under delinked/.
#
# delinked/ is regenerated host-side (Ghidra export / batch_delink.py) and is
# already covered by the /delinked/ rule in .gitignore, but .gitignore only
# stops *new* untracked files from being auto-added — it does not stop an
# explicit `git add -f` (or `git add <path>` on a file added before the
# ignore rule existed), so stale binaries have crept in and then rotted once
# a later regenerated delinked/ no longer produced byte-identical output.
# This hook makes the "never commit these" rule mechanical instead of relying
# on nobody ever running `git add -f`.
#
# Bypass: git commit --no-verify

STAGED_DELINKED="$(git diff --cached --name-only -- 'delinked/*')"

if [ -z "$STAGED_DELINKED" ]; then
    exit 0
fi

echo "" 1>&2
echo "Commit blocked: staged file(s) under delinked/ — never commit these:" 1>&2
echo "$STAGED_DELINKED" | sed 's/^/  /' 1>&2
echo "" 1>&2
echo "delinked/ is regenerated host-side and covered by .gitignore; unstage" 1>&2
echo "with: git restore --staged -- delinked/" 1>&2
echo "Bypass (should not be needed): git commit --no-verify" 1>&2
exit 1
