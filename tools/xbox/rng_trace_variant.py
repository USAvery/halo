#!/usr/bin/env python3
"""Build the "baseline" kb.json variant for an RNG-trace A/B (docs/rng-trace.md).

`--baseline` sets `ported: false` on EVERY function except the ones belonging to
random_math.obj.  patch.py then leaves the original XBE code in place everywhere
(and tail-calls the original from each of our impls), so the console runs stock
build-2276 logic while our instrumented RNG primitives stay live and keep
recording.  Diffing that trace against one from the fully-ported build tells you
which lifted function draws differently.

THROWAWAY BUILD ONLY.  The resulting kb.json must never be committed: the
pre-commit deactivation gate (tools/audit/check_ported_deactivations.py) will
reject it, and rightly so.  `--restore` puts the original file back byte for
byte from the snapshot taken by `--baseline`.

Usage:
    tools/xbox/rng_trace_variant.py --baseline
    tools/xbox/rng_trace_variant.py --status
    tools/xbox/rng_trace_variant.py --restore
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KB = ROOT / "kb.json"
SNAPSHOT = ROOT / "artifacts" / "rng_trace" / "kb.json.pre-baseline"
KEEP_OBJECTS = {"random_math.obj"}

# Functions that MUST stay ported=true even in the baseline variant.
#
# tools/build/patch.py deactivates a lift by splatting a redirect over our own
# impl entry.  For a plain cdecl/stdcall function that is a 5-byte JMP rel32 and
# always fits, but an @<reg> function needs a register-marshalling stub (re-push
# the arg block, load the register args, CALL the original, unwind) which can run
# to 41 bytes.  When our impl body is shorter than the stub, patch.py refuses
# rather than splat the next export, and the whole patched_xbe step fails:
#
#   ERROR Deactivation stub for "FUN_001550c0" (37 bytes) exceeds impl room
#         (32 bytes to next export at 73dca0)
#
# The 12 names below are every export in build/halo where the stub does not fit,
# measured by replaying patch.py's own generate_deactivation_redirect() against
# the PE export table (stub size vs. distance to the next export).  None of them
# draw from the global RNG seed, so leaving them ported does not perturb the
# trace.  Recompute after a large lift batch if --baseline starts failing again:
# import tools/build/patch.py, walk build/halo's exports, and compare
# len(generate_deactivation_redirect(sym, va, 0)) against the gap to the next
# export address.
# Keyed by kb.json "addr" because most entries carry no "name" field.
KEEP_FUNCTION_ADDRS = {
    "0x1550c0": "FUN_001550c0",  # stub 37 > room 32
    "0x155380": "FUN_00155380",  # stub 41 > room 32
    "0x1553d0": "FUN_001553d0",  # stub 37 > room 32
    "0x155620": "FUN_00155620",  # stub 17 > room 16
    "0x156070": "FUN_00156070",  # stub 33 > room 32
    "0x15b650": "FUN_0015b650",  # stub 33 > room 32
    "0x15c2b0": "FUN_0015c2b0",  # stub 33 > room 32
    "0x15d020": "FUN_0015d020",  # stub 33 > room 32
    "0x15d040": "FUN_0015d040",  # stub 33 > room 32
    "0x168230": "FUN_00168230",  # stub 41 > room 32
    "0x1744f0": "FUN_001744f0",  # stub 33 > room 32
    "0xe19c0":  "tgaLoad",       # stub 41 > room 32
    # Traced global-seed WRITERS.  Their RNG_TRACE reseed records must exist in
    # BOTH captures, otherwise the first reseed is a false "divergence".  All
    # three are byte-faithful lifts of trivial setters/initialisers.
    "0xa76a0":  "set_random_seed",
    "0x12a060": "network_game_set_random_seed",
    "0xa7780":  "game_initialize_for_new_map",
}
MARKER = "_rng_trace_off"


def read_kb() -> tuple[bytes, dict]:
    raw = KB.read_bytes()
    return raw, json.loads(raw.decode("utf-8"))


def serialize(data: dict) -> bytes:
    """kb.json is `json.dumps(..., indent=1, ensure_ascii=False)` with no
    trailing newline.  Verified by round-tripping HEAD's kb.json."""
    return json.dumps(data, indent=1, ensure_ascii=False).encode("utf-8")


