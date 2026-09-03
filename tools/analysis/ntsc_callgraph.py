#!/usr/bin/env python3
"""ntsc_callgraph.py -- Static call graph and reference index for the NTSC
Xbox debug build 2276 binary (halo-patched/cachebeta.xbe, MD5
c7869590a1c64ad034e49a5ee0c02465).

Step 3a of the CEA-to-NTSC name-propagation plan. kb.json function records
carry no callers/callees fields, so this derives them mechanically from a
single linear disassembly pass over every XBE section that contains at least
one kb.json function address.

For every kb.json function (addressed by its VA), using
tools/verify/function_bounds.json where present and falling back to "ends at
the next known function start in the same section" otherwise (mirrors
ghidra_scripts/ExportFunctionSizes.java's clamp rule), this emits:
  - callees: unique direct (E8 rel32) call targets that land on another
    known kb.json function's start address.
  - unresolved_calls: count of E8 targets that do NOT land on a known start
    (mid-function tail, unlisted helper, etc).
  - import_calls: unique import/indirect-thunk slot addresses referenced by
    "call dword ptr [disp32]" (FF 15 with a base-less, index-less memory
    operand) -- the slot address itself, not its (runtime) contents.
  - strings: unique printable-ASCII (length >= 4, truncated to 200 chars)
    strings referenced by a 32-bit absolute address literal that lands in
    .rdata or .data.
  - data_refs: unique 32-bit absolute address literals that land in .data or
    .bss and did not qualify as a string.

Decoder
-------
capstone (x86, mode 32) is used for disassembly. It is already an
undeclared-but-present dependency of this repo's own audit tools
(tools/audit/check_arg_counts.py, tools/audit/dump_caller_regsetup.py both
import it directly; it lives in .venv at 5.0.7 but is NOT listed in
requirements.txt by any of them). This script follows that precedent and
does not add capstone to requirements.txt either.

capstone's structured operand decode (X86_OP_IMM immediates, and X86_OP_MEM
operands whose base/index registers are both absent, i.e. bare
"[disp32]" absolute addressing) subsumes the byte-pattern list this task
named as an acceptable minimal-decoder fallback:
  E8 rel32            -> mnemonic "call", operand 0 is X86_OP_IMM (capstone
                          resolves the relative displacement to an absolute
                          target already).
  FF 15 disp32         -> mnemonic "call", operand 0 is X86_OP_MEM with
                          base==0 and index==0 (disp is the slot address).
  68 imm32 (push)      -> mnemonic "push", first opcode byte 0x68, operand 0
                          is X86_OP_IMM.
  B8+r imm32 (mov)     -> mnemonic "mov", first opcode byte in [0xB8, 0xBF],
                          operand 1 is X86_OP_IMM.
  C7 05 disp32 imm32   -> mnemonic "mov", first opcode byte 0xC7, operand 0
                          is X86_OP_MEM with base==0/index==0 (the ModRM
                          byte for that combination is exactly 0x05, i.e.
                          "C7 05"); both the disp32 destination and the
                          imm32 value stored there are taken as address
                          candidates.
  8D / 8B / A1 disp32  -> "lea reg, [disp32]" / "mov reg, [disp32]" /
                          "mov eax, [moffs32]" respectively, all X86_OP_MEM
                          with base==0/index==0 (A1 has no base/index by
                          construction).
  FF 25 disp32, E9 rel32 -> decoded normally as ordinary control-flow
                          instructions (indirect jmp / direct jmp) by
                          capstone's linear sweep so the sweep does not lose
                          sync; per the task's output schema (which defines
                          callees only from direct CALL targets) neither
                          contributes to any output field. This is a
                          deliberate scope choice, not an oversight: FF 25
                          import-jmp-thunk slots are not merged into
                          import_calls, and E9 tail-call targets are not
                          merged into callees.

Because capstone was available, no hand-rolled fallback decoder was written.
Had it been needed, its stated limitations would have been: no prefix
handling (operand-size/segment overrides), no ModRM/SIB decode beyond the
exact forms above (any other addressing mode for MOV/LEA is silently
skipped, undercounting strings/data_refs), and a byte-at-a-time
resynchronization on unknown opcodes rather than a proper "skip one
instruction" recovery, which drifts alignment more readily than capstone's
skipdata heuristic. capstone's own skipdata is itself a heuristic (it skips
one byte at a time when a byte sequence does not decode), which is what
"skip bytes that decode as data mid-function gracefully" relies on here --
it can still occasionally resynchronize mid-instruction rather than at the
true next instruction boundary; no static disassembler is exact over data
mixed into a code section.

Usage:
    rtk python3 tools/analysis/ntsc_callgraph.py
    rtk python3 tools/analysis/ntsc_callgraph.py \
        --xbe halo-patched/cachebeta.xbe \
        --bounds tools/verify/function_bounds.json \
        --kb kb.json \
        --out artifacts/ntsc_callgraph/callgraph.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import time
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import capstone
    from capstone import x86_const as X86
except ImportError:
    sys.exit(
        "ERROR: capstone is required (found at .venv 5.0.7 when this script "
        "was written; already an undeclared dependency of "
        "tools/audit/check_arg_counts.py and "
        "tools/audit/dump_caller_regsetup.py). Run this script with the "
        "project venv's interpreter, e.g. `rtk python3 "
        "tools/analysis/ntsc_callgraph.py`."
    )

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_XBE = REPO_ROOT / "halo-patched" / "cachebeta.xbe"
DEFAULT_BOUNDS = REPO_ROOT / "tools" / "verify" / "function_bounds.json"
DEFAULT_KB = REPO_ROOT / "kb.json"
DEFAULT_OUT = REPO_ROOT / "artifacts" / "ntsc_callgraph" / "callgraph.json"

SECTION_HEADER_SIZE = 0x38  # bytes, per-entry, in the XBE section header table
MAX_STRING_LEN = 200
MIN_STRING_LEN = 4
DEFAULT_MAX_FALLBACK_SPAN = 4096  # bytes; see resolve_function_bounds()

_NAME_FROM_DECL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# Same pattern as knowledge.py's reg_filter_re -- stripped from decl before
# name extraction below so the two tools agree byte-for-byte on what "the
# decl-derived name" means, even though in practice @<reg> annotations only
# ever appear inside the parameter list (after the function name's own
# opening paren) and so do not change what _NAME_FROM_DECL_RE's leftmost
# search finds. Stripping first removes any doubt.
_REG_ANNOTATION_RE = re.compile(r"@<(\w+)>")


# ---------------------------------------------------------------------------
# XBE parsing
# ---------------------------------------------------------------------------

class Section:
    __slots__ = ("name", "va", "vsize", "raw_off", "raw_size")

    def __init__(self, name: str, va: int, vsize: int, raw_off: int, raw_size: int):
        self.name = name
        self.va = va
        self.vsize = vsize
        self.raw_off = raw_off
        self.raw_size = raw_size

    def contains_va(self, v: int) -> bool:
        return self.va <= v < self.va + self.vsize

    def raw_contains_va(self, v: int) -> bool:
        return self.va <= v < self.va + self.raw_size


def load_xbe(path: Path) -> Tuple[bytes, int, List[Section], str]:
    """Parse the XBE section table, including section names (not read by
    prior audit tools in this repo). Returns (raw_bytes, base_va, sections,
    md5_hex)."""
    data = path.read_bytes()
    if data[:4] != b"XBEH":
        raise ValueError(f"{path}: not a valid XBE file (bad magic)")

    base = struct.unpack_from("<I", data, 0x104)[0]
    n_sects = struct.unpack_from("<I", data, 0x11C)[0]
    hdrs_va = struct.unpack_from("<I", data, 0x120)[0]
    hdr_off = hdrs_va - base

    sections: List[Section] = []
    for i in range(n_sects):
        off = hdr_off + i * SECTION_HEADER_SIZE
        va = struct.unpack_from("<I", data, off + 0x04)[0]
        vsize = struct.unpack_from("<I", data, off + 0x08)[0]
        raw_off = struct.unpack_from("<I", data, off + 0x0C)[0]
        raw_size = struct.unpack_from("<I", data, off + 0x10)[0]
        name_va = struct.unpack_from("<I", data, off + 0x14)[0]
        name_off = name_va - base
        name = data[name_off:name_off + 64].split(b"\0", 1)[0].decode("ascii", "replace")
        sections.append(Section(name, va, vsize, raw_off, raw_size))

    md5 = hashlib.md5(data).hexdigest()
    return data, base, sections, md5


def read_va(data: bytes, sections: List[Section], va: int, length: int) -> bytes:
    """Read up to `length` raw bytes starting at VA `va`. Returns b"" for any
    VA past a section's raw-backed extent (its bss-style zero-fill tail is
    never materialized in the file, so it can never decode as a string)."""
    for sec in sections:
        if sec.va <= va < sec.va + sec.vsize:
            off_in_sec = va - sec.va
            avail_raw = sec.raw_size - off_in_sec
            if avail_raw <= 0:
                return b""
            file_off = sec.raw_off + off_in_sec
            return data[file_off: file_off + min(length, avail_raw)]
    return b""


def section_for_va(sections: List[Section], va: int) -> Optional[Section]:
    for sec in sections:
        if sec.contains_va(va):
            return sec
    return None


def classify_value_section(sections_by_name: Dict[str, Section], v: int) -> Optional[str]:
    """Classify an absolute-address literal as 'rdata', 'data', 'bss', or
    None (anything else -- .text, an unrelated section, or unmapped).

    This XBE has no section literally named ".bss": .data's virtual size
    (vsize) exceeds its raw size, and that trailing, zero-filled-at-load
    range is the synthesized ".bss" region, per standard PE/XBE convention.
    """
    rdata = sections_by_name.get(".rdata")
    if rdata is not None and rdata.contains_va(v):
        return "rdata"
    data = sections_by_name.get(".data")
    if data is not None:
        if data.raw_contains_va(v):
            return "data"
        if data.contains_va(v):
            return "bss"
    return None


def try_decode_string(data: bytes, sections: List[Section], va: int) -> Optional[str]:
    raw = read_va(data, sections, va, MAX_STRING_LEN + 1)
    if not raw:
        return None
    out = bytearray()
    for b in raw:
        if 0x20 <= b <= 0x7E:
            out.append(b)
            if len(out) >= MAX_STRING_LEN:
                break
        else:
            break
    if len(out) >= MIN_STRING_LEN:
        return out.decode("ascii")
    return None


# ---------------------------------------------------------------------------
# kb.json / function_bounds.json loading
# ---------------------------------------------------------------------------

class KbFunc:
    __slots__ = ("addr", "name", "object", "decl", "kb_name")

    def __init__(self, addr: int, name: str, object_: str, decl: str, kb_name: Optional[str] = None):
        self.addr = addr
        self.name = name
        self.object = object_
        self.decl = decl
        # The kb.json row's explicit "name" field, kept alongside the
        # decl-derived `name` above only when it differs and is informative
        # (see load_kb() for why `name` no longer prefers this field).
        self.kb_name = kb_name


def load_kb(path: Path) -> Dict[int, KbFunc]:
    """Load every function from kb.json's `.objects[].functions[]` groups.
    (kb.json also carries ~87 top-level "0x..." address keys, but every one
    of those duplicates an entry already inside .objects[]; .objects[] is
    the complete, non-redundant 9,133-function source of truth.)

    `name` is always the decl-derived identifier -- the same rule
    cea_body.py's function_name() and knowledge.py's _extract_name_regex()
    use -- not kb.json's optional explicit "name" field. Previously this
    preferred the explicit "name" field when present, which put this tool
    out of step with cea_body.py and knowledge.py: at least five addresses
    (e.g. 0x10a930, kb name "transition_table_fill" but decl
    "FUN_0010a930(...)") got a different primary name here than in the two
    other tools, so a lookup keyed on this tool's callgraph output could
    silently miss the same function elsewhere. The explicit name, when
    present and different, is kept on kb_name for display/debugging, and
    cea_body.py separately indexes kb.json by both spellings so it resolves
    either one."""
    with open(path, "r", encoding="utf-8") as f:
        kb = json.load(f)

    funcs: Dict[int, KbFunc] = {}
    for obj in kb.get("objects", []):
        obj_name = obj.get("name", "?")
        for fn in obj.get("functions", []):
            addr_str = fn.get("addr", "")
            if not addr_str.startswith("0x"):
                continue
            try:
                addr = int(addr_str, 16)
            except ValueError:
                continue
            decl = fn.get("decl", "")
            cleaned_decl = _REG_ANNOTATION_RE.sub("", decl)
            m = _NAME_FROM_DECL_RE.search(cleaned_decl)
            name = m.group(1) if m else f"FUN_{addr:08x}"
            explicit_name = fn.get("name")
            kb_name = explicit_name if explicit_name and explicit_name != name else None
            funcs[addr] = KbFunc(addr, name, obj_name, decl, kb_name)
    return funcs


def load_bounds(path: Path) -> Tuple[Dict[int, int], dict]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    meta = raw.get("_meta", {})
    bounds: Dict[int, int] = {}
    for k, v in raw.items():
        if k == "_meta" or not k.startswith("0x"):
            continue
        try:
            addr = int(k, 16)
            end = int(v["end"], 16)
        except (KeyError, TypeError, ValueError):
            continue
        bounds[addr] = end
    return bounds, meta


# ---------------------------------------------------------------------------
# Bounds resolution: bounds.json entry, else next known start in section
# ---------------------------------------------------------------------------

def resolve_function_bounds(
    kb_funcs: Dict[int, KbFunc],
    bounds: Dict[int, int],
    sections: List[Section],
    max_fallback_span: Optional[int] = None,
) -> Tuple[Dict[int, Tuple[int, Optional[Section], str, bool]], int, int, int]:
    """Return (addr -> (end, owning_section, bounds_source, span_capped),
    functions_with_bounds_entry, functions_using_fallback, functions_capped).

    bounds_source is "table" when tools/verify/function_bounds.json had an
    entry for this address, "fallback" when the end was instead guessed as
    "starts at the next known function in the same section, or the section
    end if this is the last one" (mirrors
    ghidra_scripts/ExportFunctionSizes.java's clamp rule). The fallback rule
    has no way to know that the next known *kb.json* function isn't actually
    the next real function -- large gaps of unlisted static helpers, padding,
    or literal data between two kb.json entries all inflate a fallback span
    silently (e.g. FUN_001f9d1d measured at a 19,367-byte fallback span).
    When max_fallback_span is given, a fallback span longer than it is
    clamped to addr + max_fallback_span and span_capped is set True, so
    downstream consumers (and this script's own disassembly loop, which
    slices instructions using this same end value) don't attribute a large
    stretch of probably-unrelated code to one function on a guess. Bounds
    that came from the table are never capped -- they are measured, not
    guessed."""
    addr_section: Dict[int, Optional[Section]] = {}
    by_section: Dict[int, List[int]] = defaultdict(list)
    for addr in kb_funcs:
        sec = section_for_va(sections, addr)
        addr_section[addr] = sec
        if sec is not None:
            by_section[id(sec)].append(addr)
    for lst in by_section.values():
        lst.sort()

    result: Dict[int, Tuple[int, Optional[Section], str, bool]] = {}
    with_bounds = 0
    fallback = 0
    capped = 0
    for addr in kb_funcs:
        sec = addr_section[addr]
        span_capped = False
        if addr in bounds:
            with_bounds += 1
            bounds_source = "table"
            end = bounds[addr]
            if sec is not None:
                end = min(end, sec.va + sec.vsize)
            end = max(end, addr + 1)
        else:
            fallback += 1
            bounds_source = "fallback"
            end = None
            if sec is not None:
                group = by_section[id(sec)]
                idx = bisect_right(group, addr)
                end = group[idx] if idx < len(group) else sec.va + sec.vsize
            if end is None or end <= addr:
                end = addr + 1
            if max_fallback_span is not None and end - addr > max_fallback_span:
                end = addr + max_fallback_span
                span_capped = True
                capped += 1
        result[addr] = (end, sec, bounds_source, span_capped)
    return result, with_bounds, fallback, capped


# ---------------------------------------------------------------------------
# Disassembly / per-instruction classification
# ---------------------------------------------------------------------------

def make_disassembler() -> "capstone.Cs":
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    md.skipdata = True
    return md


def process_instruction(
    insn,
    data: bytes,
    sections: List[Section],
    sections_by_name: Dict[str, Section],
    known_starts: set,
    fn_acc: dict,
) -> Optional[int]:
    """Update fn_acc in place for one instruction. Returns a resolved callee
    address if this instruction was a direct call to a known function start,
    else None."""
    mnem = insn.mnemonic
    if mnem == ".byte":
        # capstone skipdata pseudo-instruction (undecodable byte skipped
        # during the linear sweep) -- no operand detail is available for
        # these; nothing to classify.
        return None
    b0 = insn.bytes[0] if insn.bytes else None
    ops = insn.operands

    if mnem == "call":
        if ops and ops[0].type == X86.X86_OP_IMM:
            target = ops[0].imm & 0xFFFFFFFF
            if target in known_starts:
                fn_acc["callees"].add(target)
                return target
            fn_acc["unresolved_calls"] += 1
        elif ops and ops[0].type == X86.X86_OP_MEM:
            mem = ops[0].mem
            if mem.base == 0 and mem.index == 0:
                fn_acc["import_calls"].add(mem.disp & 0xFFFFFFFF)
            # else: register/SIB-indirect call (vtable/function-pointer
            # dispatch) -- target not statically known, not tracked.
        return None

    candidates: List[int] = []
    if mnem == "push" and b0 == 0x68 and ops and ops[0].type == X86.X86_OP_IMM:
        candidates.append(ops[0].imm & 0xFFFFFFFF)
    elif mnem == "mov" and b0 is not None and 0xB8 <= b0 <= 0xBF:
        if len(ops) == 2 and ops[1].type == X86.X86_OP_IMM:
            candidates.append(ops[1].imm & 0xFFFFFFFF)
    elif mnem == "mov" and b0 == 0xC7:
        if len(ops) == 2 and ops[0].type == X86.X86_OP_MEM:
            dst_mem = ops[0].mem
            if dst_mem.base == 0 and dst_mem.index == 0:
                candidates.append(dst_mem.disp & 0xFFFFFFFF)
                if ops[1].type == X86.X86_OP_IMM:
                    candidates.append(ops[1].imm & 0xFFFFFFFF)
    elif mnem == "mov" and b0 == 0x8B:
        if len(ops) == 2 and ops[1].type == X86.X86_OP_MEM:
            src_mem = ops[1].mem
            if src_mem.base == 0 and src_mem.index == 0:
                candidates.append(src_mem.disp & 0xFFFFFFFF)
    elif mnem == "mov" and b0 == 0xA1:
        if len(ops) == 2 and ops[1].type == X86.X86_OP_MEM:
            candidates.append(ops[1].mem.disp & 0xFFFFFFFF)
    elif mnem == "lea" and b0 == 0x8D:
        if len(ops) == 2 and ops[1].type == X86.X86_OP_MEM:
            src_mem = ops[1].mem
            if src_mem.base == 0 and src_mem.index == 0:
                candidates.append(src_mem.disp & 0xFFFFFFFF)

    for v in candidates:
        sect = classify_value_section(sections_by_name, v)
        if sect in ("rdata", "data"):
            s = try_decode_string(data, sections, v)
            if s:
                fn_acc["strings"].add(s)
                continue
        if sect in ("data", "bss"):
            fn_acc["data_refs"].add(v)
    return None


def run_sweep(
    data: bytes,
    sections: List[Section],
    kb_funcs: Dict[int, KbFunc],
    resolved: Dict[int, Tuple[int, Optional[Section]]],
) -> Tuple[dict, dict, int, int]:
    """Single linear-disassembly pass per section that owns >=1 kb function.
    Returns (per_func, callers_map, total_instructions, decode_errors)."""
    sections_by_name: Dict[str, Section] = {}
    for sec in sections:
        sections_by_name.setdefault(sec.name, sec)

    known_starts = set(kb_funcs.keys())

    per_func: Dict[int, dict] = {
        addr: {
            "callees": set(),
            "unresolved_calls": 0,
            "import_calls": set(),
            "strings": set(),
            "data_refs": set(),
        }
        for addr in kb_funcs
    }
    callers_map: Dict[int, set] = defaultdict(set)

    # Group owning sections and, per section, the sorted (start, end) list of
    # kb functions living there.
    sections_needed: Dict[int, Section] = {}
    members_by_section: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
    for addr, (end, sec, _bounds_source, _span_capped) in resolved.items():
        if sec is not None:
            sections_needed[id(sec)] = sec
            members_by_section[id(sec)].append((addr, end))
    for lst in members_by_section.values():
        lst.sort()

    total_instructions = 0
    decode_errors = 0
    md = make_disassembler()

    for sec_id, sec in sections_needed.items():
        members = members_by_section[sec_id]
        starts = [a for a, _ in members]
        ends = [e for _, e in members]
        n = len(members)
        cur_idx = 0

        raw = data[sec.raw_off: sec.raw_off + sec.raw_size]
        try:
            insns = md.disasm(raw, sec.va)
        except Exception:
            decode_errors += 1
            continue

        for insn in insns:
            total_instructions += 1
            va = insn.address
            while cur_idx < n and va >= ends[cur_idx]:
                cur_idx += 1
            if cur_idx >= n:
                break  # past the last known function in this section
            if va < starts[cur_idx]:
                continue  # inter-function gap/padding -- not our function
            fn_addr = starts[cur_idx]
            fn_acc = per_func[fn_addr]
            try:
                callee = process_instruction(
                    insn, data, sections, sections_by_name, known_starts, fn_acc
                )
            except Exception:
                decode_errors += 1
                continue
            if callee is not None:
                callers_map[callee].add(fn_addr)

    return per_func, callers_map, total_instructions, decode_errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe", type=Path, default=DEFAULT_XBE)
    ap.add_argument("--bounds", type=Path, default=DEFAULT_BOUNDS)
    ap.add_argument("--kb", type=Path, default=DEFAULT_KB)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--max-fallback-span", type=int, default=DEFAULT_MAX_FALLBACK_SPAN,
                     help="cap a fallback (bounds_source=\"fallback\") function span to this many "
                          "bytes from its start address; a table-sourced span is never capped. "
                          "Pass 0 to disable capping entirely. Default: %(default)s bytes.")
    args = ap.parse_args()
    max_fallback_span = args.max_fallback_span if args.max_fallback_span > 0 else None

    t0 = time.perf_counter()

    data, base, sections, xbe_md5 = load_xbe(args.xbe)
    kb_funcs = load_kb(args.kb)
    bounds, bounds_meta = load_bounds(args.bounds)

    bounds_meta_md5 = bounds_meta.get("xbe_md5")
    md5_matches = (bounds_meta_md5 == xbe_md5)

    resolved, with_bounds, fallback, capped = resolve_function_bounds(
        kb_funcs, bounds, sections, max_fallback_span
    )

    outside_any_section = sum(1 for _, sec, _bs, _sc in resolved.values() if sec is None)

    per_func, callers_map, total_instructions, decode_errors = run_sweep(
        data, sections, kb_funcs, resolved
    )

    functions_out: Dict[str, dict] = {}
    total_callee_edges = 0
    total_unresolved = 0
    total_import_sites = 0
    all_strings: set = set()
    all_data_refs: set = set()
    functions_with_callees = 0

    for addr, fn in sorted(kb_funcs.items()):
        end, _sec, bounds_source, span_capped = resolved[addr]
        acc = per_func[addr]
        callees_sorted = sorted(hex(c) for c in acc["callees"])
        import_calls_sorted = sorted(hex(c) for c in acc["import_calls"])
        strings_sorted = sorted(acc["strings"])
        data_refs_sorted = sorted(hex(c) for c in acc["data_refs"])

        functions_out[hex(addr)] = {
            "addr": hex(addr),
            "end": hex(end),
            "name": fn.name,
            "kb_name": fn.kb_name,
            "object": fn.object,
            "bounds_source": bounds_source,
            "span_capped": span_capped,
            "callees": callees_sorted,
            "unresolved_calls": acc["unresolved_calls"],
            "import_calls": import_calls_sorted,
            "strings": strings_sorted,
            "data_refs": data_refs_sorted,
        }

        total_callee_edges += len(callees_sorted)
        total_unresolved += acc["unresolved_calls"]
        total_import_sites += len(import_calls_sorted)
        all_strings.update(strings_sorted)
        all_data_refs.update(data_refs_sorted)
        if callees_sorted:
            functions_with_callees += 1

    callers_out = {
        hex(callee): sorted(hex(c) for c in callers)
        for callee, callers in sorted(callers_map.items())
    }

    elapsed = time.perf_counter() - t0

    summary = {
        "xbe_path": str(args.xbe),
        "xbe_md5": xbe_md5,
        "bounds_meta_xbe_md5": bounds_meta_md5,
        "xbe_md5_matches_bounds_meta": md5_matches,
        "kb_functions_total": len(kb_funcs),
        "functions_with_bounds_entry": with_bounds,
        "functions_using_fallback_end": fallback,
        "functions_with_fallback_span_capped": capped,
        "max_fallback_span_bytes": max_fallback_span,
        "functions_outside_any_known_section": outside_any_section,
        "bounds_table_coverage_pct": round(100.0 * with_bounds / len(kb_funcs), 2) if kb_funcs else 0.0,
        "sections_disassembled": sorted(
            {sec.name for _, sec, _bs, _sc in resolved.values() if sec is not None}
        ),
        "total_instructions_decoded": total_instructions,
        "functions_with_decode_errors": decode_errors,
        "functions_with_at_least_one_callee": functions_with_callees,
        "total_unique_caller_callee_edges": total_callee_edges,
        "total_unresolved_call_sites": total_unresolved,
        "total_unique_import_call_sites": total_import_sites,
        "total_unique_strings": len(all_strings),
        "total_unique_data_refs": len(all_data_refs),
        "runtime_seconds": round(elapsed, 3),
    }

    out_doc = {
        "functions": functions_out,
        "callers": callers_out,
        "summary": summary,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out_doc, f, indent=1, sort_keys=False)
        f.write("\n")

    print(json.dumps(summary, indent=2))
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
