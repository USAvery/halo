#!/usr/bin/env bash
# Install (idempotently) the local equivalence cron schedule for THIS machine.
#   fast curated set every 2h; pass --with-full to add a nightly full sweep.
# Re-running replaces any prior run_local_equiv.sh entries.  Off-minutes (13/47)
# avoid the top-of-hour pile-up.  Requires a running cron daemon
# (systemd: `systemctl status cron`).
set -euo pipefail

RUNNER="/mnt/g/dev/halo/tools/equivalence/run_local_equiv.sh"
MARK="# halo-equivalence (managed by install_local_cron.sh)"

if [ "${1:-}" != "" ] && [ "${1:-}" != "--with-full" ]; then
  echo "usage: $0 [--with-full]" >&2
  exit 2
fi

FAST="13 */2 * * * $RUNNER fast   $MARK"
FULL="47 3 * * *   $RUNNER full   $MARK"

# Current crontab (empty if none).
current="$(crontab -l 2>/dev/null || true)"
# Drop any previously-managed lines, then append fresh ones.
filtered="$(printf '%s\n' "$current" | grep -vF "$MARK" || true)"
{
  printf '%s\n' "$filtered" | sed '/^$/d'
  printf '%s\n' "$FAST"
  if [ "${1:-}" = "--with-full" ]; then
    printf '%s\n' "$FULL"
  fi
} | crontab -

echo "Installed crontab entries:"
crontab -l | grep -F "$MARK"