def assert_round_trip(raw: bytes, data: dict) -> None:
    if serialize(data) != raw:
        raise SystemExit(
            "error: kb.json does not round-trip through this writer "
            "(json.dumps indent=1, ensure_ascii=False, no trailing newline).\n"
            "       Refusing to rewrite it -- the formatting would change."
        )


def git_clean(path: Path, rev: str | None = None) -> bool:
    cmd = ["git", "diff", "--quiet"]
    if rev:
        cmd.append(rev)
    cmd += ["--", str(path.relative_to(ROOT))]
    return subprocess.run(cmd, cwd=ROOT).returncode == 0


def each_function(data: dict):
    for obj in data.get("objects", []) or []:
        name = obj.get("name") or "?"
        for fn in obj.get("functions", []) or []:
            yield name, fn


def do_baseline() -> int:
    if SNAPSHOT.exists():
        print(f"error: {SNAPSHOT} already exists -- a previous --baseline was "
              "never restored.  Run --restore first.", file=sys.stderr)
        return 1

    raw, data = read_kb()
    assert_round_trip(raw, data)

    if not git_clean(KB):
        print("note: kb.json has uncommitted changes; they are preserved in the "
              "snapshot and restored by --restore.", file=sys.stderr)

    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_bytes(raw)

    off = kept = kept_small = 0
    for obj_name, fn in each_function(data):
        if fn.get("ported") is not True:
            continue
        if obj_name in KEEP_OBJECTS:
            kept += 1
            continue
        if str(fn.get("addr", "")).lower() in KEEP_FUNCTION_ADDRS:
            kept_small += 1
            continue
        fn["ported"] = False
        fn[MARKER] = True
        off += 1

    KB.write_bytes(serialize(data))
    print(f"deactivated {off} ported functions; kept {kept} in "
          f"{sorted(KEEP_OBJECTS)}")
    if kept_small:
        print(f"kept {kept_small} more whose impl is too small for a "
              f"deactivation stub (see KEEP_FUNCTION_ADDRS)")
    print(f"snapshot: {SNAPSHOT}")
    print("build with: rtk python3 tools/build/build.py -q --rng-trace")
    print("DO NOT COMMIT kb.json in this state; run --restore when done.")
    return 0


def do_status() -> int:
    _, data = read_kb()
    off = [(o, fn.get("addr"), fn.get("name"))
           for o, fn in each_function(data) if fn.get(MARKER)]
    print(f"snapshot present: {SNAPSHOT.exists()}")
    print(f"{len(off)} functions deactivated by --baseline")
    kept = [o for o, fn in each_function(data)
            if fn.get("ported") is True and o in KEEP_OBJECTS]
    print(f"{len(kept)} still-ported functions in {sorted(KEEP_OBJECTS)}")
    return 0


def do_restore(force_git: bool) -> int:
    if SNAPSHOT.exists():
        KB.write_bytes(SNAPSHOT.read_bytes())
        SNAPSHOT.unlink()
        print("kb.json restored byte-for-byte from the snapshot")
        return 0
    if git_clean(KB):
        print("no snapshot and kb.json matches the index -- nothing to restore")
        return 0
    if not force_git:
        print("error: no snapshot exists and kb.json has uncommitted changes.\n"
              "       Refusing to discard them.  Inspect with "
              "`git diff -- kb.json`; pass --force-git to run "
              "`git checkout -- kb.json` anyway.", file=sys.stderr)
        return 1
    subprocess.run(["git", "checkout", "--", "kb.json"], cwd=ROOT, check=True)
    print("kb.json reverted with git checkout")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--baseline", action="store_true",
                   help="deactivate every port except random_math.obj")
    g.add_argument("--restore", action="store_true",
                   help="restore kb.json from the --baseline snapshot")
    g.add_argument("--status", action="store_true")
    ap.add_argument("--force-git", action="store_true",
                    help="--restore fallback: git checkout -- kb.json")
    args = ap.parse_args()
    if args.baseline:
        return do_baseline()
    if args.status:
        return do_status()
    return do_restore(args.force_git)


if __name__ == "__main__":
    raise SystemExit(main())
