#!/usr/bin/env python3
"""Fast Unicorn regression test runner for high-risk ported functions.

Runs unicorn_diff on a curated list of targets from regression_targets.json.
Designed to be fast enough for pre-commit / CI use.

Usage:
    python3 tools/equivalence/regression_test.py          # run all targets
    python3 tools/equivalence/regression_test.py --quick   # fewer seeds (5)
    python3 tools/equivalence/regression_test.py --dry-run  # list targets only
    python3 tools/equivalence/regression_test.py --target FUN_000b4da0  # one target
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TARGETS_FILE = Path(__file__).resolve().parent / "regression_targets.json"
UNICORN_DIFF = ROOT / "tools" / "equivalence" / "unicorn_diff.py"


def load_targets(filter_name=None):
    data = json.loads(TARGETS_FILE.read_text(encoding="utf-8"))
    targets = data.get("targets", [])
    if filter_name:
        targets = [t for t in targets if t["name"] == filter_name or t["addr"] == filter_name]
    return targets


def check_prerequisites(target):
    delinked_dir = ROOT / "delinked"
    obj_path = delinked_dir / target["obj"]
    if obj_path.exists():
        pass
    else:
        addr = target.get("addr", "").replace("0x", "")
        found = any(addr and addr in d.stem for d in delinked_dir.glob("*.obj"))
        if not found:
            return f"missing delinked oracle for {target['name']} (checked {target['obj']} and *{addr}*.obj)"

    flags = target.get("flags", [])
    for i, flag in enumerate(flags):
        if flag == "--state-snapshot" and i + 1 < len(flags):
            snap_path = ROOT / flags[i + 1]
            if not snap_path.exists():
                return f"missing state snapshot: {flags[i + 1]} (capture with game_state_snapshot.py)"

    return None


def _label(target):
    """Display name. Two entries may share one `name` (same function, different
    state snapshot / arg pins), which would otherwise print identical rows —
    an optional `label` tells them apart. Display only; `name` is still what
    unicorn_diff resolves as the target symbol."""
    return target.get("label") or target["name"]


def run_target(target, seed_override=None):
    name = target["name"]
    seeds = seed_override or target.get("seeds", 20)
    flags = target.get("flags", [])

    venv_python = ROOT / ".venv" / "bin" / "python3"
    if not venv_python.exists():
        git_common = Path(subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, cwd=str(ROOT)
        ).stdout.strip()).parent / ".venv" / "bin" / "python3"
        if git_common.exists():
            venv_python = git_common
    python = str(venv_python) if venv_python.exists() else sys.executable

    cmd = [
        python, str(UNICORN_DIFF),
        name,
        "--seeds", str(seeds),
    ] + flags

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            cwd=str(ROOT),
        )
        output = result.stdout + result.stderr

        if "RESULTS:" in output:
            for line in output.splitlines():
                if "RESULTS:" in line:
                    if ", 0 failed," in line and ", 0 errors" in line:
                        return "pass", line.strip()
                    else:
                        return "fail", line.strip()

        if result.returncode != 0:
            last_lines = output.strip().splitlines()[-3:]
            return "error", "; ".join(last_lines)

        return "error", "no RESULTS line in output"

    except subprocess.TimeoutExpired:
        return "error", "timeout (120s)"


def main():
    parser = argparse.ArgumentParser(description="Unicorn regression test runner")
    parser.add_argument("--quick", action="store_true", help="Use 5 seeds per target")
    parser.add_argument("--dry-run", action="store_true", help="List targets only")
    parser.add_argument("--target", help="Run a single target by name or address")
    args = parser.parse_args()

    targets = load_targets(args.target)
    if not targets:
        print("No targets found.")
        return 1

    if args.dry_run:
        print(f"{'Addr':<12} {'Name':<20} {'Obj':<20} {'Seeds':<6} Reason")
        print("-" * 80)
        for t in targets:
            skip = check_prerequisites(t)
            status = f"SKIP: {skip}" if skip else t.get("reason", "")
            print(f"{t['addr']:<12} {_label(t):<20} {t['obj']:<20} {t.get('seeds', 20):<6} {status}")
        return 0

    seed_override = 5 if args.quick else None
    passed = failed = skipped = errors = artifacts = 0
    t0 = time.time()

    print(f"Running {len(targets)} regression target(s)...")
    print()

    for t in targets:
        skip = check_prerequisites(t)
        if skip:
            print(f"  SKIP  {_label(t):<24} {skip}")
            skipped += 1
            continue

        status, detail = run_target(t, seed_override)
        seeds_used = seed_override or t.get("seeds", 20)
        artifact = t.get("known_artifact")

        if status == "pass":
            if artifact:
                # A target marked as a known harness artifact is expected NOT
                # to produce a verdict.  If it passes, the artifact is gone and
                # the marker must be removed -- otherwise a future real
                # regression on this target would be silently excused.
                print(f"  PASS! {_label(t):<24} {seeds_used} seeds — passes "
                      f"despite known_artifact; remove the marker: {artifact}")
                failed += 1
            else:
                print(f"  PASS  {_label(t):<24} {seeds_used} seeds — {detail}")
                passed += 1
        elif artifact:
            # Counted separately, never as a pass and never as a failure.  The
            # marker requires a written reason, like batch_verify_allowlist's
            # entries do, so it cannot be used to quietly mute a red target.
            print(f"  ARTF  {_label(t):<24} {artifact}")
            print(f"        ({status}: {detail})")
            artifacts += 1
        elif status == "fail":
            print(f"  FAIL  {_label(t):<24} {seeds_used} seeds — {detail}")
            failed += 1
        else:
            print(f"  ERR   {_label(t):<24} {detail}")
            errors += 1

    elapsed = time.time() - t0
    print()
    print(f"Done in {elapsed:.1f}s: {passed} passed, {failed} failed, "
          f"{errors} errors, {artifacts} known artifacts, {skipped} skipped")

    return 1 if (failed > 0 or errors > 0) else 0


if __name__ == "__main__":
    sys.exit(main())
