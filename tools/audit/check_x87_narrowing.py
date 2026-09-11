#!/usr/bin/env python3
"""Detect lost float32 narrowing between our shipped clang code and the XBE.

Why this check exists
---------------------
`check_fpu_association.py` covers addend reassociation.  That class was measured
inert on this target: Halo runs the x87 at PC=11 (64-bit extended significand),
so a 3-term reassociation cannot change a result.

This check covers the class that is *not* inert at PC=11.  MSVC 7.1 narrows an
intermediate `float` to 24-bit significand by storing it to a dword stack slot
and reloading it (`fstp dword ptr [ebp-X]` / `fld dword ptr [ebp-X]`).  clang
with `-mno-sse` is free to leave the same value in ST(i) at 64-bit precision and
skip the round trip.  That is a real 24-bit rounding the original performs and
we do not -- at any precision control -- and it is enough to flip a threshold
compare a tick early and desync a lockstep system-link game.

VC71 cannot see it: VC71 compiles our C with cl.exe, which performs the same
narrowing our shipped clang build omits, so the score stays 100%.

Metric: per function, count dword-width x87 stores to frame-relative slots that
are later reloaded by an x87 dword load from the same slot.  Report functions
where ours is lower than the original's (MISSING) and, separately, where ours is
higher (EXTRA): clang spills a float-typed *computed* value as a dword under
register pressure -- e.g. the partial dot product in FUN_0014f2c0 -- which is a
24-bit rounding the original never performs.  Fix an EXTRA by typing the
temporary `x87_wide_t` (src/x87_math.h) and promoting the products feeding it.
The count is a net: an extra can hide a missing one, so a clean report is not
proof of parity -- inspect the computed dword stores when a divergence persists.

Fix template: `HALO_FLT_ROUNDTRIP(lv)` in `src/halo/math/real_math.c` -- a
guarded empty `asm volatile ("" : "+m"(lv))` that forces the store/reload at
zero VC71 cost.
"""

import argparse
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_fpu_association import (  # noqa: E402
    BOUNDS, OBJ_ROOT, XBE, disasm_xbe, load_xbe, objdump_functions,
)

# `fstp dword ptr [ebp - 0x10]` / `fld dword ptr [esp + 0x24]`
SLOT_RE = re.compile(r"^dword ptr \[\s*(e[bs]p)\s*([+-])\s*(0x[0-9a-fA-F]+|\d+)\s*\]$")


def _slot(op_str):
    m = SLOT_RE.match(op_str.strip())
    if not m:
        return None
    reg, sign, disp = m.groups()
    return "%s%s%d" % (reg, sign, int(disp, 0))


# Ops that can leave ST(0) with more than 24 significant bits.  Sign/exchange
# ops are excluded: they cannot widen a value, so a round trip after one of them
# re-rounds nothing.
RELOAD_OPS = ("fadd", "fsub", "fsubr", "fmul", "fdiv", "fdivr", "fcom", "fcomp")
ARITH = ("fadd", "fsub", "fmul", "fdiv", "fsqrt", "fprem", "fscale", "frndint",
         "fsin", "fcos", "fptan", "fpatan", "fyl2x", "f2xm1", "fidiv", "fimul",
         "fiadd", "fisub")


def narrowing_sites(insns):
    """Slots holding a *computed* value narrowed by a dword store/reload.

    A round trip only re-rounds when the stored value carries more than 24
    significant bits, i.e. when it came out of an arithmetic op rather than
    straight off a float32 load.  Counting plain spills instead would flag every
    register-allocation difference as a numeric one.
    """
    computed = False
    stored, roundtripped = {}, {}
    for ins in insns:
        if not ins.mnemonic.startswith("f"):
            continue
        if ins.mnemonic in ("fst", "fstp", "fld"):
            slot = _slot(ins.op_str)
            if slot is None:
                computed = ins.mnemonic != "fld"
                continue
            if ins.mnemonic == "fld":
                if slot in stored:
                    roundtripped[slot] = (stored[slot], ins.address)
                computed = False
            else:
                if computed:
                    stored[slot] = ins.address
                computed = ins.mnemonic == "fst"
            continue
        if ins.mnemonic in ("fxch", "fchs", "fabs"):
            continue  # exchange/sign ops carry the wide value through unchanged
        # MSVC also reloads a narrowed slot straight into an arithmetic op or a
        # compare (`fmul dword ptr [ebp-8]`, `fcomp dword ptr [ebp-0xc]`); those
        # are round trips just like an explicit fld and were the checker's
        # blind spot (t_min/V/b in projectile_aim_ballistic).
        if ins.mnemonic in RELOAD_OPS:
            slot = _slot(ins.op_str)
            if slot is not None and slot in stored:
                roundtripped[slot] = (stored[slot], ins.address)
        computed = ins.mnemonic.startswith(ARITH)
    return roundtripped


