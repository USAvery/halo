#!/usr/bin/env bash
# Local equivalence cron runner (system crontab, this machine only).
#
#   run_local_equiv.sh fast   # curated regression set incl. --real-callees +
#                             # committed snapshot fixtures. Fast (~seconds).
#   run_local_equiv.sh full   # bounded full batch_verify sweep (--skip-existing).
#
# Installed via crontab (see install_local_cron.sh): fast every 2h, with full
# runs only when explicitly requested. flock prevents overlap; logs rotate.
set -u

REPO="/mnt/g/dev/halo"
PY="$REPO/.venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"

MODE="${1:-fast}"
LOGDIR="$REPO/artifacts/equivalence/cron_logs"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/${MODE}-$(date +%Y%m%d-%H%M%S).log"
LOCKFILE="$LOGDIR/${MODE}.lock"

cd "$REPO" || { echo "cannot cd $REPO" >&2; exit 3; }

# Serialize: skip if a previous same-mode run is still going (e.g. a long full
# sweep overlapping the next tick).
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  echo "[$(date)] $MODE: previous run still holding lock — skipped" | tee -a "$LOG"
  exit 0
fi

echo "[$(date)] $MODE: start (py=$PY)" | tee -a "$LOG"

case "$MODE" in
  fast)
    "$PY" tools/equivalence/regression_test.py >>"$LOG" 2>&1
    rc=$?
    ;;
  full)
    BASE=""
    [ -f artifacts/batch_verify/summary.json ] && \
      BASE="--baseline artifacts/batch_verify/summary.json"
    # The GitHub nightly already runs the complete suite. Keep an explicit
    # local full run bounded so it cannot starve the self-hosted runner when
    # it is used for diagnosis. Three 3-GiB workers fit within this WSL VM
    # while leaving room for the runner, build tools, and desktop agents.
    HALO_EQUIV_MEM_LIMIT_GB="${HALO_EQUIV_MEM_LIMIT_GB:-3}" \
    "$PY" tools/equivalence/batch_verify.py \
      --seeds 50 --timeout 120 --jobs 3 --max-wall-minutes 105 --csv --skip-existing \
      --allowlist tools/equivalence/batch_verify_allowlist.json \
      --skip-allowlisted \
      $BASE --output-dir artifacts/batch_verify >>"$LOG" 2>&1
    rc=$?
    ;;
  *)
    echo "usage: $0 fast|full" >&2
    exit 2
    ;;
esac

# Keep the log dir bounded.
find "$LOGDIR" -name '*.log' -mtime +14 -delete 2>/dev/null

echo "[$(date)] $MODE: done rc=$rc" | tee -a "$LOG"
# Surface the result line for quick `tail` triage.
grep -hE 'Done in|Equivalence|RESULTS:|pass|fail' "$LOG" | tail -3
exit $rc
