#!/usr/bin/env python3
"""Read, symbolize and diff the in-game RNG draw trace (docs/rng-trace.md).

The instrumented build (`tools/build/build.py --rng-trace`) records every draw
from the GLOBAL random seed into `halo_rng_trace`, a ring buffer in the appended
PE's .bss.  This tool finds that buffer's runtime VA, reads it over XBDM,
decodes the records, symbolizes the captured return addresses, and diffs two
captures to locate the first draw where a patched build and a pristine
build-2276 diverge.

Buffer VA derivation (identical to tools/xbox/symbolize_exception.py):

    runtime_base = round_up(max(section.virtual_addr + virtual_size
                                for section in cachebeta.xbe), 0x1000)
    buffer_va    = runtime_base + <RVA of the `halo_rng_trace` PE export>

Usage:
    tools/xbox/rng_trace_dump.py --host 127.0.0.1 --out a.json
    tools/xbox/rng_trace_dump.py --diff a.json b.json
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from symbolize_exception import (  # noqa: E402
    DEFAULT_KB,
    DEFAULT_ORIGINAL_XBE,
    DEFAULT_PE,
    KbSymbolIndex,
    PeSymbolIndex,
    load_kb_symbols,
    load_pe_symbols,
    normalize_export_name,
    resolve_runtime_base,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BOUNDS = ROOT / "tools" / "verify" / "function_bounds.json"
XBDM_PORT = 731

TRACE_SYMBOL = "halo_rng_trace"
TRACE_MAGIC = 0x54474E52  # 'R','N','G','T' little-endian
TRACE_VERSION = 1         # RNG_TRACE_VERSION
TRACE_CAPACITY = 65536    # RNG_TRACE_CAPACITY
HEADER_SIZE = 16
RECORD_SIZE = 16

LCG_A = 0x19660D
LCG_C = 0x3C6EF35F

# kind -> (name, LCG steps the event advances the global seed)
KINDS = {
    0: ("random_math_real", 1),
    1: ("random_real_range", 1),
    2: ("random_seed_step", 1),
    3: ("random_range", 1),
    4: ("random_seed_get_direction3d", 1),
    5: ("seed_random_orientation", 3),
    6: ("random_direction3d", 1),
    7: ("set_random_seed", "reseed"),
    8: ("network_game_set_random_seed", "info"),
    9: ("game_initialize_for_new_map", "reseed"),
    10: ("periodic_functions_initialize", "reseed"),
    # Damage-path probes: seed_before carries FLOAT BITS, caller2 the object
    # handle.  Never part of the seed sequence; excluded from --diff.
    11: ("probe:damage_scale", "info"),
    12: ("probe:body_before", "info"),
    13: ("probe:body_after", "info"),
    14: ("probe:shield_after", "info"),
    # seed_before = animation index, caller2 = caller of model_animation_choose_random.
    15: ("anim_choose", "info"),
    # seed_before = (anim_state << 8) | old_state, caller = caller of
    # unit_animation_set_state, caller2 = unit handle.
    16: ("probe:unit_state", "info"),
    # FUN_001ab870 around original animation_update_internal; caller identifies
    # the unit_update_animation slot, caller2 is the unit handle.
    17: ("probe:anim_update_in", "info"),
    18: ("probe:anim_update_out", "info"),
}


def lcg(seed: int, steps: int) -> int:
    for _ in range(steps):
        seed = (seed * LCG_A + LCG_C) & 0xFFFFFFFF
    return seed


# --------------------------------------------------------------------------
# XBDM
# --------------------------------------------------------------------------
class XbdmError(RuntimeError):
    pass


def xbdm_connect(host: str, port: int, timeout: float) -> socket.socket:
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise XbdmError(f"failed to connect to {host}:{port}: {exc}") from exc
    sock.settimeout(timeout)
    banner = sock.recv(1024)
    if b"201" not in banner:
        sock.close()
        raise XbdmError(f"bad XBDM banner: {banner!r}")
    return sock


def getmem(sock: socket.socket, addr: int, length: int, chunk: int = 4096) -> bytes:
    """Chunked XBDM `getmem`.  Unreadable bytes come back as '?' and are
    reported as zeroes -- the caller detects that via the magic check."""
    out = bytearray()
    off = 0
    while off < length:
        n = min(chunk, length - off)
        sock.sendall(f"getmem addr=0x{addr + off:08x} length={n}\r\n".encode())
        resp = b""
        while b"\r\n.\r\n" not in resp:
            part = sock.recv(65536)
            if not part:
                raise XbdmError("connection closed during getmem")
            resp += part
        lines = resp.decode("ascii", "replace").split("\r\n")
        if not (lines[0].startswith("202") or lines[0].startswith("203")):
            raise XbdmError(f"getmem 0x{addr + off:08x} failed: {lines[0]}")
        hex_text = ""
        for line in lines[1:]:
            if line == ".":
                break
            hex_text += line.strip()
        # XBDM emits '?' for bytes it cannot read; keep the length and let the
        # magic check decide, rather than raising out of bytes.fromhex().
        hex_text = hex_text.replace("?", "0")
        blob = bytes.fromhex(hex_text) if hex_text else b""
        if len(blob) < n:
            blob += b"\x00" * (n - len(blob))
        out.extend(blob[:n])
        off += n
    return bytes(out)


# --------------------------------------------------------------------------
# Symbolization
# --------------------------------------------------------------------------
class Symbolizer:
    def __init__(self, pe: PeSymbolIndex | None, kb: KbSymbolIndex | None,
                 bounds: list[tuple[int, int, str]]):
        self.pe = pe
        self.kb = kb
        self.bounds = bounds  # sorted (start, end, name)

    def _bounds_lookup(self, address: int) -> tuple[str, int] | None:
        import bisect

        starts = [b[0] for b in self.bounds]
        i = bisect.bisect_right(starts, address) - 1
        if i < 0:
            return None
        start, end, name = self.bounds[i]
        if address < end:
            return name, address - start
        return None

    def symbolize(self, address: int) -> dict:
        """Return {name, offset, space} for a captured return address."""
        if address == 0:
            return {"name": None, "offset": 0, "space": "none"}
        if self.pe is not None and self.pe.contains(address):
            sym = self.pe.nearest(address)
            if sym is not None:
                return {"name": sym.name, "offset": address - sym.address,
                        "space": "impl"}
            return {"name": None, "offset": 0, "space": "impl"}
        hit = self._bounds_lookup(address)
        if hit is not None:
            # Prefer the kb.json name: PE exports use kb names, so this keeps
            # the two captures comparable by symbol.
            name, offset = hit
            if self.kb is not None:
                fn = self.kb.nearest(address)
                if fn is not None and address - fn.address == offset:
                    name = fn.name
            return {"name": name, "offset": offset, "space": "xbe"}
        if self.kb is not None:
            fn = self.kb.nearest(address)
            if fn is not None and address - fn.address < 0x4000:
                return {"name": fn.name, "offset": address - fn.address,
                        "space": "xbe"}
        return {"name": None, "offset": 0, "space": "unknown"}


def load_bounds(path: Path) -> list[tuple[int, int, str]]:
    if not path.exists():
        print(f"warning: function bounds not found: {path}", file=sys.stderr)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for key, value in data.items():
        if key.startswith("_"):
            continue
        try:
            start = int(key, 16)
            end = int(str(value.get("end", key)), 16)
        except (ValueError, AttributeError):
            continue
        out.append((start, end, value.get("name") or f"FUN_{start:08x}"))
    out.sort()
    return out


def find_buffer_va(pe: PeSymbolIndex) -> int:
    for sym in pe.symbols:
        if normalize_export_name(sym.raw_name) == TRACE_SYMBOL:
            return sym.address
    raise XbdmError(
        f"'{TRACE_SYMBOL}' is not exported by the PE -- this build was not "
        "compiled with tools/build/build.py --rng-trace"
    )


# --------------------------------------------------------------------------
# Decode
# --------------------------------------------------------------------------
def decode(blob_header: bytes, blob_records: bytes, write_index: int,
           capacity: int, symbolizer: Symbolizer) -> list[dict]:
    import struct

    count = min(write_index, capacity)
    first = write_index - count  # oldest surviving record's logical index
    records = []
    for n in range(count):
        logical = first + n
        slot = logical % capacity
        tick_kind, seed_before, caller, caller2 = struct.unpack_from(
            "<IIII", blob_records, slot * RECORD_SIZE)
        kind = (tick_kind >> 24) & 0xFF
        kind_name, steps = KINDS.get(kind, (f"kind_{kind}", 1))
        c1 = symbolizer.symbolize(caller)
        if kind_name.startswith("probe:"):
            c2 = {"name": f"handle=0x{caller2:08x}", "offset": 0, "space": "handle"}
        else:
            c2 = symbolizer.symbolize(caller2)
        records.append({
            "index": logical,
            "tick": tick_kind & 0x00FFFFFF,
            "kind": kind_name,
            "steps": steps,
            "seed_before": seed_before,
            "caller": c1["name"],
            "caller_addr": caller,
            "caller_offset": c1["offset"],
            "caller_space": c1["space"],
            "caller2": c2["name"],
            "caller2_addr": caller2,
            "caller2_offset": c2["offset"],
        })
    return records


def check_continuity(records: list[dict]) -> list[dict]:
    """Flag records whose seed_before is not the LCG successor of the previous
    draw.  A break means an untraced writer touched the global seed."""
    breaks = []
    expected = None
    for rec in records:
        steps = rec["steps"]
        if steps == "info":
            continue
        if steps == "reseed":
            expected = rec["seed_before"]
            continue
        if expected is not None and rec["seed_before"] != expected:
            breaks.append({"index": rec["index"], "tick": rec["tick"],
                           "expected": expected, "got": rec["seed_before"],
                           "caller": rec["caller"], "kind": rec["kind"]})
        expected = lcg(rec["seed_before"], steps)
    return breaks


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------
def capture(args) -> int:
    import struct

    runtime_base, base_source = resolve_runtime_base(
        args.runtime_base, Path(args.original_xbe))
    pe = load_pe_symbols(Path(args.pe), runtime_base)
    if pe is None:
        print(f"error: PE not found: {args.pe}", file=sys.stderr)
        return 1
    try:
        buffer_va = find_buffer_va(pe)
    except XbdmError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"runtime_base = 0x{runtime_base:x} ({base_source})", file=sys.stderr)
    print(f"{TRACE_SYMBOL} VA = 0x{buffer_va:x}", file=sys.stderr)

    try:
        sock = xbdm_connect(args.host, args.port, args.timeout)
    except XbdmError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        header = getmem(sock, buffer_va, HEADER_SIZE, args.chunk)
        magic, version, capacity, write_index = struct.unpack("<IIII", header)
        if magic != TRACE_MAGIC:
            print(f"error: magic mismatch at 0x{buffer_va:x}: read 0x{magic:08x}, "
                  f"expected 0x{TRACE_MAGIC:08x} -- the running build is not an "
                  "--rng-trace build, or it has not drawn from the global seed yet",
                  file=sys.stderr)
            return 3
        if version != TRACE_VERSION or capacity != TRACE_CAPACITY:
            print(f"error: header version={version} capacity={capacity}; this "
                  f"tool understands version {TRACE_VERSION} with capacity "
                  f"{TRACE_CAPACITY} (rng_trace.h) -- stale or corrupt header",
                  file=sys.stderr)
            return 3
        count = min(write_index, capacity)
        if count == 0:
            print("warning: buffer is empty (write_index=0)", file=sys.stderr)
        span = capacity * RECORD_SIZE if write_index >= capacity else count * RECORD_SIZE
        print(f"version={version} capacity={capacity} write_index={write_index} "
              f"records={count} (reading {span} bytes)", file=sys.stderr)
        blob = getmem(sock, buffer_va + HEADER_SIZE, span, args.chunk)
        # The game keeps running while we read (no XBDM halt: a halted title
        # does not resume cleanly on this box).  Re-read the header and drop
        # every slot the ring may have touched meanwhile, so the decoded
        # records are all from one generation.
        header_after = getmem(sock, buffer_va, HEADER_SIZE, args.chunk)
        write_index_after = struct.unpack("<IIII", header_after)[3]
    finally:
        sock.close()

    torn = write_index_after - write_index
    if torn < 0 or torn >= capacity:
        print(f"error: ring advanced by {torn} records during the read; "
              "capture again when the game is quieter", file=sys.stderr)
        return 4
    if torn:
        print(f"note: {torn} draws happened during the read; dropping the "
              f"{torn} oldest slots they may have overwritten", file=sys.stderr)

    symbolizer = Symbolizer(pe, load_kb_symbols(Path(args.kb)),
                            load_bounds(Path(args.bounds)))
    records = decode(header, blob, write_index, capacity, symbolizer)
    if torn and write_index >= capacity:
        records = records[torn:]
    breaks = check_continuity(records)
    for b in breaks[:10]:
        print(f"warning: seed continuity break at record {b['index']} "
              f"(tick {b['tick']}, {b['kind']} from {b['caller']}): "
              f"expected 0x{b['expected']:08x}, got 0x{b['got']:08x} -- an "
              "untraced writer advanced the global seed", file=sys.stderr)
    if len(breaks) > 10:
        print(f"warning: ... {len(breaks) - 10} more continuity breaks",
              file=sys.stderr)

    doc = {
        "buffer_va": buffer_va,
        "runtime_base": runtime_base,
        "version": version,
        "capacity": capacity,
        "write_index": write_index,
        "wrapped": write_index > capacity,
        "continuity_breaks": breaks,
        "records": records,
    }
    text = json.dumps(doc, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out} ({len(records)} records)", file=sys.stderr)
    else:
        print(text)
    return 0


# --------------------------------------------------------------------------
# Damage probes
# --------------------------------------------------------------------------
def probes(path: str) -> int:
    """Print every damage event: scale, victim body vitality before and after,
    and how close the result sits to the death boundary (0.0)."""
    import struct

    recs = json.loads(Path(path).read_text(encoding="utf-8"))["records"]

    def f32(bits: int) -> float:
        return struct.unpack("<f", struct.pack("<I", bits))[0]

    def ulps_from_zero(bits: int) -> int:
        # magnitude of the float as an integer count of ULPs from +0.0
        return bits & 0x7FFFFFFF

    n = 0
    for rec in recs:
        kind = rec["kind"]
        if not kind.startswith("probe:"):
            continue
        n += 1
        bits = rec["seed_before"]
        val = f32(bits)
        flag = ""
        shown = f"{val!r:>16} (0x{bits:08x})"
        if kind in ("probe:body_after", "probe:body_before"):
            if bits & 0x80000000 or bits == 0:
                flag = "  <== DEAD (body <= 0)"
            elif ulps_from_zero(bits) < 0x33D6BF95:  # < 1e-7
                flag = "  <== within 1e-7 of death boundary"
        elif kind == "probe:anim_update_in":
            shown = (f"state0=0x{bits & 0xffff:04x} "
                     f"state1=0x{bits >> 16:04x}")
        elif kind == "probe:anim_update_out":
            shown = (f"result=0x{bits & 0xffff:04x} "
                     f"state0=0x{bits >> 16:04x}")
        print(f"[{rec['index']:7d}] tick={rec['tick']:<6d} {kind:<20s} "
              f"{shown} {rec['caller2']}{flag}")
    if n == 0:
        print("no probe records (build predates the damage probes, or "
              "object_cause_damage is not ported in this build)")
        return 1
    return 0


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------
def _key(rec: dict) -> tuple:
    # Raw addresses differ between an all-ported and an all-original build, so
    # the comparison key uses the SYMBOL, not the address.
    return (rec["tick"], rec["kind"], rec["caller"], rec["seed_before"])


def _fmt(rec: dict) -> str:
    if rec["kind"].startswith("probe:"):
        import struct
        val = struct.unpack("<f", struct.pack("<I", rec["seed_before"]))[0]
        return ("  [{index:6d}] tick={tick:<8d} {kind:<28s} value={val!r} "
                "(0x{bits:08x}) {caller}+0x{off:x} {c2}").format(
            index=rec["index"], tick=rec["tick"], kind=rec["kind"], val=val,
            bits=rec["seed_before"], caller=rec["caller"] or "?",
            off=rec["caller_offset"], c2=rec["caller2"])
    return ("  [{index:6d}] tick={tick:<8d} {kind:<28s} seed=0x{seed:08x} "
            "{caller}+0x{off:x} <- {caller2}").format(
        index=rec["index"], tick=rec["tick"], kind=rec["kind"],
        seed=rec["seed_before"], caller=rec["caller"] or "?",
        off=rec["caller_offset"], caller2=rec["caller2"] or "?")


def diff(path_a: str, path_b: str, context: int = 10) -> int:
    a = json.loads(Path(path_a).read_text(encoding="utf-8"))["records"]
    b = json.loads(Path(path_b).read_text(encoding="utf-8"))["records"]
    # Probe records exist only in builds whose probed functions are ported.
    a = [r for r in a if not r["kind"].startswith("probe:")]
    b = [r for r in b if not r["kind"].startswith("probe:")]
    print(f"A: {path_a}  {len(a)} records")
    print(f"B: {path_b}  {len(b)} records")

    limit = min(len(a), len(b))
    first = None
    for i in range(limit):
        if _key(a[i]) != _key(b[i]):
            first = i
            break
    if first is None:
        if len(a) == len(b):
            print("no divergence: the two traces are identical")
            return 0
        first = limit
        print(f"traces agree for all {limit} common records; "
              f"{'A' if len(a) > len(b) else 'B'} has "
              f"{abs(len(a) - len(b))} extra trailing records")
        if first >= len(a) or first >= len(b):
            return 1

    print(f"\nfirst divergence at record index {first}")
    lo = max(0, first - context)
    hi_a = min(len(a), first + context + 1)
    hi_b = min(len(b), first + context + 1)
    print(f"\n--- A ({path_a}) ---")
    for i in range(lo, hi_a):
        print(("*" if i == first else " ") + _fmt(a[i]))
    print(f"\n--- B ({path_b}) ---")
    for i in range(lo, hi_b):
        print(("*" if i == first else " ") + _fmt(b[i]))

    tick = a[first]["tick"] if first < len(a) else b[first]["tick"]
    print(f"\n--- per-caller draw counts at tick {tick} ---")
    counts_a: dict[tuple, int] = {}
    counts_b: dict[tuple, int] = {}
    for recs, counts in ((a, counts_a), (b, counts_b)):
        for rec in recs:
            if rec["tick"] == tick:
                k = (rec["caller"] or "?", rec["kind"])
                counts[k] = counts.get(k, 0) + 1
    print(f"  {'caller':<40s} {'kind':<28s} {'A':>6s} {'B':>6s}  delta")
    for k in sorted(set(counts_a) | set(counts_b)):
        ca, cb = counts_a.get(k, 0), counts_b.get(k, 0)
        mark = "" if ca == cb else "   <-- differs"
        print(f"  {k[0]:<40s} {k[1]:<28s} {ca:>6d} {cb:>6d} {cb - ca:>+6d}{mark}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="XBDM host")
    ap.add_argument("--port", type=int, default=XBDM_PORT)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--chunk", type=int, default=4096,
                    help="getmem chunk size in bytes (default 4096)")
    ap.add_argument("--out", help="write the decoded trace to this JSON file")
    ap.add_argument("--pe", default=str(DEFAULT_PE))
    ap.add_argument("--kb", default=str(DEFAULT_KB))
    ap.add_argument("--bounds", default=str(DEFAULT_BOUNDS))
    ap.add_argument("--original-xbe", default=str(DEFAULT_ORIGINAL_XBE))
    ap.add_argument("--runtime-base", default="auto")
    ap.add_argument("--diff", nargs=2, metavar=("A.json", "B.json"),
                    help="diff two previously captured traces (no XBDM needed)")
    ap.add_argument("--context", type=int, default=10,
                    help="records of context each side of the divergence")
    ap.add_argument("--probes", metavar="A.json",
                    help="list the damage-path probe records of a capture")
    args = ap.parse_args()

    if args.chunk <= 0:
        ap.error("--chunk must be a positive byte count")
    if args.probes:
        return probes(args.probes)
    if args.diff:
        return diff(args.diff[0], args.diff[1], args.context)
    return capture(args)


if __name__ == "__main__":
    raise SystemExit(main())