def narrowing_slots(insns):
    return set(narrowing_sites(insns))


def source_locations(obj, sites):
    addresses = [address for pair in sites.values() for address in pair]
    if not addresses:
        return {}
    command = ["llvm-addr2line", "-e", str(obj)]
    command.extend("0x%x" % address for address in addresses)
    lines = subprocess.check_output(command, text=True).splitlines()
    return dict(zip(addresses, lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="restrict to these source files")
    ap.add_argument("--function", action="append", default=[])
    ap.add_argument("--min-delta", type=int, default=1,
                    help="only report when the original has this many more")
    ap.add_argument("--show-sites", action="store_true",
                    help="print store/reload addresses for compared functions")
    ap.add_argument("--context-address", action="append", default=[],
                    type=lambda value: int(value, 0),
                    help="print shipped-object instructions around this address")
    args = ap.parse_args()

    if not XBE.exists():
        print("missing %s" % XBE, file=sys.stderr)
        return 2
    raw, sections = load_xbe(XBE)
    bounds = json.loads(BOUNDS.read_text())
    by_name = {}
    for addr, ent in bounds.items():
        n = ent.get("name")
        if n:
            by_name[n] = (int(addr, 16), int(ent["end"], 16))

    # unported_thunks.c holds JMP-only thunks for functions we have NOT
    # lifted.  They contain no FPU code at all, so every one scores "ours 0"
    # against a real XBE function and lands at the top of the report.  That
    # is not a lost narrowing, it is an unported function -- 63 of 207
    # findings before this filter.
    objs = [o for o in sorted(OBJ_ROOT.rglob("*.obj"))
            if o.name != "unported_thunks.c.obj"]
    if args.paths:
        want = {Path(p).name + ".obj" for p in args.paths}
        objs = [o for o in objs if o.name in want]
    if not objs:
        print("no clang objects under %s -- build first" % OBJ_ROOT, file=sys.stderr)
        return 2

    findings, extras, checked = [], [], 0
    for obj in objs:
        for name, insns in objdump_functions(obj).items():
            if args.function and name not in args.function:
                continue
            if name not in by_name:
                continue
            start, end = by_name[name]
            ours_sites = narrowing_sites(insns)
            theirs_sites = narrowing_sites(disasm_xbe(raw, sections, start, end))
            ours = set(ours_sites)
            theirs = set(theirs_sites)
            if args.show_sites:
                locations = source_locations(obj, ours_sites)
                print("[X87-SITES] %s ours %s" % (
                    name, ", ".join("%s@0x%x(%s)->0x%x(%s)" %
                                    (slot, pair[0], locations.get(pair[0], "?"),
                                     pair[1], locations.get(pair[1], "?"))
                                    for slot, pair in sorted(ours_sites.items(),
                                                             key=lambda item: item[1]))))
                print("[X87-SITES] %s xbe  %s" % (
                    name, ", ".join("%s@0x%x->0x%x" %
                                    (slot, pair[0], pair[1])
                                    for slot, pair in sorted(theirs_sites.items(),
                                                             key=lambda item: item[1]))))
            for address in args.context_address:
                indexes = [index for index, ins in enumerate(insns)
                           if ins.address == address]
                if indexes:
                    index = indexes[0]
                    print("[X87-CONTEXT] %s 0x%x" % (name, address))
                    for ins in insns[max(0, index - 8):index + 9]:
                        print("  0x%x: %-8s %s" %
                              (ins.address, ins.mnemonic, ins.op_str))
            checked += 1
            delta = len(theirs) - len(ours)
            if delta >= args.min_delta:
                findings.append((delta, name, obj, len(ours), len(theirs)))
            elif delta < 0:
                extras.append((-delta, name, obj, len(ours), len(theirs)))

    for delta, name, obj, no, nt in sorted(findings, reverse=True):
        print("[X87-NARROW] %s (%s): ours %d, xbe %d (-%d)"
              % (name, obj.relative_to(OBJ_ROOT), no, nt, delta))
    for delta, name, obj, no, nt in sorted(extras, reverse=True):
        print("[X87-EXTRA-NARROW] %s (%s): ours %d, xbe %d (+%d)"
              % (name, obj.relative_to(OBJ_ROOT), no, nt, delta))

    print("\n%d compared, %d MISSING-NARROWING, %d EXTRA-NARROWING"
          % (checked, len(findings), len(extras)))
    return 1 if findings or extras else 0


if __name__ == "__main__":
    sys.exit(main())
