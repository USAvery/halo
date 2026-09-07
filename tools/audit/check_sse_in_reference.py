#!/usr/bin/env python3
"""Flag ported functions whose reference body uses SSE1.

MSVC 7.1 inlined some real_math helpers as SSE1 sequences (movss/movhps,
subps, mulps, addss ...) where every operation rounds to float32.  Our lifts
are compiled with -mno-sse and call the x87 helpers instead, which accumulate
at 64-bit significand and compare wide -- a different rounding model that the
x87 narrowing checker cannot see (system-link run 10, FUN_00147ed0).  Any
function listed here must reproduce the per-op float32 rounding explicitly
(HALO_FLT_ROUNDTRIP after each op, or an asm block as in 0x109850).

Usage: check_sse_in_reference.py [--all]   (default: ported functions only)
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/audit"))
from check_delinked_bounds import load_xbe, va_to_off  # noqa: E402
from capstone import Cs, CS_ARCH_X86, CS_MODE_32  # noqa: E402

SSE_PREFIX = ("shufps", "movhps", "movlps", "movaps", "movups", "cvt", "unpck")


def kb_function_map():
    kb = json.loads((ROOT / "kb.json").read_text())
    out = {}
    for ob in kb.get("objects", []):
        fns = ob.get("functions") or []
        items = fns.items() if isinstance(fns, dict) else [(f.get("addr"), f) for f in fns]
        for key, f in items:
            addr = f.get("addr") or key
            try:
                out[int(addr, 16)] = (f.get("name") or "FUN_%08x" % int(addr, 16),
                                      bool(f.get("ported")), ob.get("name"))
            except (TypeError, ValueError):
                continue
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="include unported functions")
    args = ap.parse_args()
    data, sections = load_xbe(ROOT / "halo-patched/cachebeta.xbe")
    bounds = json.loads((ROOT / "tools/verify/function_bounds.json").read_text())
    fmap = kb_function_map()
    cs = Cs(CS_ARCH_X86, CS_MODE_32)
    rows = []
    for key, ent in bounds.items():
        if not key.startswith("0x"):
            continue
        start = int(key, 16)
        info = fmap.get(start)
        if not args.all and not (info and info[1]):
            continue
        off = va_to_off(sections, start)
        if off is None:
            continue
        end = int(ent["end"], 16)
        ops = {}
        for ins in cs.disasm(bytes(data[off:off + (end - start)]), start):
            m = ins.mnemonic
            if m.endswith(("ss", "ps")) or m.startswith(SSE_PREFIX):
                ops[m] = ops.get(m, 0) + 1
        if ops:
            name = info[0] if info else ent.get("name", key)
            rows.append((sum(ops.values()), key, name, " ".join(sorted(ops))))
    rows.sort(reverse=True)
    for n, key, name, ops in rows:
        print("[SSE-REF] %s %s: %d SSE insns [%s]" % (key, name, n, ops))
    print("%d function(s) with SSE in the reference" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
