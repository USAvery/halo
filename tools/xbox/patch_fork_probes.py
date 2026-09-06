#!/usr/bin/env python3
"""Patch the turn-in-place fork probes into OUR built XBE.

FUN_001a4c50 (0x1a4c50) is unported, so our build runs the original bytes at
the same addresses as the pristine one.  The host-side equivalent lives in
artifacts/rng_trace/build_original_probes.py; the two differ only in where the
ring and the code caves are.

Why this exists rather than a source-level probe: the fork writes the current
facing in place at 0x1a4f0e, before it builds the cosine at 0x1a5061.  Sampling
the facing in the ported caller therefore reads it one update too early, which
is correct only for a biped that is not turning.  Only a probe inside the fork
sees what the fcomp actually compares.

    python3 tools/xbox/patch_fork_probes.py [--xbe PATH] [--pe PATH]
"""
import argparse
import hashlib
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools/xbox"), str(ROOT / "tools/audit")]
import rng_trace_dump as trace                      # noqa: E402
from check_delinked_bounds import load_xbe, va_to_off  # noqa: E402

# Probe A: top of the gate chain.  Fires even when an earlier gate exits, so
# both machines always log it.  Stolen: mov al,[esi+0x42a] (6 bytes).
GATE_SITE, GATE_STOLEN, GATE_RESUME = 0x1A5109, "8a862a040000", 0x1A510F
# Probe B: the fcomp's ported-computed operand, [ebp-0xc].
# Stolen: mov eax,[esi+0x1b8] (6 bytes).
COS_SITE, COS_STOLEN, COS_RESUME = 0x1A5142, "8b86b8010000", 0x1A5148

GATE_VALUE = (
    b"\x0f\xb6\x86\x2a\x04\0\0"      # movzx eax, byte [esi+0x42a]
    b"\x0f\xb6\x8e\x57\x02\0\0"      # movzx ecx, byte [esi+0x257]
    b"\xc1\xe1\x08\x09\xc8"          # shl ecx,8 ; or eax,ecx
    b"\x8b\x8e\xb4\x01\0\0"          # mov ecx, [esi+0x1b4]
    b"\x81\xe1\x00\x40\0\0"          # and ecx, 0x4000
    b"\xc1\xe1\x02\x09\xc8"          # shl ecx,2 -> bit 16 ; or
    b"\x8b\x8e\xb8\x01\0\0"          # mov ecx, [esi+0x1b8]
    b"\x81\xe1\x00\x01\0\0"          # and ecx, 0x100
    b"\xc1\xe1\x09\x09\xc8"          # shl ecx,9 -> bit 17 ; or
    b"\x8b\x75\x08"                  # mov esi, [ebp+8]  (unit handle)
)
COS_VALUE = (
    b"\x8b\x45\xf4"                  # mov eax, [ebp-0xc]
    b"\x8b\x75\x08"                  # mov esi, [ebp+8]
)


def relative(opcode, at, target):
    return bytes([opcode]) + struct.pack("<I", (target - at - 5) & 0xFFFFFFFF)


def log(note_fn, seed_addr, kind, value_code, caller):
    """Ring write by CALLING rng_trace_note, not by reimplementing it.

    The host patcher inlines the ring write because the baseline build has no
    logger to call.  Ours does, and calling it guarantees the binary probe and
    the source-level probes produce byte-identical records.

    PUSHFD/PUSHAD bracket the body, so value_code may clobber any GPR.  It must
    leave the payload in EAX and the unit handle in ESI.
    """
    code = b"\x9c\x60" + value_code
    code += b"\x56"                                    # push esi   (extra)
    code += b"\x68" + struct.pack("<I", caller)        # push caller
    code += b"\x50"                                    # push eax   (payload)
    code += b"\x6a" + bytes([kind])                    # push kind
    code += b"\x68" + struct.pack("<I", seed_addr)     # push seed ptr
    return code, b"\x83\xc4\x14\x61\x9d"           # add esp,20 ; popad ; popfd


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xbe", default=str(ROOT / "halo-patched/default.xbe"))
    ap.add_argument("--pe", default=str(ROOT / "build/halo"))
    ap.add_argument("--base", type=lambda v: int(v, 0), default=0x642000)
    args = ap.parse_args()

    xbe = Path(args.xbe)
    data, sections = load_xbe(xbe)
    data = bytearray(data)

    pe = trace.load_pe_symbols(Path(args.pe), args.base)
    exports = {s.name: s.address for s in pe.symbols}
    for want in ("halo_rng_trace", "halo_probe_cave", "rng_trace_note"):
        if want not in exports:
            print("error: %s missing from %s -- build with --rng-trace"
                  % (want, args.pe), file=sys.stderr)
            return 2
    cave = exports["halo_probe_cave"]
    note_fn = exports["rng_trace_note"]
    seed_addr = 0x46E3F4   # the global seed; rng_trace_note ignores any other

    def write(addr, blob):
        off = va_to_off(sections, addr)
        if off is None:
            raise SystemExit("address 0x%x is not mapped in %s" % (addr, xbe))
        # A .bss address maps to a file offset PAST the end of the image: the
        # section has a virtual size but no raw bytes.  bytearray slice
        # assignment there silently appends to the file instead of raising, so
        # the probe lands nowhere and the XBE grows.  Check the bound.
        if off + len(blob) > len(data):
            raise SystemExit(
                "address 0x%x maps to file offset %d, past the end of %s "
                "(%d bytes) -- that region has no raw data.  If this is "
                "halo_probe_cave, its initializer put it in .bss; give it a "
                "non-zero initializer so it lands in .data."
                % (addr, off, xbe.name, len(data)))
        data[off:off + len(blob)] = blob

    def install(site, stolen_hex, resume, kind, value, at):
        stolen = bytes.fromhex(stolen_hex)
        off = va_to_off(sections, site)
        actual = bytes(data[off:off + len(stolen)])
        if actual != stolen:
            raise SystemExit(
                "site 0x%x holds %s, expected %s -- the fork is not the "
                "original bytes here, refusing to patch"
                % (site, actual.hex(), stolen_hex))
        head, tail = log(note_fn, seed_addr, kind, value, site)
        call_at = at + len(head)
        code = head + relative(0xE8, call_at, note_fn) + tail + stolen
        code += relative(0xE9, at + len(code), resume)
        write(at, code)
        write(site, relative(0xE9, site, at) + b"\x90" * (len(stolen) - 5))
        return len(code)

    used = install(GATE_SITE, GATE_STOLEN, GATE_RESUME, 19, GATE_VALUE, cave)
    used += install(COS_SITE, COS_STOLEN, COS_RESUME, 20, COS_VALUE,
                    cave + used)
    if used > 1024:
        raise SystemExit("probe code is %d bytes, cave is 1024" % used)

    xbe.write_bytes(bytes(data))
    print("patched %s" % xbe)
    print("  cave 0x%x  rng_trace_note 0x%x  used %d/1024 bytes"
          % (cave, note_fn, used))
    print("  sha256 %s" % hashlib.sha256(bytes(data)).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
