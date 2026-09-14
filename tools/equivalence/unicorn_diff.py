#!/usr/bin/env python3
"""Unicorn-Engine differential tester for pure x86 functions.

Compares MSVC-compiled delinked .obj (oracle) against clang-compiled lifted
.obj (candidate) by running both in separate Unicorn x86 emulators with
identical inputs and comparing CPU+FPU state at RET.

QUICK START
-----------
    # Basic smoke test (leaf function, 100 seeds):
    python3 tools/equivalence/unicorn_diff.py <func_name>

    # Non-leaf function (has external calls like csmemcpy, fabs):
    python3 tools/equivalence/unicorn_diff.py <func_name> --allow-stubs

    # FPU-heavy function with float output buffers:
    python3 tools/equivalence/unicorn_diff.py <func_name> --allow-stubs --float-tolerance 32

    # Detailed per-seed state dump:
    python3 tools/equivalence/unicorn_diff.py <func_name> --allow-stubs --verbose

    # Run self-test against built-in leaf function:
    python3 tools/equivalence/unicorn_diff.py --self-test

FLAGS
-----
    --seeds N          Number of test vectors (default: 100).
    --seed N           Base RNG seed for reproducibility (default: 0).
    --allow-stubs      Enable non-leaf emulation.  Patches DIR32 data
                       relocations, redirects REL32 calls to stub sentinels,
                       and emulates known callees (csmemcpy, fabs, _chkstk,
                       etc.) inline.  Required for any function that calls
                       other functions or references global data.
    --float-tolerance N
                       ULP (Unit in the Last Place) tolerance for float*
                       scratch buffer comparison.  When set, float pointer
                       params are compared element-by-element allowing up to
                       N ULP difference per float.  Non-float slots remain
                       byte-exact.  Auto-detects float* params from the
                       function declaration.  Typical values:
                         16  — tight, catches real bugs (recommended default)
                         32  — moderate, tolerates 1-2 extra rounding steps
                        256  — loose, for functions with long FPU chains
                       1024  — very loose, for extreme count=128 degenerates
    --float-params NAMES
                       Comma-separated param names to treat as float arrays.
                       Only needed when auto-detection guesses wrong.
    --verbose          Print per-seed register dump, FPU state, stub trace,
                       and scratch buffer diff for failures.
    --output-json PATH Write structured JSON result for CI/automation.
    --batch-classify   Classify every function in
                       tools/verify/function_bounds.json against the pristine
                       XBE as leaf / data_only / stubbable / non_leaf, into
                       leaf_cache.json.
    --list-funcs OBJ   List function symbols in a .obj file.

HOW IT WORKS
------------
1. Locates the oracle (delinked/*.obj from the original XBE) and the lifted
   candidate (build/*.obj compiled from our C sources).
2. Parses the function declaration from kb.json to determine parameter types,
   calling convention, and return type.
3. Generates typed test seeds: corner-case values for scalars, float arrays
   for pointer params, sanitized for valid-path execution.
4. For each seed, runs both oracle and lifted in separate Unicorn x86 (i386)
   instances with identical memory layout:
     - Stack (1 MB), code, and a globals region for DIR32 targets seeded from
       known XBE data
     - Scratch buffer for pointer params (1 KB per param)
     - Stub sentinels for intercepted calls
   Every base address lives in `memmap.py` -- do not restate them here; the
   ones this docstring used to name were already stale.
5. Compares EAX/EDX (integer return), ST0 (float return), and scratch buffer.
   With --float-tolerance, float* scratch slots use ULP comparison instead of
   byte-exact.

GLOBALS SEEDING
---------------
The oracle COFF has DIR32 relocations like DAT_002533c8 that reference XBE
data addresses.  These are patched into the GLOBALS region (see memmap.py).
_KNOWN_GLOBAL_BYTES maps original XBE addresses to their canonical values
(0x2533c0=0.0f, 0x2533c8=1.0f, etc.).  After patching, _build_globals_seeds
writes the correct values into the GLOBALS slots so the oracle reads the same
data as the real binary.  The lifted code accesses these addresses directly
(via *(float*)0x2533c0 in C) and gets correct values from auto-mapped pages.

STUB EMULATION
--------------
External calls are redirected to sentinel addresses.  Known stubs:
  - _chkstk: stack probe, no-op in Unicorn (stack is pre-allocated)
  - csmemcpy / memcpy: reads src, writes dst, returns dst
  - fabs: x87 FABS instruction on ST0
  - _display_assert / system_exit: no-op (return 0)
All other stubs return 0/EAX or 0.0/ST0 depending on ABI.

INTERPRETING RESULTS
--------------------
  "4 passed, 0 failed"           — Oracle and lifted are equivalent for
                                   these seeds.  Genuine equivalence is
                                   likely (especially with --seeds 100+).
  "14 passed, 6 failed"          — Look at the failure details.  If the
                                   diff is in float scratch and ULP < 32,
                                   use --float-tolerance.  If EDX or EAX
                                   differs, there's a real logic bug.
  "0 passed, N failed, M errors"  — Errors mean the emulator crashed
                                   (unhandled stub, unmapped memory, stack
                                   overflow).  Check --verbose output for
                                   the crash address and missing stubs.

To add a new known global, edit _KNOWN_GLOBAL_BYTES near the top of this
file.  To add a new stub handler, see StubManager.execute_stub() in
stubs.py.
"""

import argparse
import json
import re
import re as _re_mod
import os
import re
import struct
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Optional

# unicorn lives in the project venv; allow the script to find it even when
# invoked via system python (e.g. `rtk python3`).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_VENV_SP = _REPO_ROOT / ".venv" / "lib" / "python3.12" / "site-packages"
if _VENV_SP.exists() and str(_VENV_SP) not in sys.path:
  sys.path.insert(0, str(_VENV_SP))

try:
  import unicorn  # noqa: F401  (probe)
  _UNICORN_IMPORT_ERROR = ""
except ImportError as exc:
  _UNICORN_IMPORT_ERROR = str(exc)

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_TOOLS_DIR = _SCRIPT_DIR.parent
_REPO_ROOT = _TOOLS_DIR.parent
sys.path.insert(0, str(_TOOLS_DIR))

KB_JSON = _REPO_ROOT / "kb.json"
OBJDIFF_JSON = _REPO_ROOT / "objdiff.json"
DELINKED_DIR = _REPO_ROOT / "delinked"
BUILD_DIR = _REPO_ROOT / "build"

# ---------------------------------------------------------------------------
# Unicorn memory layout constants
# ---------------------------------------------------------------------------
# Declared once in memmap.py, which also self-tests that the regions are
# pairwise disjoint, that none of them lands inside the pristine XBE's image
# span, and that STACK/CODE/GLOBALS stay below HEAP_LINE.  CODE_BASE,
# STACK_BASE and GLOBALS_BASE all used to sit INSIDE the image (.text, .data,
# .data) and were moved there for the raw-XBE oracle.
from memmap import (  # noqa: E402  (kept where the constants used to live)
    CODE_BASE, CODE_SIZE,
    STACK_BASE, STACK_SIZE, STACK_TOP,
    SCRATCH_BASE, SCRATCH_SIZE,
    TRAMP_BASE, TRAMP_SIZE,
    FXSAVE_BASE, FXSAVE_SIZE,
    HEAP_LINE,
    FAKE_RET_ADDR,
)

MAX_INSN       = 100_000      # hard limit on emulated instructions
TIMEOUT_MS     = 5_000        # 5 second timeout

# Backstop cap on the number of DISTINCT (address, size) keys retained by the
# --mem-trace differential. The write hook coalesces writes into a final-value
# map keyed by (address, size) — the exact form compare_mem_traces() consumes —
# so a rep-stosd loop (a single instruction whose count comes from a random
# seed) collapses to the handful of addresses it touches instead of appending a
# MemoryWrite per iteration. That unbounded append accumulated >11 GB and OOM-
# killed the CI runner (2026-07-06). Coalescing makes memory scale with the
# working set, not the write count. This cap only engages if a run touches an
# absurd number of DISTINCT addresses; past it we stop adding new keys (existing
# keys still update) and mark the trace truncated. Override with
# HALO_EQUIV_MEM_TRACE_CAP (0 disables the cap).
try:
    MEM_TRACE_CAP = int(os.environ.get("HALO_EQUIV_MEM_TRACE_CAP", "500000"))
except ValueError:
    MEM_TRACE_CAP = 500_000

# Frequently used scalar globals that appear as hardcoded absolute addresses in
# lifted C. Preload them so Unicorn sees the same canonical values as the XBE
# instead of faulting or reading synthetic zero pages.
#
# Values are loaded from known_globals.json when it exists, falling back to a
# minimal hardcoded set.  That file is a LIVE CAPTURE, not a static extract:
# `extract_globals.py --json` writes unpadded keys ("4566ec") while every
# committed key is 0x-prefixed and 8 wide ("0x004566ec").  The values describe
# a RUNNING game, which is why they are worth seeding and why they must never
# be asserted to equal the XBE's bytes.
#
# Measured shape of the committed file (7181 keys): 6183 are backed by bytes in
# the image file (5785 .rdata, 534 .data, 22 D3D/BINKDATA) and agree with them
# byte for byte -- zero disagreements.  998 are not: 840 sit in no section at
# all and 158 in a section's BSS tail.  So the capture is NOT an independent
# second opinion about .rdata; it is a 4-or-8-byte-wide echo of the image plus
# a genuinely unique set of runtime values.  Where it echoes, the image is the
# better source, because the image is not width-limited -- see
# `_xbe_global_bytes` below and test_known_globals_vs_xbe.py.
def _load_known_globals():
    """Load global bytes from known_globals.json, falling back to hardcoded defaults."""
    json_path = Path(__file__).resolve().parent / "known_globals.json"
    if json_path.exists():
        import json as _json
        raw = _json.loads(json_path.read_text(encoding="utf-8"))
        result = {}
        for addr_hex, val_hex in raw.items():
            try:
                addr = int(addr_hex, 16)
            except (ValueError, TypeError):
                continue  # skip metadata keys (e.g. "_warning")
            result[addr] = bytes.fromhex(val_hex)
        return result
    return {
        0x253394: struct.pack("<f", 30.0),
        0x253398: struct.pack("<f", 0.5),
        0x2533C0: struct.pack("<f", 0.0),
        0x2533C8: struct.pack("<f", 1.0),
        0x254CB8: struct.pack("<f", 1000.0),
    }


_KNOWN_GLOBAL_BYTES = _load_known_globals()

# --- Load-time truth for the globals the XBE file actually backs -------------
#
# _KNOWN_GLOBAL_BYTES is a capture, and a capture has a WIDTH: whatever the
# extractor happened to sample.  `0x2533d0` is the worked example.  It is the
# 1e-4 epsilon double; the capture holds its low dword `000000e0`, and seeding
# four bytes leaves the high dword zero, so the oracle compares against
# 4.6e-310 instead of 1e-4 and takes the other branch
# (reference_equiv_oracle_reloc_gap).  The image holds all eight bytes,
# `000000e0 e2361a3f`, and always did.
#
# So this is not a second opinion -- it is the same opinion without the width
# limit.  6183 of the file's 7181 entries are file-backed and every one of them
# already agrees with the image byte for byte.  The remaining 998 (no section,
# or a section's BSS tail) have no load-time value at all, and there the
# capture stays the only evidence: that is why this reads with
# `read_va_raw` and returns None rather than zero-filling.
_XBE_GLOBALS_CACHE = None


def _xbe_globals_image():
    """`(raw, secs)` for the pristine XBE, parsed once per process, or None.

    Negative-cached: on a host without the XBE this must not re-stat the file
    once per relocation site.
    """
    global _XBE_GLOBALS_CACHE
    if _XBE_GLOBALS_CACHE is None:
        try:
            import xbe_image
            xbe_image.assert_pristine()
            _XBE_GLOBALS_CACHE = xbe_image.load_xbe()
        except Exception:
            _XBE_GLOBALS_CACHE = ()
    return _XBE_GLOBALS_CACHE or None


def _xbe_global_bytes(addr: int, size: int = 256):
    """Load-time bytes at `addr`, or None where the FILE does not back them.

    `read_va_raw`, not `read_va`: read_va zero-fills a section's BSS tail with
    loader semantics, which is right for an emulated oracle but wrong here.
    A zero-filled BSS read is indistinguishable from a genuinely zero-
    initialised global, and seeding those zeros would shadow the capture --
    which for the 998 unbacked addresses is the only evidence there is.
    """
    img = _xbe_globals_image()
    if img is None:
        return None
    import xbe_image
    raw, secs = img
    return xbe_image.read_va_raw(raw, secs, addr, size) or None


# One DIR32 globals slot is 256 bytes wide.  Seeding a whole slot from the
# image keeps byte offsets WITHIN a slot coherent (a consumer reading slot+8
# sees what the original read at orig_addr+8) without ever reaching the next
# slot's first byte.
_GLOBALS_SLOT_STRIDE = 256

# Delinked COFF switch tables sometimes keep the table label but lose the table
# entries themselves (all zeroes, no internal relocations).  Seed only tables we
# have binary-backed evidence for; entries below are from cachebeta.xbe memory.
_ORACLE_SWITCH_TABLE_FIXUPS = {
    0x0002CDB0: {
        "switchD_0002ce68::switchdataD_0002d334": (
            0x0002CE6F, 0x0002CF62, 0x0002CEC7, 0x0002CFC9,
        ),
    },
    0x000D04D0: {
        "switchD_000d05d1::switchdataD_000d0b70": (
            0x000D05D8, 0x000D05D8, 0x000D065C, 0x000D091C,
            0x000D0607, 0x000D071F, 0x000D07C1, 0x000D0863,
            0x000D0863, 0x000D06A5, 0x000D08E3,
        ),
    },
    # actor_action_try_to_dive: two 4-entry jump tables (0x20120/0x20130) live
    # just past the RET (0x2011f); a function-only delinked export keeps the
    # table labels but drops the entries.  Targets read directly from the
    # pristine cachebeta.xbe disassembly (JMP [EAX*4+table] case dispatch).
    0x0001FE70: {
        "switchD_0001fef4::switchdataD_00020120": (
            0x0001FEFB, 0x0001FF0D, 0x0001FF1F, 0x0001FF2F,
        ),
        "switchD_00020055::switchdataD_00020130": (
            0x0002005C, 0x0002006C, 0x0002007C, 0x0002008A,
        ),
    },
    # FUN_00141970: two-level MSVC switch — 8-entry pointer table @0x141b38
    # followed by a byte index-map @0x141b58 (codes->table index), both carried
    # by ONE symbol (the movzbl addresses the map as switchdata+0x20).  Entries
    # that are `bytes` are emitted verbatim after the packed pointers; values
    # read from the pristine cachebeta.xbe (ghidra read_memory 0x141b38, 52B).
    0x00141970: {
        "switchD_001419d6::switchdataD_00141b38": (
            0x001419F7, 0x00141A04, 0x001419DD, 0x001419EA,
            0x00141A2A, 0x00141A4B, 0x00141A62, 0x00141ACE,
            # Byte index-map @0x141b58 keyed on ecx=code-1, dispatch bounded to
            # ecx<=0x12 (code 0x13).  Read verbatim from pristine cachebeta.xbe
            # (VA 0x141b58): 00 01 02 03 04, twelve 07 (indices 5..16 -> region
            # default), 05 (index 17 = code 0x12 -> caseD_12), 06 (index 18 =
            # code 0x13 -> caseD_13).  A prior hand-authored copy had THIRTEEN
            # 07s, shifting code 0x12->region (assert) and code 0x13->caseD_12.
            b"\x00\x01\x02\x03\x04" + b"\x07" * 12 + b"\x05\x06",
        ),
    },
}


def _apply_oracle_switch_table_fixups(func_addr: int, function_slice,
                                      rdata_map: dict,
                                      maps_full_text: bool) -> tuple[dict, dict]:
    """Add binary-backed switch table bytes missing from delinked COFF.

    Returns (patched_rdata_map, identity_seeds).

    ``identity_seeds`` maps the switchdata symbol's ORIGINAL XBE virtual
    address -> the same fixup bytes, so they are mapped verbatim at that VA in
    the emulator.  MSVC two-level ``switch`` dispatch emits a byte index-map
    (code -> pointer-table index) read via an ABSOLUTE displacement with NO
    relocation (``movzbl <switchdata+0x20>(ecx), edx``), then an indirect jump
    through the reloc'd pointer table (``jmp [switchdata + edx*4]``).  The
    pointer-table read is redirected to a globals slot by patch_dir32_relocs,
    but the absolute index-map read has no reloc to redirect — left unseeded it
    reads zero, so ``edx`` is always 0 and every code dispatches to table[0]
    (e.g. FUN_00141970 wrote the case-1 value to all four function slots).
    Seeding the block at its original VA makes the absolute read resolve while
    the reloc'd pointer-table read keeps using its slot.
    """
    fixups = _ORACLE_SWITCH_TABLE_FIXUPS.get(func_addr)
    if not fixups:
        return rdata_map, {}

    patched = dict(rdata_map)
    identity_seeds = {}
    section_base_delta = (func_addr - function_slice.section_offset
                          if maps_full_text else func_addr)
    for symbol_name, target_vas in fixups.items():
        if symbol_name in patched:
            continue
        data = bytearray()
        for target_va in target_vas:
            if isinstance(target_va, (bytes, bytearray)):
                data.extend(target_va)  # raw suffix (e.g. byte index-map)
            else:
                data.extend(struct.pack("<I", CODE_BASE + target_va - section_base_delta))
        patched[symbol_name] = bytes(data)
        m = re.search(r'switchdataD_([0-9a-fA-F]+)', symbol_name)
        if m:
            identity_seeds[int(m.group(1), 16)] = bytes(data)
    return patched, identity_seeds


def _relocate_rdata_text_refs(function_slice, rdata_map: dict,
                              maps_full_text: bool) -> dict:
    """Relocate .rdata jump-table entries that point back into .text."""
    reloc_map = getattr(function_slice, "rdata_relocs", {})
    if not reloc_map:
        return rdata_map

    patched = dict(rdata_map)
    base_delta = 0 if maps_full_text else function_slice.section_offset
    for symbol_name, data in rdata_map.items():
        relocs = reloc_map.get(symbol_name)
        if not relocs:
            continue
        chunk = bytearray(data)
        for reloc in relocs:
            if reloc.reloc_type != 0x0006:  # IMAGE_REL_I386_DIR32
                continue
            if not reloc.symbol_name.startswith(".text"):
                continue
            off = reloc.virtual_address
            if off + 4 <= len(chunk):
                target_offset = struct.unpack_from("<I", chunk, off)[0]
                struct.pack_into("<I", chunk, off,
                                 CODE_BASE + target_offset - base_delta)
        patched[symbol_name] = bytes(chunk)
    return patched


def _relocate_text_label_refs(function_slice, code: bytes,
                              maps_full_text: bool) -> bytes:
    """Relocate DIR32 references to intra-.text labels (MSVC switch tables).

    MSVC/VC71 emits ``switch`` jump tables inside the function's own .text
    section: the indirect ``jmp dword ptr [reg*4 + $LNNNNN]`` disp32 and each
    table entry are DIR32 relocations to internal labels ($LNNNNN) defined in
    that same section.  ``patch_dir32_relocs`` skips them (they are in
    ``defined_symbols``), leaving the encoded field at its raw addend (usually
    0), so the jump reads a garbage/zero target and control escapes into
    unmapped memory.  Clang instead parks its tables in .rdata, which the
    rdata_map/_relocate_rdata_text_refs path already handles.

    We resolve each such label to its section-relative offset (captured in
    ``FunctionSlice.text_symbol_offsets``) and patch the field to the address
    the code is loaded at: ``CODE_BASE + (offset - base_delta) + addend``.
    ``base_delta`` is 0 when the full .text section is mapped at CODE_BASE
    (a label at section offset V lands at CODE_BASE+V) and the function's
    ``section_offset`` when only the extracted slice is mapped at CODE_BASE
    (matching ``_relocate_rdata_text_refs``).
    """
    text_offsets = getattr(function_slice, "text_symbol_offsets", None)
    if not text_offsets:
        return code
    patched = bytearray(code)
    base_delta = 0 if maps_full_text else function_slice.section_offset
    slice_lo = function_slice.section_offset
    slice_hi = slice_lo + len(function_slice.code)
    for reloc in function_slice.relocs:
        if reloc.reloc_type != 0x0006:  # IMAGE_REL_I386_DIR32
            continue
        sym = reloc.symbol_name
        # Section symbols (".text"/".rdata"/...) are handled elsewhere; only
        # named intra-section labels are rebased here.
        if sym.startswith("."):
            continue
        # Ghidra-delinked switch DATA symbols (switchD_*::switchdataD_*) are
        # served by _apply_oracle_switch_table_fixups / the globals-slot path;
        # rebasing them here pointed the jmp at slice-relative addresses whose
        # bytes are NOT loaded (table lives outside the extracted slice) ->
        # jmp *0 -> ORACLE-CRASH eip=0 (FUN_000d04d0 regression, 2026-07-23).
        if "switchdata" in sym or sym.startswith("switchD_"):
            continue
        target_off = text_offsets.get(sym)
        if target_off is None:
            continue
        # Only rebase labels whose bytes are actually inside the extracted
        # slice; a label outside it (another function's table/label in a
        # whole-TU delinked obj) cannot be a valid target in this mapping.
        if not maps_full_text and not (slice_lo <= target_off < slice_hi):
            continue
        off = reloc.virtual_address
        if off + 4 <= len(patched):
            addend = struct.unpack_from("<I", patched, off)[0]
            struct.pack_into("<I", patched, off,
                             CODE_BASE + (target_off - base_delta) + addend)
    return bytes(patched)

# ---------------------------------------------------------------------------
# kb.json helpers
# ---------------------------------------------------------------------------

def _load_kb() -> dict:
    with open(KB_JSON) as f:
        return json.load(f)


def _find_kb_entry(kb: dict, func_name: str) -> Optional[dict]:
    """Find a function entry in kb.json by name or hex address."""
    addr_query = func_name if func_name.startswith("0x") else None
    for obj in kb.get("objects", []):
        for fn in obj.get("functions", []):
            decl = fn.get("decl", "")
            # Match function name in decl
            m = re.search(r'\b(\w+)\s*\(', decl)
            fn_name = m.group(1) if m else ""
            if fn_name == func_name or fn.get("addr", "") == addr_query:
                # A function-level "source" overrides the object default: a
                # few objects (e.g. rasterizer.obj) aggregate several TUs, and
                # the object's source names only the primary one.
                return dict(fn, _obj_name=obj.get("name", ""),
                            _obj_source=fn.get("source") or obj.get("source", ""))
    return None


def _find_kb_entry_by_addr(kb: dict, addr: str) -> Optional[dict]:
    addr_norm = addr.lower().lstrip("0x")
    for obj in kb.get("objects", []):
        for fn in obj.get("functions", []):
            a = fn.get("addr", "").lower().lstrip("0x")
            if a == addr_norm:
                return dict(fn, _obj_name=obj.get("name", ""),
                            _obj_source=fn.get("source") or obj.get("source", ""))
    return None


# ---------------------------------------------------------------------------
# Object file discovery
# ---------------------------------------------------------------------------

def _load_objdiff() -> list[dict]:
    with open(OBJDIFF_JSON) as f:
        data = json.load(f)
    return data.get("units", [])


def _find_obj_paths(kb_entry: dict) -> tuple[Optional[Path], Optional[Path]]:
    """Return (delinked_path, build_path) for a kb.json entry.

    Searches objdiff.json units for a unit whose base_path matches the
    obj_name field, then returns both paths.  Also tries direct lookup
    in delinked/.
    """
    obj_name = kb_entry.get("_obj_name", "")
    obj_source = kb_entry.get("_obj_source", "")
    addr = kb_entry.get("addr", "")
    # Strip decorations like "halo/" prefix or ".obj" suffix
    bare = obj_name.replace(".obj", "").replace("LIBCMT:", "")
    source_stem = Path(obj_source).name if obj_source else ""

    units = _load_objdiff()
    delinked_path = None
    build_path = None

    # First pass: per-function unit matching the exact function address in base_path.
    # This must run before the bare-name scan to avoid picking a sibling per-function
    # unit whose target_path happens to match the shared .obj name.
    if addr:
        addr_int = int(addr, 16) if addr.startswith("0x") else int(addr, 16)
        addr_str_lo = f"FUN_{addr_int:08x}"
        addr_str_hi = f"FUN_{addr_int:08X}"
        # Also match short form (e.g. FUN_0013ef0 vs FUN_00013ef0) by checking
        # that the base_path contains the non-zero-padded hex address.
        addr_hex_bare = f"{addr_int:x}"
        for unit in units:
            base = unit.get("base_path", "")
            # vc71_verify's per-function convention names refs
            # delinked/functions/<hex8>.obj without a FUN_ prefix.
            if addr_str_lo in base or addr_str_hi in base or (
                    addr_hex_bare in base and "FUN_" in base) or (
                    Path(base).name == f"{addr_int:08x}.obj"):
                dp = _REPO_ROOT / base
                if dp.exists():
                    delinked_path = dp
                target = unit.get("target_path", "")
                if target:
                    tp = _REPO_ROOT / target
                    if tp.exists() and tp.suffix == ".obj":
                        build_path = tp
                break  # per-function unit is unambiguous; stop here

    if delinked_path and build_path:
        return delinked_path, build_path

    import re as _re
    # Match bare name at a word boundary to prevent "ai" matching "actors_ai..."
    _bare_pat = _re.compile(r'(?<![A-Za-z0-9_])' + _re.escape(bare) + r'\.obj')
    matches = [
        unit for unit in units
        if bare and (_bare_pat.search(unit.get("base_path", ""))
                     or _bare_pat.search(unit.get("target_path", "")))
    ]

    # Multiple units can share the same delinked base_path (e.g. a combined
    # multi-TU delinked export used as the oracle for several candidate build
    # objects), so a bare-name match on base_path alone is ambiguous — it can
    # pair the right oracle with the WRONG build object. Prefer the unit whose
    # target_path is actually the function's source TU (kb.json object
    # "source" field) before falling back to the first bare-name match.
    chosen = None
    if source_stem:
        _source_pat = _re.compile(r'(?<![A-Za-z0-9_])' + _re.escape(source_stem) + r'\.obj$')
        for unit in matches:
            if _source_pat.search(unit.get("target_path", "")):
                chosen = unit
                break
    if chosen is None and matches:
        chosen = matches[0]

    if chosen:
        dp = _REPO_ROOT / chosen.get("base_path", "")
        tp = _REPO_ROOT / chosen.get("target_path", "")
        if dp.exists():
            delinked_path = dp
        if tp.exists() and tp.suffix == ".obj":
            build_path = tp

    # Direct fallback: look for <bare>.obj in delinked/
    if not delinked_path:
        candidate = DELINKED_DIR / f"{bare}.obj"
        if candidate.exists():
            delinked_path = candidate

    return delinked_path, build_path


def _find_build_obj_for_source(source_path: str) -> Optional[Path]:
    """Given a source file path, find the corresponding build .obj."""
    units = _load_objdiff()
    for unit in units:
        src = unit.get("metadata", {}).get("source_path", "")
        if src and (source_path.endswith(src) or src.endswith(source_path)):
            tp = _REPO_ROOT / unit.get("target_path", "")
            if tp.exists():
                return tp

    cmake_obj = BUILD_DIR / "CMakeFiles" / "halo.dir" / "src" / "halo" / f"{source_path}.obj"
    if cmake_obj.exists():
        return cmake_obj

    return None


def _function_aliases(func_name: str) -> set[str]:
    aliases = {func_name.lstrip("_")}
    entry = _find_kb_entry(_load_kb(), func_name)
    if not entry:
        return aliases

    decl = entry.get("decl", "")
    addr = entry.get("addr", "")
    m = re.search(r"\b(\w+)\s*\(", decl)
    if m:
        aliases.add(m.group(1))
    if addr:
        aliases.add(f"FUN_{int(addr, 16):08x}")
    return aliases


def _per_function_ref(func_name: str) -> Optional[Path]:
    for alias in _function_aliases(func_name):
        m = re.match(r"FUN_([0-9a-f]{8})$", alias, re.IGNORECASE)
        if not m:
            continue
        candidate = DELINKED_DIR / "functions" / f"{m.group(1).lower()}.obj"
        if candidate.exists():
            return candidate
    return None


def _compile_build_obj_for_source(source_path: str) -> tuple[Optional[Path], Optional[str]]:
    """Compile a standalone candidate .obj for a source file.

    This is used when a TU is not part of HALO_SOURCES yet, but we still want
    Unicorn/Z3 iteration against the current lifted C.
    """
    src_path = _REPO_ROOT / "src" / "halo" / source_path
    if not src_path.exists():
        return None, f"source file does not exist: {src_path}"

    gen_dir = BUILD_DIR / "generated"
    if not gen_dir.exists():
        return None, f"generated headers missing: {gen_dir}"

    out_dir = BUILD_DIR / "equivalence"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_obj = out_dir / (Path(source_path).name + ".obj")

    cmd = [
        "clang",
        "-Wall", "-Werror",
        "-target", "i386-pc-win32",
        "-march=pentium3",
        "-mno-sse",
        "-nostdlib",
        "-ffreestanding",
        "-fno-builtin",
        "-fno-exceptions",
        "-mstack-probe-size=65536",
        f"-I{_REPO_ROOT / 'src'}",
        f"-I{_REPO_ROOT / 'third_party' / 'xbox'}",
        f"-I{gen_dir}",
        "-include", str(_REPO_ROOT / "src" / "common.h"),
        "-c", str(src_path),
        "-o", str(out_obj),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "clang compile failed"
        return None, detail
    return out_obj, None


def _seed_known_globals(uc, base: int, size: int):
    """Stamp a freshly mapped region with everything known about its addresses.

    Order is deliberate: the capture goes down first and the XBE's own bytes
    over it.  Where the two overlap -- 6183 of the capture's 7181 addresses --
    they are byte-identical today, so the order is only load-bearing for the
    case where a wider image read covers a narrower captured value, and there
    the image is the one that should win.  (It is the capture's symbol NAMES
    that are cross-build and untrustworthy, not these bytes; see
    known_globals.json's own _warning.)
    """
    end = base + size
    for addr, data in _KNOWN_GLOBAL_BYTES.items():
        if base <= addr and addr + len(data) <= end:
            uc.mem_write(addr, data)
    _seed_xbe_initialized(uc, base, size)


def _seed_capture_over_bss(uc, raw: bytes, secs) -> int:
    """Seed the live capture into the mapped image, BSS addresses only.

    The complement of `_seed_xbe_initialized`.  That one fills a synthetic page
    with the image's bytes; this one fills the image's uninitialised holes with
    the capture's bytes.  Between them every global in the span has the best
    evidence available for it, and neither ever overwrites the other's
    territory: file-backed addresses are left exactly as `map_image` wrote
    them, which is what H11 protects.

    Returns the number of addresses seeded.  Called for BOTH instances, so the
    two sides see identical values -- a capture seeded on one side only would
    be a divergence the harness manufactured.
    """
    import xbe_image
    n = 0
    for addr, data in _KNOWN_GLOBAL_BYTES.items():
        if xbe_image.read_va_raw(raw, secs, addr, len(data)):
            continue                     # the image backs it; leave it alone
        if xbe_image.section_at(secs, addr) is None:
            continue                     # outside the span; not our page
        try:
            uc.mem_write(addr, data)
            n += 1
        except Exception:
            pass
    return n


def _seed_xbe_initialized(uc, base: int, size: int):
    """Write the XBE's initialised bytes for whatever part of `[base, base+size)`
    the image file backs.

    Only ever reached for a page the harness mapped ITSELF.  A page of the
    genuinely mapped image already holds these bytes and its callers skip it
    (H11).  A SYNTHETIC zero page at an image address, though, is a page where
    the answer is sitting in a committed file and the harness was guessing
    zero -- which is the `oracle-unmappable` / NULL-guard-early-out failure
    mode this migration exists to remove.
    """
    img = _xbe_globals_image()
    if img is None:
        return
    import xbe_image
    raw, secs = img
    end = base + size
    for raw_s in secs:
        s = xbe_image.as_section(raw_s)
        lo = max(base, s.va)
        hi = min(end, s.va + s.raw_size)
        if hi <= lo:
            continue
        off = s.raw_off + (lo - s.va)
        try:
            uc.mem_write(lo, raw[off:off + (hi - lo)])
        except Exception:
            # A partially-mapped region: the known-globals loop above is
            # equally best-effort, and a missing seed is a weaker oracle, not
            # a wrong one.
            pass


_SYMBOL_ADDR_CACHE = None


def _decl_identifier(decl: str):
    """Extract the declared identifier from a C decl, e.g.
    'data_t *actor_data;' -> 'actor_data', 'char buf[0x10];' -> 'buf'."""
    import re
    if not decl:
        return None
    head = re.split(r'[\[(;]', decl, 1)[0]
    ids = re.findall(r'[A-Za-z_]\w*', head)
    return ids[-1] if ids else None


def _load_symbol_addrs() -> dict:
    """Build {symbol_name: original_address} from kb.json by parsing decls.

    Lets named globals (e.g. actor_data referenced as __imp__actor_data in the
    candidate .obj) resolve to their XBE address so state-snapshot data can
    seed their DIR32 slots."""
    global _SYMBOL_ADDR_CACHE
    if _SYMBOL_ADDR_CACHE is not None:
        return _SYMBOL_ADDR_CACHE
    name2addr = {}
    try:
        kb_path = Path(__file__).resolve().parent.parent.parent / "kb.json"
        import json as _json
        kb = _json.loads(kb_path.read_text(encoding="utf-8"))
        for obj in kb.get("objects", []):
            if not isinstance(obj, dict):
                continue
            for group in ("data", "functions"):
                for sym in obj.get(group, []) or []:
                    addr = sym.get("addr")
                    ident = sym.get("name") or _decl_identifier(sym.get("decl") or "")
                    if addr and ident:
                        try:
                            name2addr.setdefault(ident, int(addr, 16))
                        except ValueError:
                            pass
    except Exception:
        pass
    _SYMBOL_ADDR_CACHE = name2addr
    return name2addr


_FUNC_ADDR_NAME_CACHE = None


def _load_function_addr_to_name() -> dict:
    """Build {address: kb-registered name} from kb.json function entries only.

    Lets a raw 'FUN_XXXXXXXX' reloc symbol (the delinked oracle's name for a
    callee kb.json has since given a friendly name, e.g.
    object_set_automatic_deactivation) canonicalize to the same stub sentinel
    as the candidate's named reloc to that address — otherwise they get
    different sentinels for the same real callee and the stub-arg-diff
    reports a spurious call-sequence divergence."""
    global _FUNC_ADDR_NAME_CACHE
    if _FUNC_ADDR_NAME_CACHE is not None:
        return _FUNC_ADDR_NAME_CACHE
    addr2name = {}
    try:
        kb_path = Path(__file__).resolve().parent.parent.parent / "kb.json"
        import json as _json
        kb = _json.loads(kb_path.read_text(encoding="utf-8"))
        for obj in kb.get("objects", []):
            if not isinstance(obj, dict):
                continue
            for sym in obj.get("functions", []) or []:
                addr = sym.get("addr")
                ident = sym.get("name") or _decl_identifier(sym.get("decl") or "")
                if addr and ident:
                    try:
                        addr2name[int(addr, 16)] = ident
                    except ValueError:
                        pass
    except Exception:
        pass
    _FUNC_ADDR_NAME_CACHE = addr2name
    return addr2name


def _canonicalize_callee_key(sym_key: str) -> str:
    """Map a 'FUN_XXXXXXXX' reloc symbol to its kb.json friendly name, if any.

    Leaves already-named symbols (the normal case for both oracle and
    candidate relocs) unchanged."""
    m = re.match(r'FUN_([0-9a-fA-F]+)$', sym_key)
    if not m:
        return sym_key
    addr = int(m.group(1), 16)
    return _load_function_addr_to_name().get(addr, sym_key)


# Ghidra-labeled globals in delinked refs whose name does NOT match kb.json's
# name for the same address (or matches a DIFFERENT kb global).  Resolved by
# explicit address, checked before the kb name lookup.  Do NOT generic-strip
# the g_ prefix instead: g_object_header_data labels 0x5a8d50 (the object
# table pointer) while kb's object_header_data is 0x5abc10 — a bare strip
# would seed the wrong slot (2026-07-21, object_iterator_next campaign).
_GLOBAL_NAME_ALIASES = {
    "g_object_header_data": 0x5a8d50,
    # Glow trailing-particle datum pool pointer.  The delinked oracle labels it
    # g_glow_particle_data; kb.json has no named global at this address, so the
    # candidate references it as the raw pointer *(data_t**)0x5a90cc.  Alias so
    # both sides seed the same pool (glow.obj equivalence, 2026-07-21).
    "g_glow_particle_data": 0x5a90cc,
}


def _normalize_global_symbol(sym_name: str) -> str:
    """Strip MSVC/clang decoration to recover the bare C identifier:
    '__imp__actor_data' -> 'actor_data', '_actor_data@8' -> 'actor_data'."""
    name = sym_name
    if name.startswith("__imp__"):
        name = name[len("__imp__"):]
    name = name.split("@", 1)[0]
    return name.lstrip("_")


def _snapshot_value_at(snapshot_overrides: dict, addr: int, n: int = 4):
    """Read n bytes at addr from snapshot regions ({region_base: bytes}),
    honoring regions that span addr (not just exact-key matches)."""
    if not snapshot_overrides:
        return None
    for base, data in snapshot_overrides.items():
        if base <= addr and addr + n <= base + len(data):
            off = addr - base
            return data[off:off + n]
    return None


_XBE_SECTIONS_CACHE = None


def _xbe_sections() -> list:
    """Pristine-XBE section headers, loaded once ([] when unavailable)."""
    global _XBE_SECTIONS_CACHE
    if _XBE_SECTIONS_CACHE is None:
        try:
            from extract_globals import _load_xbe_sections, XBE_PATH
            _XBE_SECTIONS_CACHE = (_load_xbe_sections()
                                   if XBE_PATH.exists() else [])
        except Exception:
            _XBE_SECTIONS_CACHE = []
    return _XBE_SECTIONS_CACHE


def _xbe_dir32_symbol_addrs(func_va: int, relocs) -> dict:
    """Resolve each DIR32 relocation's symbol to the absolute address the
    ORIGINAL code stored at that spot, by reading the pristine XBE at
    func_va + reloc_offset.  Returns {symbol_name: original_address}.

    Relocs are function-relative (coff_loader rebases them by func_offset),
    so func_va + r.virtual_address is exactly the dword the linker wrote.
    That makes this ground truth and, unlike name matching, completely
    label-independent: it resolves `g_decals_data` -- a Ghidra label with no
    kb.json counterpart, since kb calls 0x5aa8b8 `global_decal_data` -- just
    as well as `DAT_0032516c`.  Name matching resolved only the latter, so
    the oracle's slot for the former stayed zero while the candidate read
    the seeded pool pointer through its absolute immediate.  Every seed then
    diverged on arg[0] of datum_get and looked like a dropped argument
    (FUN_0015b0c0, 2026-07-28).

    Only DIR32 relocs are read: a DISP32 (call) site holds a displacement,
    not an address.  Values outside the XBE's mapped VA range are ignored,
    and a symbol resolving to two different addresses is dropped rather
    than guessed.
    """
    from stubs import IMAGE_REL_I386_DIR32
    # Pass the un-deduplicated pair list: a symbol relocated at two sites that
    # hold different addresses must be dropped, and a dict would hide that.
    return _xbe_addrs_at_sites(
        (r.symbol_name, func_va + r.virtual_address) for r in relocs
        if getattr(r, "reloc_type", None) == IMAGE_REL_I386_DIR32)


def _xbe_addrs_at_sites(sites) -> dict:
    """{symbol: address} by reading 4 bytes at each relocation SITE in the
    pristine XBE.

    ``sites`` is either {symbol: site_va} or an iterable of (symbol, site_va)
    pairs.  Values outside the XBE's mapped VA range are ignored, and a symbol
    whose sites disagree is dropped rather than guessed.
    """
    import struct as _struct
    secs = _xbe_sections()
    if not secs:
        return {}
    from extract_globals import _read_xbe_bytes
    lo = min(s["vaddr"] for s in secs)
    hi = max(s["vaddr"] + s["vsize"] for s in secs)
    items = sites.items() if isinstance(sites, dict) else sites
    out, ambiguous = {}, set()
    for sym, site in items:
        raw = _read_xbe_bytes(secs, site, 4)
        if not raw or len(raw) != 4:
            continue
        val = _struct.unpack("<I", raw)[0]
        if not (lo <= val < hi):
            continue
        prev = out.get(sym)
        if prev is not None and prev != val:
            ambiguous.add(sym)
            continue
        out[sym] = val
    for sym in ambiguous:
        out.pop(sym, None)
    return out


def _in_image_span(addr: int) -> bool:
    """True when `addr` lies in the pristine XBE's page-aligned image span."""
    lo, hi = _image_span_cached()
    return bool(lo) and lo <= addr < hi


def _build_globals_seeds(*slot_maps: dict,
                         snapshot_overrides: dict = None,
                         sym_addr_hints: dict = None,
                         image_mapped: bool = False) -> dict:
    """Build {slot_address: bytes} from DIR32 slot mappings + _KNOWN_GLOBAL_BYTES
    + optional state-snapshot overrides.

    Each slot_map has symbol_name -> slot_address.  Symbol names like
    DAT_002533c8 encode the original XBE address; named globals (actor_data,
    __imp__swarm_data, ...) are resolved to their address via kb.json.  If that
    address is in snapshot_overrides (live game state) or _KNOWN_GLOBAL_BYTES,
    the slot gets seeded.  Snapshot data wins so the same real table backs both
    the oracle (DAT_-named) and candidate (named) references to a global.
    """
    import re, struct as _struct
    name2addr = _load_symbol_addrs()
    seeds = {}
    for smap in slot_maps:
        for sym_name, slot_addr in smap.items():
            # XBE-derived hints first: the address the original linker wrote
            # at this reloc site is ground truth, so it outranks every name
            # heuristic -- including a bare-name match that lands on a
            # DIFFERENT kb global (the hazard _GLOBAL_NAME_ALIASES documents).
            orig_addr = (sym_addr_hints or {}).get(sym_name)
            if orig_addr is not None:
                pass
            elif (m := re.match(r'(?:DAT|PTR|PTR_FUN|PTR_DAT|s)_([0-9a-fA-F]{4,})',
                                sym_name)):
                orig_addr = int(m.group(1), 16)
            else:
                bare = _normalize_global_symbol(sym_name)
                orig_addr = _GLOBAL_NAME_ALIASES.get(bare)
                if orig_addr is None:
                    orig_addr = name2addr.get(bare)
            if orig_addr is None:
                continue
            snap = _snapshot_value_at(snapshot_overrides, orig_addr, 4)
            # dllimport globals (__imp__X) carry an EXTRA dereference at the use
            # site:  mov eax,[slot]; mov eax,[eax]  — the slot holds &X and the
            # real storage at &X holds the value.  kb.json HDATA globals are
            # __declspec(dllimport), so BOTH the oracle and candidate reference
            # them this way.  When a state snapshot maps the real global page,
            # seed the slot with the global's real ADDRESS so the mapped page
            # supplies the value through that extra deref.  Direct refs (DAT_X)
            # are single-deref and keep value-seeding.
            is_dllimport = sym_name.startswith("__imp_")
            if is_dllimport:
                # Seed the slot with the target's real ADDRESS whenever we have
                # data for it — from a live snapshot or from static known-globals
                # bytes — so the second deref resolves through the same
                # known-globals auto-map path used for direct (DAT_X) refs.
                # Seeding with the VALUE here (as the non-dllimport branches do)
                # would make the second deref read whatever garbage lives at
                # that value-as-address, which is wrong.
                # `image_mapped` makes the last condition unnecessary in
                # principle and load-bearing in practice: with the whole image
                # mapped, the storage at ANY in-span address exists, so the
                # slot can always point at it -- no byte evidence required.
                # Without it a BSS global with no capture entry (e.g.
                # game_state_globals at 0x4ea990, .data past raw_size) left the
                # slot at zero, and the candidate then wrote through a NULL
                # pointer while the oracle wrote to 0x4ea990: one address set
                # each, and a --mem-trace divergence on every seed.
                if (snap is not None
                        or orig_addr in _KNOWN_GLOBAL_BYTES
                        or _xbe_global_bytes(orig_addr, 4) is not None
                        or (image_mapped and _in_image_span(orig_addr))):
                    seeds[slot_addr] = _struct.pack("<I", orig_addr)
            elif snap is not None:
                # Direct value reference (DAT_X).  Seed up to 8 bytes so a
                # constant read as an 8-byte double (e.g. `*(double*)0x2533d0`)
                # is NOT truncated to its low dword.  DIR32 globals slots are
                # 256 bytes apart, so over-seeding a 4-byte consumer is
                # harmless — it only reads its own 4 bytes.  Snapshot overrides
                # take priority over hardcoded known globals.
                seeds[slot_addr] = _snapshot_value_at(snapshot_overrides,
                                                      orig_addr, 8) or snap
            elif (xbe_bytes := _xbe_global_bytes(orig_addr,
                                                 _GLOBALS_SLOT_STRIDE)) is not None:
                # Load-time truth, at full width, straight out of the binary of
                # truth.  This is what retires the 4-vs-8-byte epsilon bug: a
                # double is seeded as eight bytes because that is how many the
                # image has there, not because the extractor happened to
                # capture the adjacent dword.  Reaches .rdata constants and
                # initialised .data; BSS falls through to the capture below.
                seeds[slot_addr] = xbe_bytes
            elif orig_addr in _KNOWN_GLOBAL_BYTES:
                # Static fallback: concatenate the adjacent dword when it was
                # also extracted, so double reads get both halves even without
                # a snapshot.  (Many epsilon doubles have an unreferenced high
                # dword that the extractor never captured — those still need a
                # snapshot; see memsave_snapshot.py.)
                val = _KNOWN_GLOBAL_BYTES[orig_addr]
                nxt = _KNOWN_GLOBAL_BYTES.get(orig_addr + 4)
                if nxt is not None and len(val) == 4:
                    val = val + nxt
                seeds[slot_addr] = val
    return seeds


def _seed_dllimport_indirection(orc_slots: dict, lft_slots: dict,
                                orc_addr_hints: dict = None) -> dict:
    """Make a dllimport (indirect) reference resolve to the same storage as the
    other side's direct reference to the same global.

    kb.json globals are declared ``HDATA`` = ``__declspec(dllimport)``, so the
    clang candidate reaches one via a pointer-to-pointer::

        mov eax,[__imp__event_manager_globals]   ; slot holds &global
        push eax                                  ; the global's address

    The delinked MSVC oracle has no import table and references the same
    storage directly as ``DAT_0046bd40``, which patch_dir32_relocs rewrites to
    a globals slot.  The candidate's ``__imp_`` slot, though, is only seeded
    when a snapshot or _KNOWN_GLOBAL_BYTES entry exists for the target -- with
    neither, it stays zero-filled and the extra deref yields a NULL pointer.
    The two sides then pass different pointers and write to different pages,
    so both the stub-arg compare and the memory-trace compare are meaningless
    rather than merely imprecise.

    So: point each ``__imp_X`` slot at the OTHER side's direct slot for X,
    falling back to its own side's direct slot.  Both sides then agree on the
    pointer and share one page of storage.

    When BOTH sides go through ``__imp_X`` nothing is emitted -- they already
    agree (both deref the same zero), and the existing snapshot /
    known-globals seeding in _build_globals_seeds still takes precedence,
    since callers apply that map after this one.
    """
    import re, struct as _struct
    name2addr = _load_symbol_addrs()

    def _direct_slots(smap, hints=None):
        """{real_address: slot} for non-dllimport symbols we can resolve.

        The delinked oracle names data by address (``DAT_0046bd40``), so match
        that spelling too -- resolving only friendly names would miss every
        oracle-side direct reference, which is exactly the side we need.
        XBE-derived hints outrank both, and cover Ghidra labels that match no
        kb.json name at all (see _xbe_dir32_symbol_addrs).
        """
        out = {}
        for sym, slot in smap.items():
            if sym.startswith("__imp_"):
                continue
            hinted = (hints or {}).get(sym)
            if hinted is not None:
                out.setdefault(hinted, slot)
                continue
            m = re.match(r'(?:DAT|PTR|PTR_FUN|PTR_DAT|FLOAT|s)_([0-9a-fA-F]{4,})$',
                         sym)
            if m:
                out.setdefault(int(m.group(1), 16), slot)
                continue
            bare = _normalize_global_symbol(sym)
            addr = (_GLOBAL_NAME_ALIASES.get(bare) if bare else None)
            if addr is None and bare:
                addr = name2addr.get(bare)
            if addr is not None:
                out.setdefault(addr, slot)
        return out

    # Hints are read out of the pristine XBE at the ORACLE's reloc sites, so
    # they only apply to the oracle map -- the candidate's code layout differs.
    orc_direct = _direct_slots(orc_slots, orc_addr_hints)
    lft_direct = _direct_slots(lft_slots)
    seeds = {}
    for smap, other_direct, own_direct in ((orc_slots, lft_direct, orc_direct),
                                           (lft_slots, orc_direct, lft_direct)):
        for sym, slot in smap.items():
            if not sym.startswith("__imp_"):
                continue
            bare = _normalize_global_symbol(sym)
            addr = _GLOBAL_NAME_ALIASES.get(bare) or name2addr.get(bare)
            if addr is None:
                continue
            target = other_direct.get(addr, own_direct.get(addr))
            if target is not None:
                seeds[slot] = _struct.pack("<I", target)
    return seeds


# ---------------------------------------------------------------------------
# Unicorn emulation
# ---------------------------------------------------------------------------

def _run_function(code: bytes, abi: dict, arg_values: list,
                  verbose: bool = False, map_globals: bool = False,
                  stub_manager=None, globals_seeds: dict = None,
                  section_code: bytes = None,
                  func_offset: int = 0,
                  lifted: bool = False,
                  collect_mem_trace: bool = False,
                  memory_overrides: dict = None,
                  max_insn: int = None,
                  stub_arg_tracer=None,
                  auto_map_unmapped: bool = False,
                  image=None,
                  entry_va: int = None,
                  native_callee_ranges=None,
                  intercept_vas: dict = None) -> "state.CPUState":
    """Run a function in a fresh Unicorn instance.

    Returns a CPUState with captured registers and scratch memory.
    If emulation fails, returns a CPUState with .error set.

    map_globals: if True, maps a zeroed globals region at memmap.GLOBALS_BASE
    auto_map_unmapped: if True, installs the auto-map hook without also
        mapping/seeding the globals region. A leaf that reads a global (or
        dereferences an int-typed parameter that is really a pointer) would
        otherwise die on its first access with UC_ERR_READ_UNMAPPED before
        executing anything, which is why 170 cached entries sit at 0.0%
        coverage. The hook is symmetric across oracle and candidate, so a
        genuine difference in what each side reads still surfaces.
    stub_manager: if set, installs a fetch-unmapped hook to intercept calls
    globals_seeds: dict of {address: bytes} to write into the globals region
                   after zero-initialization (seeds known global values).
    section_code: full .text section bytes (enables intra-object calls)
    func_offset: offset of the target function within section_code
    collect_mem_trace: if True, install write/read hooks for trace differential
    memory_overrides: dict of {address: bytes} written after all setup (snapshot replay)

    image: `(raw, sections)` from `xbe_image.load_xbe()`.  When set, the whole
        pristine XBE is mapped at its REAL virtual addresses and `code` is
        written at `entry_va` inside it, instead of mapping CODE_BASE.  That is
        the raw-XBE oracle: switch tables, .rdata constants, sibling function
        bodies and BSS-sized .data are all correct by construction, so there is
        nothing to relocate.  It changes four things in here, each a hazard in
        docs/raw-xbe-oracle-migration.md:
          * CODE_BASE is not mapped at all (H4 moved the FXSAVE stub to
            TRAMP_BASE for exactly this reason);
          * the two ret-stub pre-map passes must not write into the image (H3),
            or they would stamp `31 C0 C3` over real function bodies and
            .rdata;
          * escaped control flow no longer faults on an unmapped page -- it
            would execute 6.5 MB of real Halo code until MAX_INSN -- so
            `hook_code` gets an EIP-domain guard (H5);
          * `global_reads` is recorded against auto-mapped pages only, and
            image pages are pre-mapped, so the union is what gets recorded or
            concolic Phase 2 silently stops seeing any input (H6).
    entry_va: absolute VA to run from; required with `image`.
    native_callee_ranges: iterable of `(lo, hi)` VA ranges the oracle is
        DELIBERATELY allowed to execute natively, i.e. callees the candidate
        also runs for real.  Anything outside the target body, the stub
        sentinels and these ranges trips the H5 guard.
    intercept_vas: `{callee VA: sentinel}` (H9).  A 5-byte `E9 <rel32>` is
        written at each VA inside the image, so every call reaching that
        callee -- from the target body, from a sibling, direct or tail --
        lands on the same sentinel the candidate's call site was patched to.
        The patch targets the CALLEE, not the call site, because raw image
        bytes carry no relocation to rewrite: the call is a finished E8 with a
        correct displacement into real code.  Without it the oracle runs the
        engine natively while the candidate hits return-0 trampolines, which
        compares "lift" against "lift plus engine" (H9).
    """
    import unicorn
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
    from unicorn import (UC_HOOK_CODE, UC_HOOK_MEM_FETCH_UNMAPPED, UC_HOOK_MEM_INVALID,
                         UC_HOOK_INTR)
    from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_EBP, UC_X86_REG_EIP

    import sys
    sys.path.insert(0, str(_SCRIPT_DIR))
    from abi import setup_args, SCRATCH_BASE as SCBASE, SCRATCH_SIZE as SCSIZE
    import state as state_mod

    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    last_map_error = [""]
    last_unmapped_access = [""]
    stub_addrs = stub_manager.get_stub_addresses() if stub_manager else set()
    if stub_manager is not None:
        # Sequenced stub_returns replay from call #0 in every run so oracle
        # and candidate (and every seed) see the identical sequence.
        stub_manager.reset_stub_sequences()
    if verbose and stub_addrs:
        stub_pairs = []
        for addr in sorted(stub_addrs):
            name = ""
            if stub_manager is not None:
                name = stub_manager._stub_names.get(addr, "")
            stub_pairs.append(f"{addr:#x}:{name}")
        print("    [stub-map] " + ", ".join(stub_pairs))

    # Map memory regions
    _image_lo = _image_hi = None
    _image_pages = frozenset()
    if image is not None:
        import xbe_image
        _img_raw, _img_secs = image
        # ONE coalesced page-aligned map plus a write per section: XBE sections
        # are 0x20-aligned, not page-aligned, so 24 separate mem_map calls
        # overlap and fail.
        _image_lo, _image_hi = xbe_image.map_image(uc, _img_raw, _img_secs)
        _image_pages = frozenset(xbe_image.image_pages(_img_secs))
        # Image where the image knows, capture where it does not -- the same
        # layering `_seed_known_globals` applies to a page the harness maps
        # itself, applied here to the pre-mapped image.
        #
        # It has to happen HERE and not there, because H11 makes the callers of
        # `_seed_known_globals` skip image pages outright: the capture must
        # never land on top of real .rdata/.data.  But a BSS address has no
        # real bytes to protect, and `map_image` leaves it at load-time zero.
        # Without this, mapping the image into the CANDIDATE would take away
        # the capture it used to get from the auto-map path and replace it with
        # zeros -- a NULL-guard early-out on every seed, which is H10.
        _seed_capture_over_bss(uc, _img_raw, _img_secs)
    if entry_va is None:
        # The candidate always runs from CODE_BASE, image or no image: its code
        # is a clang .obj, not part of the XBE.  Mapping both is what makes the
        # DATA image shared -- see the `image` docstring.
        uc.mem_map(CODE_BASE, CODE_SIZE)
    uc.mem_map(STACK_BASE, STACK_SIZE)
    uc.mem_map(SCRATCH_BASE, SCRATCH_SIZE)

    if map_globals:
        from stubs import GLOBALS_BASE, GLOBALS_SIZE
        uc.mem_map(GLOBALS_BASE, GLOBALS_SIZE)
        uc.mem_write(GLOBALS_BASE, b'\x00' * GLOBALS_SIZE)
        # This call is a no-op now and kept only as a belt-and-braces guard.
        #
        # GLOBALS_BASE used to be 0x00500000, i.e. inside real Xbox address
        # space, so a candidate's raw-absolute read of a real global landing in
        # 0x500000-0x600000 (e.g. *(data_t**)0x5ab23c -- no relocation, so it
        # never becomes a slot below) hit this PRE-MAPPED region and therefore
        # never reached the UC_HOOK_MEM_READ_UNMAPPED auto-seed path that
        # handles every OTHER known global.  Seeding here was the patch for
        # that.  GLOBALS_BASE is now outside the XBE image entirely, so no
        # known global falls in this window and the read falls through to
        # hook_mem_unmapped, which maps its 64 KB page and seeds the same bytes
        # -- the general path, not a special case.  Reads there are now also
        # recorded in `global_reads`, which the pre-mapped form suppressed.
        _seed_known_globals(uc, GLOBALS_BASE, GLOBALS_SIZE)
        if globals_seeds:
            for addr, data in globals_seeds.items():
                try:
                    uc.mem_write(addr, data)
                except unicorn.UcError:
                    # Seed lands outside the pre-mapped globals region (e.g. a
                    # switch index-map seeded at its original low VA). Map the
                    # spanned pages first, then write.
                    start_page = addr & ~0xFFFF
                    end_page = (addr + len(data) + 0xFFFF) & ~0xFFFF
                    for page in range(start_page, end_page, 0x10000):
                        try:
                            uc.mem_map(page, 0x10000)
                            uc.mem_write(page, b'\x00' * 0x10000)
                        except unicorn.UcError:
                            pass
                    uc.mem_write(addr, data)

    # Apply memory overrides (state snapshot replay)
    _override_mapped = set()
    if memory_overrides:
        for addr, data in memory_overrides.items():
            # Map all 64KB pages spanned by this override entry
            start_page = addr & ~0xFFFF
            end_addr = addr + len(data)
            end_page = (end_addr + 0xFFFF) & ~0xFFFF
            for page in range(start_page, end_page, 0x10000):
                if page in _override_mapped:
                    continue
                if page in _image_pages:
                    # H11.  The page is already mapped with the XBE's real
                    # bytes.  Zero-filling it and stamping known_globals.json
                    # over it would replace ground truth with a cross-build
                    # live capture (see known_globals.json's own _warning).
                    # The snapshot write below still lands, which is the point.
                    _override_mapped.add(page)
                    continue
                try:
                    uc.mem_map(page, 0x10000)
                    uc.mem_write(page, b'\x00' * 0x10000)
                    _seed_known_globals(uc, page, 0x10000)
                except Exception:
                    try:
                        uc.mem_protect(page, 0x10000, unicorn.UC_PROT_ALL)
                    except Exception:
                        pass
                _override_mapped.add(page)
            uc.mem_write(addr, data)

    # Write function code at CODE_BASE -- or, for the raw-XBE oracle, over the
    # image at the function's real VA.  The bytes are normally identical to what
    # map_image already wrote; the write is what lets a caller hand in a PATCHED
    # oracle body (the symmetric-interception JMPs of hazard H9).
    _intercept_sites = {}
    if entry_va is not None:
        uc.mem_write(entry_va, code)
        entry_point = entry_va
    elif section_code is not None and len(section_code) <= CODE_SIZE:
        combined = bytearray(section_code)
        combined[func_offset:func_offset + len(code)] = code
        uc.mem_write(CODE_BASE, bytes(combined))
        entry_point = CODE_BASE + func_offset
    else:
        uc.mem_write(CODE_BASE, code)
        entry_point = CODE_BASE

    # H9 interception, in whichever instance mapped the image -- which since
    # step 6b is BOTH.  The candidate is not exempt: its identity-relocated
    # DIR32 sites read the image's real function pointers, so
    # `call dword ptr [main_lost_map]` reaches real engine code on the
    # candidate side too, and a JMP written only into the oracle's copy would
    # make the two sides call different things.  The sentinel behind a given
    # symbol is shared, so patching both keeps one intercept set for the
    # comparison rather than one per side.
    for _cva, _sent in (intercept_vas or {}).items():
        if entry_point <= _cva < entry_point + len(code):
            # The target calls itself, or its bound swallowed the callee.
            # Patching here would overwrite the code under test.
            continue
        if not (_image_lo <= _cva < _image_hi - 5):
            continue
        _rel = (_sent - (_cva + 5)) & 0xFFFFFFFF
        try:
            uc.mem_write(_cva, b"\xe9" + _rel.to_bytes(4, "little"))
        except unicorn.UcError:
            continue
        _intercept_sites[_cva] = _sent

    # Pre-map pages for indirect call targets: hardcoded absolute addresses
    # in the function code that match known stub targets (FUN_XXXXXXXX).
    # These addresses are reached via `call reg`, not REL32-relocated calls.
    # Pre-mapping them avoids run-time fetch-hook cascading on the lifted side.
    _known_targets = set()
    if stub_manager is not None:
        for _sentinel, _sym in stub_manager._stub_names.items():
            _m = re.match(r'FUN_([0-9a-fA-F]+)', _sym.lstrip('_'))
            if _m:
                _known_targets.add(int(_m.group(1), 16))
    _pre_mapped = set()
    for _i in range(len(code) - 3):
        _v = struct.unpack_from('<I', code, _i)[0]
        if _v in _known_targets:
            if _image_lo is not None and _image_lo <= _v < _image_hi:
                # H3: the target is already real, mapped code.  These passes
                # exist only to avoid a fetch-hook cascade on an UNMAPPED
                # target, which the image map already satisfies, and writing
                # a ret-stub here would overwrite a real function body.
                continue
            _page = _v & ~0xFFFF
            if _page not in _pre_mapped:
                try:
                    uc.mem_map(_page, 0x10000)
                    uc.mem_write(_page, b"\xCC" * 0x10000)
                    _pre_mapped.add(_page)
                except Exception:
                    try:
                        uc.mem_protect(_page, 0x10000, unicorn.UC_PROT_ALL)
                        _pre_mapped.add(_page)
                    except Exception:
                        pass
            uc.mem_write(_v, b"\x31\xC0\xC3")

    # Second pre-map pass: scan globals_seeds for 4-byte values that look
    # like XBE function pointers (0x000100–0x01FFFFFF).  These are callback
    # addresses loaded from snapshots (e.g. PTR_FUN_0032eaa0 contains the
    # pre-save callback at 0x001BF760).  Pre-mapping them prevents the
    # emulator from hitting the fetch_hook when memory-indirect calls like
    # `call dword ptr [globals_slot]` resolve to these addresses.
    _override_ranges = []
    if memory_overrides:
        _override_ranges = [(a, a + len(d)) for a, d in memory_overrides.items()]
    if globals_seeds:
        for _slot_addr, _seed_data in globals_seeds.items():
            for _j in range(0, len(_seed_data) - 3, 4):
                _ptr = struct.unpack_from('<I', _seed_data, _j)[0]
                if 0x000100 <= _ptr <= 0x01FFFFFF and _ptr not in _known_targets:
                    if CODE_BASE <= _ptr < CODE_BASE + CODE_SIZE:
                        continue
                    if _image_lo is not None and _image_lo <= _ptr < _image_hi:
                        # H3, and this pass is the dangerous one: its scan range
                        # 0x000100..0x01FFFFFF CONTAINS the whole image span, so
                        # without this it would stamp ret-stubs across real code
                        # and .rdata.
                        continue
                    # Skip pointers backed by snapshot memory or known-globals
                    # data: these are real data addresses (e.g. an __imp__ slot
                    # holding &actor_data), not code callbacks.  Writing a
                    # ret-stub here would clobber the global value that
                    # _build_globals_seeds / _seed_known_globals expects to
                    # supply through the resulting double-deref.
                    if any(lo <= _ptr < hi for lo, hi in _override_ranges):
                        continue
                    if _ptr in _KNOWN_GLOBAL_BYTES:
                        continue
                    # Skip the page containing FAKE_RET_ADDR stack sentinel
                    _ptr_page = _ptr & ~0xFFFF
                    if _ptr_page == (STACK_TOP & ~0xFFFF):
                        continue
                    if map_globals:
                        from stubs import GLOBALS_BASE, GLOBALS_SIZE
                        if GLOBALS_BASE <= _ptr < GLOBALS_BASE + GLOBALS_SIZE:
                            continue
                    _page = _ptr & ~0xFFFF
                    if _page not in _pre_mapped:
                        try:
                            uc.mem_map(_page, 0x10000)
                            uc.mem_write(_page, b"\xCC" * 0x10000)
                            _pre_mapped.add(_page)
                        except Exception:
                            try:
                                uc.mem_protect(_page, 0x10000, unicorn.UC_PROT_ALL)
                                _pre_mapped.add(_page)
                            except Exception:
                                pass  # region limit — fetch_hook fallback
                    try:
                        uc.mem_write(_ptr, b"\x31\xC0\xC3")
                    except Exception:
                        pass  # page not mapped — fetch_hook fallback

    if stub_addrs:
        stub_page_base = min(stub_addrs) & ~0xFFFF
        stub_page_end = (max(stub_addrs) & ~0xFFFF) + 0x20000  # 2-page safety margin
        uc.mem_map(stub_page_base, stub_page_end - stub_page_base)
        uc.mem_write(stub_page_base, b"\xCC" * (stub_page_end - stub_page_base))
        for stub_addr in stub_addrs:
            uc.mem_write(stub_addr, stub_manager.get_stub_code(stub_addr))

    # Set up stack: ESP points just below STACK_TOP
    esp = STACK_TOP - 4
    uc.reg_write(UC_X86_REG_ESP, esp)
    uc.reg_write(UC_X86_REG_EBP, esp)

    # Initialize FPU control word: mask all exceptions, double-extended precision,
    # round to nearest. Matches the Xbox OS default (0x027F).
    from unicorn.x86_const import UC_X86_REG_FPCW
    uc.reg_write(UC_X86_REG_FPCW, 0x027F)

    # Zero scratch buffer
    uc.mem_write(SCRATCH_BASE, b'\x00' * SCRATCH_SIZE)

    # Set up arguments and fill scratch buffer
    scratch_writes = {}
    setup_args(uc, abi, arg_values, scratch_writes, lifted=lifted)
    for offset, data in scratch_writes.items():
        uc.mem_write(SCRATCH_BASE + offset, data)

    # Push fake return address (FAKE_RET_ADDR acts as termination sentinel)
    esp_after_args = uc.reg_read(UC_X86_REG_ESP)
    esp_after_args -= 4
    uc.mem_write(esp_after_args, struct.pack('<I', FAKE_RET_ADDR))
    uc.reg_write(UC_X86_REG_ESP, esp_after_args)

    entry_esp = esp_after_args  # ESP at function entry (after pushing ret addr)

    # Track instruction count and code coverage (address -> instruction size)
    insn_count = [0]
    stub_trace_count = [0]
    visited_pcs = {}
    # Times we caught execution running inside an auto-mapped DATA page and
    # returned to the caller instead of letting it slide (see hook_code).
    # HALO_NO_DATA_EXEC_GUARD=1 disables the guard, so its effect can be
    # A/B-measured with a flag instead of a source swap (a source swap during a
    # long measurement has burned us before).
    data_exec_recoveries = [0]
    MAX_DATA_EXEC_RECOVERIES = 64
    # Set when a recovery path declines to continue because control flow is
    # provably gone.  Folded into err_msg so the seed is scored as an error
    # rather than as a clean stop with whatever state happened to be left.
    escape_reason = [None]
    _data_exec_guard = os.environ.get("HALO_NO_DATA_EXEC_GUARD") != "1"
    # H5: with the whole image mapped, escaped control flow does not fault --
    # it runs real Halo code until MAX_INSN and reports a timeout at some
    # address unrelated to where control was lost.  Constrain the EIP domain to
    # the target body, the stub sentinels, and callees the caller declared as
    # deliberately native.  Disarmed before the FXSAVE capture below, which
    # deliberately executes a stub at TRAMP_BASE.
    _eip_guard = [image is not None]
    _eip_lo, _eip_hi = entry_point, entry_point + len(code)
    _native_ranges = tuple(native_callee_ranges or ())
    # The ARENA, not the sentinel addresses.  A sentinel is only the FIRST
    # instruction of what runs there: a trampoline is several instructions
    # (`mov eax,imm32; ret`), and a real-code stub is a whole function body.
    # Allowing only the exact sentinel addresses made the guard fire on the
    # second byte of the very first trampoline the oracle jumped to
    # (eip=0x40000002), reporting an escape for the interception working.
    # Same expression as the arena mem_map above, so the two cannot drift.
    _stub_lo = _stub_hi = 0
    if stub_addrs:
        _stub_lo = min(stub_addrs) & ~0xFFFF
        _stub_hi = (max(stub_addrs) & ~0xFFFF) + 0x20000
    _ring = None
    if os.environ.get("BIPED_RING_TRACE") == "1":
        from collections import deque
        _ring = deque(maxlen=48)

    def hook_code(uc, address, size, user_data):
        insn_count[0] += 1
        if _ring is not None:
            from unicorn.x86_const import UC_X86_REG_ESP as _ESP_T
            _ring.append((address, uc.reg_read(_ESP_T)))
        if address not in visited_pcs:
            visited_pcs[address] = size
        if _eip_guard[0] and not (_eip_lo <= address < _eip_hi):
            if (not (_stub_lo <= address < _stub_hi)
                    and address not in _intercept_sites
                    and not any(lo <= address < hi
                                for lo, hi in _native_ranges)):
                escape_reason[0] = (
                    f"oracle_escaped eip={address:#x} -- outside the target "
                    f"body [{_eip_lo:#x},{_eip_hi:#x}), the stub arena "
                    f"[{_stub_lo:#x},{_stub_hi:#x}), "
                    f"the H9 interception JMPs and the declared "
                    f"native-callee ranges")
                uc.emu_stop()
                return
        # Executing inside a page we auto-mapped for DATA is never legitimate:
        # it means an indirect call went through synthetic state -- a garbage
        # function pointer that still passed a `!= NULL` check. Those pages are
        # zero-filled, and zeros decode as `add [eax], al`: a 2-byte
        # instruction that alters neither ESP nor control flow, so execution
        # SLID through the entire page and only stopped on walking off the end,
        # reporting UC_ERR_FETCH_UNMAPPED at an address unrelated to the actual
        # call. That is the single largest error class in the batch.
        #
        # The fill cannot just be made non-zero (the hook_mem_unmapped comment
        # proposes 0xCC): these are DATA pages, so the fill is read back as
        # pointers and counts, and 0xCC is swallowed by hook_interrupt anyway,
        # which only turns a 2-byte slide into a 1-byte one. Guard the
        # EXECUTION instead, exactly as hook_fetch_unmapped does for a target
        # that is still unmapped: EAX=0, pop the return address, continue.
        # Applied identically to oracle and candidate, so a wrong call target
        # surfaces in the differential rather than crashing both sides.
        if (_data_exec_guard and (address & ~0xFFFF) in _mapped_regions
                and address not in stub_addrs):
            if data_exec_recoveries[0] >= MAX_DATA_EXEC_RECOVERIES:
                uc.emu_stop()
                return
            data_exec_recoveries[0] += 1
            from unicorn.x86_const import (UC_X86_REG_EAX as _EAX_G,
                                           UC_X86_REG_ESP as _ESP_G,
                                           UC_X86_REG_EIP as _EIP_G)
            try:
                _esp = uc.reg_read(_ESP_G)
                _ret = struct.unpack('<I', bytes(uc.mem_read(_esp, 4)))[0]
            except Exception:
                uc.emu_stop()
                return
            # The dword at ESP is only a return address if a CALL put it there.
            # After a stray indirect call through garbage, it is just as likely
            # to be a saved register or the address of a local -- and jumping
            # to a STACK address means decoding that address's own bytes as
            # code.  Whether that faults or stalls harmlessly then depends on
            # the numeric value of the stack base, which is how
            # game_state_memory_pool_new scored PASS at 60% coverage with
            # STACK_BASE=0x00100000 and ERROR with STACK_BASE=0x08000000: the
            # oracle had already lost control flow in BOTH cases and the
            # verdict was decided by a coin flip in the recovery path.
            #
            # Refuse the jump instead.  The escape is then reported as an
            # escape, identically on both sides and at any stack base.
            if STACK_BASE <= _ret < STACK_TOP:
                escape_reason[0] = (f"data-exec recovery at pc={address:#x} "
                                    f"popped a stack address ({_ret:#x}) as a "
                                    f"return target -- control flow was "
                                    f"already lost")
                uc.emu_stop()
                return
            uc.reg_write(_EAX_G, 0)
            uc.reg_write(_ESP_G, _esp + 4)
            uc.reg_write(_EIP_G, _ret)
            return
        if verbose and address in stub_addrs and stub_trace_count[0] < 64:
            symbol_name = ""
            if stub_manager is not None:
                symbol_name = stub_manager._stub_names.get(address, "")
            print(f"    [stub] {symbol_name or hex(address)} @ {address:#x}")
            stub_trace_count[0] += 1
        if address in stub_addrs and stub_manager is not None and stub_manager.should_intercept(address):
            if stub_manager.execute_stub(uc, address):
                cur_esp = uc.reg_read(UC_X86_REG_ESP)
                ret_addr_bytes = uc.mem_read(cur_esp, 4)
                ret_addr = struct.unpack('<I', bytes(ret_addr_bytes))[0]
                uc.reg_write(UC_X86_REG_ESP, cur_esp + 4)
                uc.reg_write(UC_X86_REG_EIP, ret_addr)

    uc.hook_add(UC_HOOK_CODE, hook_code)

    from unicorn import UC_HOOK_MEM_READ_UNMAPPED, UC_HOOK_MEM_WRITE_UNMAPPED

    known_pages = {addr & ~0xFFFF for addr in _KNOWN_GLOBAL_BYTES}

    def hook_mem_invalid(uc, access, address, size, value, user_data):
        if address in stub_addrs:
            stub_manager.execute_stub(uc, address)
            cur_esp = uc.reg_read(UC_X86_REG_ESP)
            ret_addr_bytes = uc.mem_read(cur_esp, 4)
            ret_addr = struct.unpack('<I', bytes(ret_addr_bytes))[0]
            uc.reg_write(UC_X86_REG_ESP, cur_esp + 4)
            uc.reg_write(UC_X86_REG_EIP, ret_addr)
            return True
        last_unmapped_access[0] = (
            f"invalid addr={address:#x} size={size} access={access} value={value:#x}"
        )
        return False

    uc.hook_add(UC_HOOK_MEM_INVALID, hook_mem_invalid)

    # Memory trace and global reads collection
    mem_writes = {}  # (address, size) -> final value; coalesced, see MEM_TRACE_CAP
    mem_trace_truncated = [False]
    global_reads = {}
    _mapped_regions = set()

    # GATED heap-output witnessing (BIPED_HEAP_COMPARE=1): when a heap write
    # (>= HEAP_LINE) falls inside a snapshot-mapped region, INCLUDE it in the
    # write trace so output writes to the live biped object (~0x800bxxxx) are
    # actually compared.  Flag-off behaviour is byte-identical to before (the
    # blanket >= HEAP_LINE drop is preserved).  The FXSAVE instrumentation
    # page lives at FXSAVE_BASE (== HEAP_LINE), which is never inside a snapshot
    # region, so it is excluded for free by the membership test.
    _heap_compare = (os.environ.get("BIPED_HEAP_COMPARE") == "1"
                     and bool(_override_ranges))

    if collect_mem_trace:
        from unicorn import UC_HOOK_MEM_WRITE, UC_HOOK_MEM_READ

        def hook_mem_write(uc, access, address, size, value, user_data):
            if STACK_BASE <= address < STACK_TOP:
                return
            if CODE_BASE <= address < CODE_BASE + CODE_SIZE:
                return
            if address >= HEAP_LINE:  # FXSAVE region / arbitrary heap
                if not _heap_compare:
                    return
                # Only witness heap writes that land in a snapshot-mapped
                # region (shared by oracle and candidate -> same addresses).
                if not any(lo <= address < hi for lo, hi in _override_ranges):
                    return
            key = (address, size)
            if (key not in mem_writes and MEM_TRACE_CAP
                    and len(mem_writes) >= MEM_TRACE_CAP):
                mem_trace_truncated[0] = True
                return
            mem_writes[key] = value  # last write wins (matches _build_finals)

        def hook_mem_read(uc, access, address, size, value, user_data):
            # H6: only AUTO-mapped pages were recorded, which is the right
            # filter for synthetic globals -- but the raw-XBE oracle pre-maps
            # the image, so every real global read would go unrecorded and
            # concolic Phase 2 would silently have no inputs to work from.
            page = address & ~0xFFFF
            if page in _mapped_regions or page in _image_pages:
                try:
                    data = bytes(uc.mem_read(address, min(size, 4)))
                    global_reads[address] = (size, int.from_bytes(data, 'little'))
                except Exception:
                    pass

        uc.hook_add(UC_HOOK_MEM_WRITE, hook_mem_write)
        uc.hook_add(UC_HOOK_MEM_READ, hook_mem_read)

    # Non-leaf support: handle unmapped memory access
    if map_globals or auto_map_unmapped:

        def hook_mem_unmapped(uc, access, address, size, value, user_data):
            # Auto-map a 64KB page for any unmapped read/write.
            # Fill with ZEROS. An earlier version of this comment proposed
            # 0xCC (INT3) to stop accidental code execution on data pages, but
            # that is not viable: the fill is read back as data (pointers,
            # counts, sizes), so a non-zero fill changes program meaning, and
            # hook_interrupt swallows INT3 regardless. Accidental execution is
            # caught in hook_code via _mapped_regions instead.
            page_base = address & ~0xFFFF
            last_unmapped_access[0] = (
                f"page={page_base:#x} addr={address:#x} size={size} access={access}"
            )
            if page_base not in _mapped_regions:
                try:
                    uc.mem_map(page_base, 0x10000)
                    uc.mem_write(page_base, b'\x00' * 0x10000)
                    _seed_known_globals(uc, page_base, 0x10000)
                    _mapped_regions.add(page_base)
                    return True
                except Exception as exc:
                    last_map_error[0] = (
                        f"page={page_base:#x} addr={address:#x} size={size} access={access}: {exc}"
                    )
                    return False
            return False

        uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED, hook_mem_unmapped)
        uc.hook_add(UC_HOOK_MEM_WRITE_UNMAPPED, hook_mem_unmapped)
    else:
        def hook_known_globals(uc, access, address, size, value, user_data):
            from unicorn import UC_MEM_WRITE_UNMAPPED
            page_base = address & ~0xFFFF
            last_unmapped_access[0] = (
                f"known-page={page_base:#x} addr={address:#x} size={size} access={access}"
            )
            is_write = (access == UC_MEM_WRITE_UNMAPPED)
            if page_base not in known_pages and not is_write:
                return False
            try:
                uc.mem_map(page_base, 0x10000)
                uc.mem_write(page_base, b'\x00' * 0x10000)
                _seed_known_globals(uc, page_base, 0x10000)
                return True
            except Exception as exc:
                last_map_error[0] = (
                    f"known-page={page_base:#x} addr={address:#x} size={size} access={access}: {exc}"
                )
                return False

        uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED, hook_known_globals)
        uc.hook_add(UC_HOOK_MEM_WRITE_UNMAPPED, hook_known_globals)

    # Stub interception: handle fetch from sentinel addresses
    if stub_manager:
        _dynamic_stub_pages = set()

        def hook_fetch_unmapped(uc, access, address, size, value, user_data):
            if address in stub_addrs:
                stub_manager.execute_stub(uc, address)
                cur_esp = uc.reg_read(UC_X86_REG_ESP)
                ret_addr_bytes = uc.mem_read(cur_esp, 4)
                ret_addr = struct.unpack('<I', bytes(ret_addr_bytes))[0]
                uc.reg_write(UC_X86_REG_ESP, cur_esp + 4)
                uc.reg_write(UC_X86_REG_EIP, ret_addr)
                return True
            if address == FAKE_RET_ADDR:
                return False
            # Unknown code fetch (indirect call, vtable, intra-obj call target
            # outside extracted range). Treat as a zero-return function:
            # set EAX=0, pop return address, set EIP directly — same pattern
            # as sentinel stubs. Avoids cascading through wild RET target.
            if address != 0:
                # Best-effort page setup (safety net if re-entered without CALL)
                page = address & ~0xFFFF
                if page not in _dynamic_stub_pages:
                    try:
                        uc.mem_map(page, 0x10000)
                        uc.mem_write(page, b"\xCC" * 0x10000)
                        _dynamic_stub_pages.add(page)
                    except Exception:
                        try:
                            import unicorn as _uc
                            uc.mem_protect(page, 0x10000, _uc.UC_PROT_ALL)
                            _dynamic_stub_pages.add(page)
                        except Exception:
                            pass
                uc.mem_write(address, b"\x31\xC0\xC3")
                # Directly handle the call/return
                uc.reg_write(UC_X86_REG_EAX, 0)
                cur_esp = uc.reg_read(UC_X86_REG_ESP)
                ret_addr_bytes = uc.mem_read(cur_esp, 4)
                ret_addr = struct.unpack('<I', bytes(ret_addr_bytes))[0]
                uc.reg_write(UC_X86_REG_ESP, cur_esp + 4)
                uc.reg_write(UC_X86_REG_EIP, ret_addr)
                insn_count[0] += 2
                if address not in visited_pcs:
                    visited_pcs[address] = 2
                return True
            return False

        uc.hook_add(UC_HOOK_MEM_FETCH_UNMAPPED, hook_fetch_unmapped)

    # Handle INT3 (0xCC) from assert_halt in MSVC retail builds.
    # The oracle code may compile assert macros as INT3 breakpoints.
    # Skip the INT3 and continue execution (treat as no-op).
    def hook_interrupt(uc, intno, user_data):
        if intno == 3:  # INT3 breakpoint
            return
        if intno == 0:  # divide by zero — stop cleanly
            uc.emu_stop()
            return

    uc.hook_add(UC_HOOK_INTR, hook_interrupt)

    # Attach stub-arg tracer for this run (detached in the finally block below)
    if stub_manager is not None and stub_arg_tracer is not None:
        # Record this side's target extent so the comparator can tell the
        # target's own calls from calls made by natively-executed callees.
        # Each side has its own layout, hence per-tracer rather than global.
        stub_arg_tracer.target_range = (entry_point, entry_point + len(code))
        stub_manager.set_tracer(stub_arg_tracer)

    err_msg = None
    try:
        uc.emu_start(entry_point, entry_point + len(code), timeout=TIMEOUT_MS * 1000,
                     count=max_insn if max_insn is not None else MAX_INSN)
    except unicorn.UcError as e:
        err_str = str(e)
        cur_eip = uc.reg_read(UC_X86_REG_EIP)
        # UC_ERR_FETCH_UNMAPPED at FAKE_RET_ADDR means clean return
        if "fetch" in err_str.lower() or "Fetch" in err_str:
            if cur_eip != FAKE_RET_ADDR:
                err_msg = f"{err_str} [eip={cur_eip:#x}]"
        else:
            if last_map_error[0]:
                err_msg = f"{err_str} [{last_map_error[0]}]"
            elif last_unmapped_access[0]:
                err_msg = f"{err_str} [{last_unmapped_access[0]}]"
            else:
                err_msg = err_str

    # A declined recovery stops emulation WITHOUT raising, so it would
    # otherwise read as a clean return.  Report it.
    if err_msg is None and escape_reason[0]:
        err_msg = escape_reason[0]

    # Detach stub-arg tracer now that emulation has finished for this run
    if stub_manager is not None and stub_arg_tracer is not None:
        stub_manager.set_tracer(None)

    s = state_mod.capture(uc, SCRATCH_BASE, SCRATCH_SIZE, entry_esp + 4)

    # Capture full 80-bit FPU state via FXSAVE (reg_read only returns mantissa).
    #
    # The stub used to be written at CODE_BASE + CODE_SIZE - 16, which assumes
    # this run mapped CODE_BASE at all.  The raw-XBE oracle does not -- it runs
    # from the image at real VAs -- so the write would raise into the bare
    # `except: pass` below and silently leave `s.st` holding mantissa-only
    # values, making every float-returning function's ST0 comparison vacuously
    # equal.  TRAMP_BASE is a page neither side owns, so the capture no longer
    # depends on which oracle is in use, and a failure is now recorded.
    _eip_guard[0] = False   # the capture below deliberately runs a stub
    fxsave_stub_addr = TRAMP_BASE
    fxsave_stub = b"\x0F\xAE\x05" + struct.pack('<I', FXSAVE_BASE) + b"\xC3"
    try:
        uc.mem_map(TRAMP_BASE, TRAMP_SIZE)
        uc.mem_map(FXSAVE_BASE, FXSAVE_SIZE)
        uc.mem_write(fxsave_stub_addr, fxsave_stub)
        fake_ret2 = fxsave_stub_addr + len(fxsave_stub) - 1
        cur_esp = uc.reg_read(UC_X86_REG_ESP)
        cur_esp -= 4
        uc.mem_write(cur_esp, struct.pack('<I', fake_ret2))
        uc.reg_write(UC_X86_REG_ESP, cur_esp)
        uc.emu_start(fxsave_stub_addr, fake_ret2, timeout=1000000, count=10)
        fxsave_data = bytes(uc.mem_read(FXSAVE_BASE, 512))
        uc.reg_write(UC_X86_REG_ESP, cur_esp + 4)
        for i in range(8):
            off = 32 + i * 16
            s.st[i] = fxsave_data[off:off + 10]
    except Exception as _fx_exc:
        s.fxsave_error = f"{type(_fx_exc).__name__}: {_fx_exc}"
    if err_msg and _ring is not None:
        print("    [ring] last insns (pc, esp):")
        for _pc, _sp in _ring:
            print(f"      0x{_pc:08x}  esp=0x{_sp:08x}")
    s.error = err_msg
    s.insn_count = insn_count[0]
    s.visited_pcs = visited_pcs
    # Materialize the coalesced map back into the list[MemoryWrite] interface
    # that compare_mem_traces() and the debug fallbacks expect.
    s.mem_writes = [state_mod.MemoryWrite(address=a, size=sz, value=v)
                    for (a, sz), v in mem_writes.items()]
    if mem_trace_truncated[0]:
        print(f"    [mem-trace] WARNING: distinct-address cap "
              f"({MEM_TRACE_CAP}) hit; trace truncated — writes to addresses "
              f"beyond the cap are not compared")
    s.global_reads = global_reads
    s.auto_mapped_pages = set(_mapped_regions)
    return s


# ---------------------------------------------------------------------------
# Raw-XBE oracle: slice extraction, bound sanity, relocation-free classification
# ---------------------------------------------------------------------------
#
# Under `--oracle=xbe` the oracle is "this VA range of the one true XBE" rather
# than a delinked COFF.  That removes relocation synthesis, but it also removes
# the three things the lane derived FROM relocations, so each needs an explicit
# replacement here:
#
#   * `_check_relocations` / `classify_relocations` answered "is this a leaf?"
#     from the reloc list.  A raw slice has `relocs == []` by construction, and
#     an empty list would silently read as "self-contained" -- flipping the leaf
#     gate, suppressing oracle stub sentinels, and letting the Z3 proof run over
#     code containing CALL.  `_classify_raw_oracle` recovers the same three
#     counts by disassembling the bytes.
#   * `slice_looks_truncated` answered "does the reference actually cover the
#     function?" from `reached_section_end`, which a raw slice never sets.
#     `_oracle_bound_unreliable` answers it from the committed bounds table.
#
# Nothing in this section is reachable until the `--oracle` flag lands; it is
# exercised directly by tools/equivalence/test_raw_oracle_classify.py.

def _xbe_oracle_slice(addr: int, name: str):
    """(FunctionSlice, None) for the pristine-XBE body at `addr`, or (None, reason).

    The span comes from the committed `tools/verify/function_bounds.json` -- the
    same authority the VC71 lane has used since 2026-08-09 -- so the oracle needs
    no bounds heuristic of its own.  `relocs` is empty and `real_va` is set;
    every consumer keys off `real_va` to know that the empty reloc list carries
    no information (see `_check_relocations`).
    """
    sys.path.insert(0, str(_REPO_ROOT / "tools" / "verify"))
    import xbe_reference
    code, err = xbe_reference.function_bytes(addr)
    if code is None:
        return None, err
    ext = xbe_reference.function_extent(addr)
    kind, prov = (ext[1], ext[2]) if ext is not None else (None, None)
    from coff_loader import FunctionSlice
    return FunctionSlice(
        name=name,
        raw_name=name,
        code=code,
        relocs=[],
        defined_symbols=set(),
        section_offset=0,
        real_va=addr,
        bound_kind=kind,
        bound_provenance=prov,
    ), None


def _xbe_function_extent(addr: int):
    """COMMITTED `(lo, hi)` VA range for a function, or None.

    `function_bounds.json` is the authority and the only accepted source.
    `xbe_reference.function_extent` will happily COMPUTE a bound at run time
    for an address the table does not list -- that is right for a scoring
    reference, where a computed bound is better than none, and wrong here: this
    range widens the H5 escape guard's allowed set, so a guess turns a missed
    escape into a silent 48k-instruction run through unrelated engine code
    instead of a reported reason.  So `provenance` must say "table"; anything
    else refuses.  Same rule `_oracle_bound_unreliable` applies to the target
    itself.
    """
    sys.path.insert(0, str(_REPO_ROOT / "tools" / "verify"))
    try:
        import xbe_reference
        ext = xbe_reference.function_extent(addr)
    except Exception:
        return None
    if ext is None:
        return None
    end, _kind, prov = ext[0], ext[1], ext[2]
    if prov != "table":
        return None
    return (addr, end) if end > addr else None


def _oracle_bound_unreliable(addr: int) -> Optional[str]:
    """Reason the XBE bound for `addr` cannot found a verdict, else None.

    The delinked lane's guard was `slice_looks_truncated`: a reference that did
    not reach the target produced a one-byte slice, which emulated to 100%
    coverage against nothing.  A raw slice cannot be short for that reason, but
    it can be WRONG for four others, and each is missing evidence rather than a
    behavioural result:

      * no bound at all -- `unmapped`, or `no_terminator` recorded with
        end == start, both of which `function_extent` reports as None;
      * `kind == "table_data"` -- the range is data, not a function body;
      * `kind == "no_terminator"` -- the scan never found a RET/JMP, so the end
        is wherever the next kb.json symbol happens to start;
      * `provenance == "computed"` -- the address is absent from the committed
        table and the bound was recomputed at run time, so it is a per-run
        heuristic rather than something reviewable in a diff.

    Finally the bytes themselves must disassemble cleanly to a terminating
    instruction, which is the one check carried over verbatim from
    `slice_looks_truncated`.
    """
    sys.path.insert(0, str(_REPO_ROOT / "tools" / "verify"))
    import xbe_reference
    ext = xbe_reference.function_extent(addr)
    if ext is None:
        return ("no usable bound for %#x in function_bounds.json (absent, "
                "unmapped, or recorded with end == start)" % addr)
    end, kind, prov = ext
    if kind == "table_data":
        return "function_bounds.json marks %#x as table_data, not code" % addr
    if kind == "no_terminator":
        return ("function_bounds.json marks %#x as no_terminator, so its end "
                "(%#x) is the next symbol rather than a real tail" % (addr, end))
    if prov != "table":
        return ("%#x is absent from function_bounds.json; its bound was "
                "computed at run time (regenerate with "
                "tools/verify/function_bounds.py)" % addr)
    code, err = xbe_reference.function_bytes(addr)
    if code is None:
        return err or "no bytes for %#x" % addr
    from coff_loader import _TERMINATOR_MNEMONICS
    body = code
    while body and body[-1] in (0x90, 0xCC):
        body = body[:-1]
    if not body:
        return "slice at %#x is empty after stripping padding" % addr
    try:
        import capstone
    except ImportError:
        return None
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    last, pos = None, 0
    for insn in md.disasm(body, addr):
        last = insn
        pos = insn.address + insn.size - addr
    if last is None:
        return "slice at %#x does not disassemble" % addr
    if pos != len(body):
        return ("disassembly of %#x stops %d byte(s) before the end of the "
                "bounded range" % (addr, len(body) - pos))
    if last.mnemonic not in _TERMINATOR_MNEMONICS:
        return ("bounded range at %#x ends on '%s', not a terminating "
                "instruction" % (addr, last.mnemonic))
    return None


_PTR_TABLE_LIMIT = 256


def _pointer_table_callees(slot_va: int, limit: int = _PTR_TABLE_LIMIT):
    """Consecutive dwords at `slot_va` that are exact kb.json function entries.

    A direct `E8 rel32` names its callee in the instruction, so the intercept
    set (H9) falls out of the disassembly.  An INDIRECT call names only a
    pointer, and until the pristine image was mapped that pointer held nothing:
    the delinked oracle's unrelocated word read as zero, the data-exec guard
    "recovered" with EAX=0, and the call simply never happened.  With the image
    mapped the pointer is real, the oracle makes the call for real -- and while
    the candidate still stubs its equivalent callee, that is "lift vs
    lift+entire engine" again, which is what H9 exists to prevent.

    Two shapes turn up in `game_state.obj` alone and both are resolved here:

      * a single slot -- `game_state_save` opens with
        `call dword ptr [0x32eaa0]`, whose slot holds 0x1bf760, a sibling in
        the same object.
      * a TABLE walked through a register --
        `game_state_call_after_load_procs` is
        `mov esi, 0x32eaa8; mov edi, 0xd; call dword ptr [esi]; add esi, 4;
        dec edi; jne` over 13 function pointers.  The call site names no
        address at all, so the table has to be read from the pointer the
        function loads.

    Hence "a run of dwords", not "one dword": the run is what makes the second
    shape work without inferring the loop's trip count from `edi`.  Reading
    further than the loop actually walks is harmless -- an intercepting JMP
    only matters if control reaches it -- whereas reading too few leaves a live
    callee escaping into real engine code.

    The two dword tests are deliberately NOT the same test.

    The FIRST dword must land exactly on a kb.json function entry.  That is the
    expensive claim and it is what keeps this cheap and safe on the common
    case: a plain `push <format string>` probes one dword, fails, and stops, so
    no `.rdata` byte soup is ever mistaken for a table.

    Later dwords only have to point into `.text`.  Requiring a function entry
    there truncates real tables: of the 13 pointers at 0x32eaa8, five
    (0xb9880, 0x17ca90, 0x1939e0, 0x1bfbd0, 0x877e0) are in neither kb.json
    nor `function_bounds.json` -- they are real engine functions this project
    has simply never listed -- and stopping at the first of them left nine
    callees escaping.  Inside a table whose head is a confirmed function
    pointer, a `.text` address in the next slot IS evidence of a call target;
    demanding a second, independent attestation of something we can already
    see is what produced the wrong answer.  The run still stops at the first
    dword that is not `.text` at all, which is how the 13-entry table ends
    (slot 13 holds 0).

    Over-reading is the safe direction and under-reading is not: an
    intercepting JMP only matters if control reaches it, so a spurious entry
    costs an unused sentinel, while a missing entry is a live escape into real
    engine code and a lost verdict.
    """
    img = _xbe_globals_image()
    if img is None:
        return []
    import xbe_image
    raw, secs = img
    funcs = _kb_func_addr_set()
    out = []
    for k in range(limit):
        word = xbe_image.read_va_raw(raw, secs, slot_va + 4 * k, 4)
        if not word or len(word) < 4:
            break                    # BSS or past raw_size: no pointer there
        target = int.from_bytes(word, "little")
        if k == 0:
            if target not in funcs:
                break
        elif _xbe_section_name_at(target) != ".text":
            break
        out.append(target)
    return out


_ESCAPE_EIP_RE = _re_mod.compile(r"oracle_escaped eip=(0x[0-9a-fA-F]+)")


def _symmetric_escape(orc_err, lft_err):
    """The EIP both sides escaped to, when they escaped to the SAME one.

    An escape is the absence of evidence: control left the function body, so
    nothing about the rest of the run says anything about the lift.  When only
    one side escapes that absence is itself a finding -- the two sides took
    different paths -- and it is scored as an error.  When BOTH escape to the
    same address, there is no finding at all: the seed drove identical control
    flow off the end of both bodies, which is a statement about the SEED, not
    about the candidate.

    This is not hypothetical bookkeeping.  Under the raw-XBE oracle the
    concolic phase reaches inputs the delinked lane never could, and
    `actor_action_replace_prop` takes 25 of its 45 seeds to
    `oracle_escaped eip=0x1` on both sides at once: the solver picked a value
    for a field the function then calls through.  Counting those as errors
    turned a 20/20 pass into a red gate while every seed that carried
    information agreed.

    Requiring the same EIP is what keeps this from excusing real divergence:
    two sides escaping to DIFFERENT addresses have taken different paths and
    are still errors.
    """
    if not orc_err or not lft_err:
        return None
    mo = _ESCAPE_EIP_RE.search(str(orc_err))
    ml = _ESCAPE_EIP_RE.search(str(lft_err))
    if not mo or not ml:
        return None
    if int(mo.group(1), 16) != int(ml.group(1), 16):
        return None
    return int(mo.group(1), 16)


def _classify_raw_oracle(code: bytes, va: int):
    """`stubs.RelocClassification` for raw XBE bytes, which carry no relocations.

    Recovers by disassembly what `classify_relocations` reads off a delinked
    .obj's relocation table:

      call_count   direct CALL/JMP sites whose target leaves [va, va+len(code)).
                   A branch back INSIDE that range is a local label and counts
                   as nothing.
      dir32_count  absolute-address SITES inside the XBE image span: a memory
                   operand's displacement with no base register, or an
                   immediate.  Counted per site, not per distinct address,
                   because that is what `classify_relocations` does -- each
                   relocation is one site.

    Two deliberate differences from the delinked classifier, both because a raw
    image has no notion of an "object":

      * `intra_obj_calls` is always 0.  There is no exported range for a call
        to be internal to.
      * `call_count` is therefore >= the delinked `call_count` for the same
        function.  A delinked .obj that happens to CONTAIN the callee resolved
        that call itself, with a correct in-object displacement and no
        relocation at all (which is why `_has_raw_calls` /
        `_redirect_raw_calls` exist to find them), so `classify_relocations`
        structurally cannot see it.  Every such call still leaves the function
        body and still has to be intercepted symmetrically with the candidate
        (hazard H9), so counting it is the point, not an error.
        `test_raw_oracle_classify.py` pins exactly this relationship.

    Why the operand guards matter: an absolute displacement is only
    recognisable as such when the operand has no base register
    (`mov eax, [0x4ea9ac]`, or the indexed-table form
    `jmp [eax*4 + 0x1e5000]`); a displacement off a base register is a struct
    field, not a global.  `.rdata` addresses are counted like any other -- a
    delinked object names them `s_...` / `DAT_...` externals, and only a
    relocation against its OWN `.rdata` section carries the `.rdata` prefix
    that `classify_relocations` skips.

    A bare IMMEDIATE is the one genuinely ambiguous form, since a plain size or
    count can land in the image span by arithmetic accident: `cmp eax, 0x40000`
    (256 KB) and `push 0x40000` both fall inside `.text`, which starts at
    0x12000, and both were counted as data references until this rule was
    added.  So an immediate pointing into `.text` is only accepted when it is
    an exact kb.json function entry -- i.e. when it really is someone taking
    the address of a function.  Immediates into `.rdata`/`.data` keep no such
    requirement: there is no plausible reading of `push 0x2b998c` other than a
    pointer to the format string that lives there.
    """
    from stubs import RelocClassification
    lo, hi = _image_span_cached()
    end = va + len(code)

    rel32_ext = 0
    dir32_ext = 0
    external = []
    # Callees reached through a pointer rather than named by an instruction.
    # Held apart from `external` and folded in once at the end: a table is
    # read per SITE, so the same callee can be found several times, and the
    # stub budget counts callees, not sightings.
    indirect = set()

    try:
        import capstone
        from capstone import x86 as cs_x86
    except ImportError:
        return RelocClassification("non_leaf", 0, 0, 0, ["<no capstone>"],
                                   "capstone unavailable; cannot classify raw "
                                   "bytes without a disassembler")

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    decoded = 0
    for insn in md.disasm(code, va):
        decoded = insn.address + insn.size - va
        is_branch = insn.mnemonic == "call" or insn.mnemonic.startswith("j")
        for op in insn.operands:
            if op.type == cs_x86.X86_OP_IMM:
                target = op.imm & 0xFFFFFFFF
                if is_branch:
                    if va <= target < end:
                        continue            # local label or self-recursion
                    rel32_ext += 1
                    external.append("FUN_%08x" % target)
                elif lo <= target < hi:
                    if (_xbe_section_name_at(target) == ".text"
                            and target not in _kb_func_addr_set()):
                        continue        # a size/count, not an address
                    dir32_ext += 1
                    external.append("DAT_%08x" % target)
                    indirect.update(_pointer_table_callees(target))
            elif op.type == cs_x86.X86_OP_MEM:
                if op.mem.base != 0:
                    continue
                disp = op.mem.disp & 0xFFFFFFFF
                if lo <= disp < hi and not (va <= disp < end):
                    dir32_ext += 1
                    external.append("DAT_%08x" % disp)
                    indirect.update(_pointer_table_callees(disp))

    for _target in sorted(indirect):
        if va <= _target < end:
            continue                # the function's own entry, in its own table
        rel32_ext += 1
        external.append("FUN_%08x" % _target)

    if decoded != len(code):
        # A tail that does not decode means the bound is wrong or the range is
        # not all code; `_oracle_bound_unreliable` is the gate that rejects it,
        # but say so here too rather than silently classifying a partial body.
        external.append("<undecoded tail at +%#x>" % decoded)

    if dir32_ext == 0 and rel32_ext == 0:
        # `external` is normally empty here; it can still hold the undecoded-tail
        # marker above, and dropping that would hide a wrong bound behind a
        # confident "leaf".
        return RelocClassification("leaf", 0, 0, 0, external,
                                   "no calls or absolute data references in "
                                   "the raw XBE bytes")
    if rel32_ext == 0:
        return RelocClassification("data_only", dir32_ext, 0, 0, external,
                                   f"{dir32_ext} global data reference(s)")
    return RelocClassification("stubbable", dir32_ext, rel32_ext, 0, external,
                               f"{rel32_ext} external call(s), {dir32_ext} data ref(s)")


def _z3_gate_reason(oracle_slice, lifted_slice, oracle_raw_class) -> Optional[str]:
    """Reason a Z3 equivalence proof must not be attempted, else None.

    `is_leaf` was the whole gate while both sides came from a COFF, because a
    relocation table that resolves internally really does mean "no external
    references".  A raw-XBE oracle has no relocation table (hazard H1), so two
    extra conditions have to be checked explicitly, and only for that oracle --
    a delinked run reaches this function with `oracle_raw_class` None and is
    told to proceed, so delinked verdicts are unchanged.

      * The raw classifier must itself say "leaf".  `z3_equiv/x86_to_z3` models
        a single straight-line body with a fresh symbolic memory; a CALL in the
        oracle bytes has no meaning there, and the proof would silently be over
        different code than the candidate's.
      * The CANDIDATE must have zero DIR32 relocations.  A candidate DIR32 is a
        global the oracle reaches through a real absolute address in the shared
        image while the candidate reaches it through a globals slot, so the two
        sides' symbolic memories are not the same memory and "proven equal" is
        a statement about neither.
    """
    if oracle_raw_class is None:
        return None
    if oracle_raw_class.category != "leaf":
        return (f"raw oracle is {oracle_raw_class.category}, not leaf "
                f"({oracle_raw_class.reason})")
    from stubs import classify_relocations
    lft = classify_relocations(lifted_slice.relocs,
                               getattr(lifted_slice, "defined_symbols", set()))
    if lft.dir32_count:
        return (f"candidate has {lft.dir32_count} DIR32 data relocation(s); "
                f"the oracle reads those globals from the image and the "
                f"candidate from slots, so the two symbolic memories differ")
    return None


_IMAGE_SPAN_CACHE = None
_XBE_SECTION_TABLE = None
_KB_FUNC_ADDRS = None


def _image_span_cached() -> tuple[int, int]:
    global _IMAGE_SPAN_CACHE
    if _IMAGE_SPAN_CACHE is None:
        import memmap
        _IMAGE_SPAN_CACHE = memmap.image_span()
    return _IMAGE_SPAN_CACHE


def _xbe_section_name_at(va: int) -> Optional[str]:
    """Name of the pristine-XBE section containing `va`, or None."""
    global _XBE_SECTION_TABLE
    import xbe_image
    if _XBE_SECTION_TABLE is None:
        _XBE_SECTION_TABLE = xbe_image.load_xbe()[1]
    sec = xbe_image.section_at(_XBE_SECTION_TABLE, va)
    return sec.name if sec is not None else None


def _kb_func_addr_set() -> frozenset:
    """Every kb.json function entry address, as ints.  Same set
    `stubs.StubManager._kb_func_addrs` builds; kept separate so the raw
    classifier does not need a StubManager instance."""
    global _KB_FUNC_ADDRS
    if _KB_FUNC_ADDRS is None:
        addrs = set()
        try:
            kb = _load_kb()
        except Exception:
            kb = {}
        for obj in kb.get("objects", []):
            for fn in obj.get("functions", []):
                a = fn.get("addr", "")
                if not a:
                    continue
                try:
                    addrs.add(int(a, 16))
                except ValueError:
                    pass
        _KB_FUNC_ADDRS = frozenset(addrs)
    return _KB_FUNC_ADDRS


# ---------------------------------------------------------------------------
# Relocation checker
# ---------------------------------------------------------------------------

def _check_relocations(func_slice, label: str, quiet: bool = False,
                      expect_relocs: bool = True) -> bool:
    """Return True if the function has no unresolvable external relocations.

    Relocations that reference symbols defined in the same .obj are safe
    (intra-object calls/data).  Only truly external symbols (not in
    defined_symbols and not section-relative) are rejected.

    `expect_relocs` says whether this slice's relocation table is AUTHORITATIVE.
    It is for anything parsed from a COFF: an empty table there really does mean
    the function references nothing outside its own bytes.  It is not for a
    slice read straight out of the XBE image, where `relocs` is empty by
    construction (hazard H1) -- and answering "True, it is a leaf" from that
    would flip the leaf gate, suppress the oracle's stub sentinels, and let the
    Z3 proof run over code containing CALL.  Callers with a raw slice must use
    `_classify_raw_oracle` instead; passing `expect_relocs=False` here returns
    False, which is the conservative direction (treat as non-leaf).
    """
    if not func_slice.relocs:
        if not expect_relocs:
            if not quiet:
                print(f"  [RELOC] {label}: slice carries no relocation table "
                      f"(raw XBE bytes); leaf status must come from "
                      f"_classify_raw_oracle, not from an empty reloc list")
            return False
        return True

    defined = getattr(func_slice, 'defined_symbols', set())
    ok = True
    for r in func_slice.relocs:
        sym = r.symbol_name
        if sym.startswith(".text") or sym.startswith(".rdata"):
            continue
        if sym in defined:
            continue
        if not quiet:
            print(f"  [RELOC] {label}: '{sym}' at +0x{r.virtual_address:x} — external, cannot emulate")
        ok = False
    return ok


# ---------------------------------------------------------------------------
# Pure-leaf cache (consumed by tools/llm_auto_lift.py for selection scoring)
# ---------------------------------------------------------------------------

_LEAF_CACHE_PATH = _REPO_ROOT / "tools" / "equivalence" / "leaf_cache.json"


def _record_leaf_classification(addr: str, is_leaf: bool) -> None:
    """Persist a classification entry to leaf_cache.json.

    Uses the extended schema: each entry is a dict with at least a "class" key.
    Legacy string entries are upgraded on read.
    """
    if not addr:
        return
    norm = addr if addr.startswith("0x") else hex(int(addr, 0))
    norm = norm.lower()
    try:
        if _LEAF_CACHE_PATH.exists():
            data = json.loads(_LEAF_CACHE_PATH.read_text(encoding="utf-8"))
        else:
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    cat = "leaf" if is_leaf else "non_leaf"
    existing = data.get(norm)
    if isinstance(existing, dict):
        entry = dict(existing)
        entry["class"] = cat
    else:
        entry = {"class": cat}
    # Skip the write when nothing changed: the equivalence cron runs in the
    # MAIN worktree, and an unconditional rewrite dirties it (parking lands).
    if data.get(norm) == entry:
        return
    data[norm] = entry
    try:
        _LEAF_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LEAF_CACHE_PATH.write_text(
            json.dumps(dict(sorted(data.items())), indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


_CONFIDENCE_RANK = {"none": -1, "weak": 0, "moderate": 1, "high": 2}

# Below this much coverage, a run has not tested the function in any meaningful
# sense and must not be recorded with a confidence tier at all. 129 cached
# entries were carrying a tier at literally 0.0% coverage -- a verdict with no
# evidence behind it reads downstream exactly like a verdict with evidence.
COVERAGE_FLOOR_PCT = 10.0

# Minimum passing seeds before "every seed observed the same behaviour" counts
# as evidence of a vacuous run rather than a deliberately pinned input.
VACUOUS_OUTPUT_MIN_SEEDS = 5


def _classify_confidence(coverage_pct: float, output_varied: bool,
                         passed: int) -> str:
    """Map a run's coverage and observable-output diversity to a tier.

    `output_varied` means the oracle produced more than one distinct
    observable result across seeds -- counting return value, scratch-buffer
    payload, AND memory writes. Judging diversity on the return value alone
    (the previous `monotonic_return -> weak` rule) mislabels every function
    whose real output is a buffer it was handed: vector3d_scale_add covers
    100% of its body and writes different floats every seed, yet returned the
    same out-pointer each time and was therefore recorded "weak".
    """
    if passed <= 0 or coverage_pct < COVERAGE_FLOOR_PCT:
        return "none"
    if coverage_pct >= 60:
        return "high" if output_varied else "moderate"
    if coverage_pct >= 30:
        return "moderate"
    return "weak"


def _record_confidence(addr, confidence: str, coverage_pct: float,
                       oracle: str = "delinked") -> None:
    """Persist confidence and coverage to leaf_cache.json, per-key BEST-OF
    WITHIN ONE ORACLE.

    A re-measurement must never DOWNGRADE a recorded entry. Coverage varies run
    to run (stub availability, snapshot state, concolic luck), and the previous
    unconditional overwrite silently replaced good measurements with worse ones
    -- e.g. 0x6c high/86.3 -> weak/75.0 and 0xa83a0 100.0 -> 73.4, both from
    routine cron sweeps.

    Best-of is only meaningful between two measurements of the SAME thing, and
    the two oracles do not measure the same thing (hazard H8).  `func_size`
    changes from a delinked slice length to a bounds-table length, so coverage
    shifts for every target -- sometimes up, as with the lone RET at 0x1bf760
    that the delinked export inflates to 38 bytes and scores 2.6% on.  Kept
    best-of across oracles, an inflated pre-migration number would permanently
    shadow an honest post-migration one, and nothing in the file would say
    which oracle produced any given row.

    So each entry records the oracle that measured it, and a measurement from a
    DIFFERENT oracle replaces the row outright instead of competing with it.
    Entries with no `oracle` key predate the flag and are treated as delinked.

    The write is also skipped when nothing actually changes. The equivalence
    cron (run_local_equiv.sh) runs in the MAIN worktree, so an unconditional
    rewrite left main dirty and parked every auto-session land.
    """
    if not addr:
        return
    if isinstance(addr, int):
        norm = hex(addr)
    else:
        norm = addr if addr.startswith("0x") else hex(int(addr, 0))
    norm = norm.lower()
    try:
        if _LEAF_CACHE_PATH.exists():
            data = json.loads(_LEAF_CACHE_PATH.read_text(encoding="utf-8"))
        else:
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    existing = data.get(norm)
    if isinstance(existing, dict):
        old_cov = existing.get("coverage_pct")
        old_conf = existing.get("confidence")
        same_oracle = existing.get("oracle", "delinked") == oracle
        if same_oracle and isinstance(old_cov, (int, float)):
            better = (coverage_pct > old_cov
                      or (coverage_pct == old_cov
                          and _CONFIDENCE_RANK.get(confidence, -1)
                          > _CONFIDENCE_RANK.get(old_conf, -1)))
            if not better:
                return
        entry = dict(existing)
        entry["confidence"] = confidence
        entry["coverage_pct"] = coverage_pct
        entry["oracle"] = oracle
    else:
        entry = {"confidence": confidence, "coverage_pct": coverage_pct,
                 "oracle": oracle}
    if data.get(norm) == entry:
        return
    data[norm] = entry
    try:
        _LEAF_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LEAF_CACHE_PATH.write_text(
            json.dumps(dict(sorted(data.items())), indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

# The smoke target has to be testable under BOTH oracles, which is a narrower
# requirement than it sounds: it needs a committed function_bounds.json entry
# AND a delinked export on this host, and `delinked/` currently holds one whole
# object.  So it comes from game_state.obj and is picked for being a pure leaf
# -- no calls, no absolute data references -- because a non-leaf would need the
# symmetric interception that lands in step 6.
SELF_TEST_FUNC = "game_state_dispose_from_old_map"
SELF_TEST_FUNC_XBE = SELF_TEST_FUNC


def _run_self_test(verbose: bool = False, oracle: str = "delinked") -> int:
    """Run a smoke test against SELF_TEST_FUNC.

    Returns 0 on success, 1 on failure.
    """
    target = SELF_TEST_FUNC_XBE if oracle == "xbe" else SELF_TEST_FUNC
    print(f"[self-test] Running against '{target}' (oracle={oracle}) ...")
    return run_diff(target, num_seeds=20, base_seed=0xC0FFEE,
                    verbose=verbose, save_log=False, oracle=oracle)


# ---------------------------------------------------------------------------
# Main diff runner
# ---------------------------------------------------------------------------

def run_diff(func_name: str, num_seeds: int = 100, base_seed: int = 0,
             verbose: bool = False, save_log: bool = True,
             output_json: Optional[Path] = None,
             record_leaf: bool = True, z3_equiv: bool = False,
             allow_stubs: bool = False,
             float_tolerance_ulp: int = 0,
             float_tolerance_params: list = None,
             skip_esp: bool = False,
             quiet: bool = False,
             mem_trace: bool = False,
             state_snapshot: Optional[Path] = None,
             no_concolic: bool = False,
             real_callees: bool = False,
             max_insn: int = None,
             stub_arg_trace: bool = True,
             stub_conv_check: bool = True,
             value_corpus: Optional[Path] = None,
             oracle: str = "delinked",
             oracle_native_callees: bool = False) -> int:
    """Run the differential test.  Returns 0 if all pass, 1 if any diverge.

    `oracle` selects the reference side:

      "delinked"  a Ghidra-delinked COFF from `delinked/`.  Relocatable, so
                  every reference outside its own bytes -- globals, switch
                  tables, sibling functions, internal labels -- arrives as an
                  unresolved relocation this harness synthesizes by hand.
      "xbe"       a VA range of the pristine `halo-patched/cachebeta.xbe`,
                  mapped at its REAL virtual addresses.  Nothing was ever
                  unlinked from its neighbours, so there is nothing to
                  relocate; see docs/raw-xbe-oracle-migration.md.

    The default stays "delinked" until the A/B parity artifact of step 7 is
    committed.  Every emitted JSON records which was used, so no downstream
    artifact is ambiguous about it.
    """
    if oracle not in ("delinked", "xbe"):
        raise ValueError(f"unknown oracle mode {oracle!r}; "
                         f"expected 'delinked' or 'xbe'")
    _oracle_xbe = (oracle == "xbe")

    # In --allow-stubs mode each callee stub executes real oracle code, consuming
    # many more instructions than a 2-byte trampoline.  Use a higher default limit.
    _max_insn = max_insn if max_insn is not None else (1_000_000 if allow_stubs else MAX_INSN)

    sys.path.insert(0, str(_SCRIPT_DIR))
    from coff_loader import extract_function, CoffParseError, slice_looks_truncated
    from abi import parse_decl
    from seeds import generate_seeds
    import state as state_mod

    log_lines = []
    snapshot_overrides = None
    snapshot_arg_overrides = {}
    snapshot_stub_returns = {}
    snapshot_stub_writes = {}

    def log(msg: str = ""):
        print(msg)
        log_lines.append(msg)

    def info(msg: str = ""):
        log_lines.append(msg)
        if not quiet:
            print(msg)

    def finish(status: str, applicable: bool, reason: Optional[str],
               exit_code: int, **extra) -> int:
        payload = {
            "target": func_name,
            # Stamped so every downstream artifact is self-describing: a
            # verdict means something different under each oracle.
            "oracle": oracle,
            "status": status,
            "applicable": applicable,
            "reason": reason,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "seeds": 0,
            "log_path": None,
        }
        payload.update(extra)

        if save_log:
            log_dir = _REPO_ROOT / "artifacts" / "equivalence"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{func_name}_smoke.log"
            with open(log_path, "w") as f:
                f.write('\n'.join(log_lines) + '\n')
            payload["log_path"] = str(log_path)
            if not quiet:
                print(f"\n  Log saved to: {log_path}")

        if output_json:
            output_json.parent.mkdir(parents=True, exist_ok=True)
            output_json.write_text(json.dumps(payload, indent=2) + "\n",
                                   encoding="utf-8")

        return exit_code

    info(f"=== unicorn_diff: {func_name} ===")

    if _UNICORN_IMPORT_ERROR:
        log("ERROR: unicorn not importable. Activate the project venv or install:")
        log(f"  {_REPO_ROOT}/.venv/bin/python3 -m pip install unicorn lief")
        return finish("not_applicable", False, "unicorn_unavailable", 2)

    # --- Locate kb.json entry ---
    kb = _load_kb()
    entry = _find_kb_entry(kb, func_name)
    if not entry:
        # Try as address
        entry = _find_kb_entry_by_addr(kb, func_name)
    if not entry:
        log(f"ERROR: '{func_name}' not found in kb.json")
        return finish("error", True, "missing_kb_entry", 1)

    decl = entry.get("decl", "")
    addr = entry.get("addr", "")
    obj_name = entry.get("_obj_name", "")
    # Capture the target address NOW, for the leaf_cache write ~1100 lines
    # below. Six later loops in this same function say `for addr, ... in`
    # (global_reads, and the five mem-trace diff lists), each of which rebinds
    # this `addr` at function scope -- so by the time we record coverage it can
    # hold a *memory* address instead of the function's. That is how
    # leaf_cache.json collected keys like 0x3f800034 / 0xccccccda / 0x700804,
    # none of which is a function in kb.json: the measurement was filed under
    # a garbage key, and the real target's entry never got updated.
    target_addr = addr
    info(f"  decl : {decl}")
    info(f"  addr : {addr}")
    info(f"  obj  : {obj_name}")

    if not decl:
        log("ERROR: no 'decl' in kb.json entry")
        return finish("error", True, "missing_decl", 1)

    # --- Parse ABI ---
    abi = parse_decl(decl)
    info(f"  conv : {abi['conv']}")
    info(f"  params: {[(p.name, p.c_type, p.reg) for p in abi['params']]}")
    info(f"  return: {abi['return_type']} (st0={abi['ret_st0']}, edx_eax={abi['ret_edx_eax']})")

    if float_tolerance_ulp > 0 and float_tolerance_params is None:
        float_tolerance_params = [
            p.name for p in abi['params']
            if p.is_pointer and 'float' in p.c_type
        ]

    float_tolerance_slot_indices = None
    if float_tolerance_ulp > 0 and float_tolerance_params:
        ptr_idx = 0
        float_tolerance_slot_indices = []
        for p in abi['params']:
            if p.is_pointer:
                if p.name in float_tolerance_params:
                    float_tolerance_slot_indices.append(ptr_idx)
                ptr_idx += 1
        if float_tolerance_params:
            info(f"  float-tolerance: {float_tolerance_ulp} ULP for {float_tolerance_params}")

    # --- Locate .obj files ---
    delinked_path, build_path = _find_obj_paths(entry)
    build_compiled_on_demand = False

    # Try source path from kb.json entry
    if not build_path and entry.get("_obj_source"):
        build_path = _find_build_obj_for_source(entry["_obj_source"])

    if not delinked_path and not _oracle_xbe:
        # Try matching address to a FUN_XXXXXXXX name in delinked/
        addr_int_for_search = int(addr, 16) if addr.startswith("0x") else None
        if addr_int_for_search is not None:
            addr_sym_upper = f"FUN_{addr_int_for_search:08X}"
            addr_sym_lower = f"FUN_{addr_int_for_search:08x}"
            for d in list(DELINKED_DIR.glob("*.obj")) + \
                     sorted(DELINKED_DIR.glob("functions/*.obj")):
                try:
                    result = subprocess.run(
                        ["llvm-objdump", "-t", str(d)],
                        capture_output=True, text=True
                    )
                    if addr_sym_upper in result.stdout or addr_sym_lower in result.stdout:
                        delinked_path = d
                        break
                except Exception:
                    pass

    if not delinked_path and not _oracle_xbe:
        log(f"ERROR: cannot find delinked .obj for '{obj_name}'")
        log(f"  Searched delinked/ for {obj_name}.obj")
        return finish("not_applicable", False, "missing_delinked_reference", 2)

    if not build_path:
        compile_detail = None
        if entry.get("_obj_source"):
            build_path, compile_detail = _compile_build_obj_for_source(entry["_obj_source"])
        if build_path:
            build_compiled_on_demand = True
            info(f"  build   : {build_path} (compiled on demand)")
        else:
            log(f"ERROR: cannot find build .obj for '{obj_name}'")
            if compile_detail:
                log(f"  on-demand compile failed: {compile_detail}")
            log(f"  Run: python3 tools/build/build.py -q --target halo")
            return finish("not_applicable", False, "missing_build_object", 2)

    if _oracle_xbe:
        import xbe_image
        info(f"  oracle  : {xbe_image.PRISTINE_XBE.name} at real VAs "
             f"(md5 {xbe_image.PRISTINE_MD5})")
    else:
        info(f"  delinked: {delinked_path}")
    if not build_compiled_on_demand:
        info(f"  build   : {build_path}")

    # --- Extract function slices ---
    # The delinked obj uses FUN_00XXXXXX naming; the build obj uses
    # _funcname (MSVC cdecl) or funcname.
    addr_int = int(addr, 16) if addr.startswith("0x") else int(addr, 16)
    delinked_sym = f"FUN_{addr_int:08x}"

    oracle_bound_kind = None
    oracle_bound_provenance = None
    if _oracle_xbe:
        # No pairing heuristic, no chunk search, no symbol-name fallbacks: the
        # oracle is the bytes at this address in the one true XBE, bounded by
        # the committed tools/verify/function_bounds.json.
        oracle_slice, _ox_err = _xbe_oracle_slice(addr_int, func_name)
        if oracle_slice is None:
            log(f"ERROR: no XBE oracle bytes for {func_name} @ {addr}: {_ox_err}")
            return finish("not_applicable", False, "oracle_xbe_no_bytes", 2)
        oracle_bound_kind = oracle_slice.bound_kind
        oracle_bound_provenance = oracle_slice.bound_provenance
        per_func_ref = None
        info(f"  oracle bound: {addr}..{addr_int + len(oracle_slice.code):#x} "
             f"({oracle_bound_kind}, {oracle_bound_provenance})")
    else:
        # Prefer per-function delinked refs — they isolate callees as external
        # stubs, avoiding intra-object call resolution issues.
        per_func_ref = _per_function_ref(func_name)
        if per_func_ref:
            try:
                try:
                    oracle_slice = extract_function(str(per_func_ref), delinked_sym)
                except CoffParseError:
                    # A kb-renamed function (e.g. glow_trailing_particle_new)
                    # exports its real name — not FUN_<addr> — in the per-function
                    # delinked ref, so fall back to the kb function name.
                    oracle_slice = extract_function(str(per_func_ref), func_name)
                delinked_path = per_func_ref
                info(f"  (using per-function delinked ref: {per_func_ref.name})")
            except CoffParseError:
                per_func_ref = None  # fall through to main delinked
        if not per_func_ref:
            try:
                oracle_slice = extract_function(str(delinked_path), delinked_sym)
            except CoffParseError as e:
                # Try without leading underscore / with function name
                try:
                    oracle_slice = extract_function(str(delinked_path), func_name)
                except CoffParseError as e2:
                    per_func_ref2 = _per_function_ref(func_name)
                    if per_func_ref2:
                        try:
                            oracle_slice = extract_function(str(per_func_ref2), delinked_sym)
                            delinked_path = per_func_ref2
                        except CoffParseError as e3:
                            log(f"ERROR extracting oracle: {e}")
                            log(f"  (also tried '{func_name}': {e2})")
                            log(f"  (also tried split ref '{per_func_ref2.name}': {e3})")
                            return finish("not_applicable", False, "oracle_extract_failed", 2)
                    else:
                        base_stem = delinked_path.stem
                        chunked_match = None
                        for chunked in delinked_path.parent.glob(f"{base_stem}_*.obj"):
                            try:
                                result = subprocess.run(
                                    ["llvm-objdump", "-t", str(chunked)],
                                    capture_output=True, text=True
                                )
                                if delinked_sym in result.stdout or delinked_sym.upper() in result.stdout:
                                    chunked_match = chunked
                                    break
                            except Exception:
                                pass
                        if chunked_match:
                            try:
                                oracle_slice = extract_function(str(chunked_match), delinked_sym)
                                delinked_path = chunked_match
                            except CoffParseError as e3:
                                log(f"ERROR extracting oracle: {e}")
                                log(f"  (also tried chunked '{chunked_match.name}': {e3})")
                                return finish("not_applicable", False, "oracle_extract_failed", 2)
                        else:
                            log(f"ERROR extracting oracle: {e}")
                            log(f"  (also tried '{func_name}': {e2})")
                            return finish("not_applicable", False, "oracle_extract_failed", 2)

    try:
        lifted_slice = extract_function(str(build_path), func_name)
    except CoffParseError as e:
        try:
            lifted_slice = extract_function(str(build_path), delinked_sym)
        except CoffParseError as e2:
            kb_name = entry.get("name", "")
            if kb_name and kb_name != func_name:
                try:
                    lifted_slice = extract_function(str(build_path), kb_name)
                except CoffParseError as e3:
                    log(f"ERROR extracting lifted: {e}")
                    log(f"  (also tried '{delinked_sym}': {e2})")
                    log(f"  (also tried '{kb_name}': {e3})")
                    return finish("not_applicable", False, "lifted_extract_failed", 2)
            else:
                log(f"ERROR extracting lifted: {e}")
                log(f"  (also tried '{delinked_sym}': {e2})")
                return finish("not_applicable", False, "lifted_extract_failed", 2)

    info(f"  oracle code: {len(oracle_slice.code)} bytes, {len(oracle_slice.relocs)} relocs")
    info(f"  lifted code: {len(lifted_slice.code)} bytes, {len(lifted_slice.relocs)} relocs")

    from coff_loader import load_text_section

    def _has_raw_calls(code, relocs):
        """Check if code has E8 CALL instructions without matching relocations."""
        reloc_offsets = {r.virtual_address for r in relocs}
        for i in range(len(code) - 4):
            if code[i] == 0xE8 and (i + 1) not in reloc_offsets:
                return True
        return False

    # `oracle_text` exists to give a delinked slice its surrounding .text so
    # intra-object calls with real displacements land somewhere.  The image
    # already IS that surrounding code, at the right addresses, so there is
    # nothing to load.
    oracle_text = (None if _oracle_xbe else
                   (load_text_section(str(delinked_path))
                    if _has_raw_calls(oracle_slice.code, oracle_slice.relocs)
                    else None))

    if not oracle_slice.code:
        log("ERROR: oracle code is empty")
        return finish("error", True, "empty_oracle_code", 1)
    if not lifted_slice.code:
        log("ERROR: lifted code is empty")
        return finish("error", True, "empty_lifted_code", 1)

    # A delinked reference that does not reach the target leaves an oracle slice
    # that is only the first byte(s) of the prologue.  Emulating it compares the
    # lift against nothing, while byte-coverage reports 100% because every byte
    # present was executed -- a truncated reference otherwise yields a confident
    # -looking divergence.  This is missing evidence, not a behavioural result,
    # so it must not reach the seed loop.  Re-export the delinked object over a
    # range that covers the function (see the delinked-reference precondition in
    # CLAUDE.md) to make the target testable.
    if _oracle_xbe:
        # H2.  `slice_looks_truncated` only fires on `reached_section_end`,
        # which a raw slice never sets, so the same question -- does the
        # reference actually cover the function -- is asked of the committed
        # bounds table instead.
        unreliable = _oracle_bound_unreliable(addr_int)
        if unreliable:
            log(f"ERROR: XBE bound for {func_name} is not usable: {unreliable}")
            log(f"  the lifted body is {len(lifted_slice.code)} bytes.")
            return finish("not_applicable", False, "oracle_bound_unreliable", 2,
                          oracle_bound_kind=oracle_bound_kind,
                          oracle_bound_provenance=oracle_bound_provenance)
    else:
        truncated = slice_looks_truncated(oracle_slice)
        if truncated:
            log(f"ERROR: delinked reference does not cover {func_name}: {truncated}")
            log(f"  oracle slice is {len(oracle_slice.code)} byte(s) at section offset "
                f"0x{oracle_slice.section_offset:x} and runs to the end of the section;")
            log(f"  the lifted body is {len(lifted_slice.code)} bytes.")
            log(f"  Re-export {delinked_path.name} over a range covering this function.")
            return finish("not_applicable", False, "oracle_truncated", 2)

    # --- Check for external relocations ---
    #
    # `real_va` is set only by `_xbe_oracle_slice`, so it is the discriminator
    # for whether the oracle's (empty) relocation table means anything.  A
    # delinked slice takes the unchanged path; a raw slice is classified by
    # disassembly instead (hazard H1).
    _oracle_va = getattr(oracle_slice, "real_va", None)
    if _oracle_va is None:
        oracle_ok = _check_relocations(oracle_slice, "oracle", quiet=quiet,
                                      expect_relocs=True)
        oracle_raw_class = None
    else:
        oracle_raw_class = _classify_raw_oracle(oracle_slice.code, _oracle_va)
        oracle_ok = oracle_raw_class.category == "leaf"
        if not quiet:
            print(f"  [RAW-CLASS] oracle: {oracle_raw_class.category} "
                  f"({oracle_raw_class.reason})")
    lifted_ok = _check_relocations(lifted_slice, "lifted", quiet=quiet)
    is_leaf = oracle_ok and lifted_ok
    if record_leaf:
        _record_leaf_classification(addr, is_leaf)

    # Non-leaf: try stubs if allowed
    use_stubs = False
    stub_manager = None
    globals_seeds = None
    # None = compare every recorded stub call (correct when both sides stub
    # alike). Narrowed to the oracle/candidate intersection once both stub
    # maps exist; must stay bound for the leaf path, which never builds them.
    comparable_stub_sentinels = None
    # {callee VA: sentinel} for the raw-XBE oracle's symmetric interception
    # (H9).  Stays empty for the delinked oracle, which intercepts by
    # rewriting its own relocated call sites instead.  Both of these are set
    # inside the stub block below and read by the oracle's _run_function calls,
    # so they must be initialised BEFORE it and not again after -- a later
    # `= None` would silently discard --oracle-native-callees' ranges.
    oracle_intercept_map = {}
    oracle_native_ranges = None
    unresolved_dir32 = 0
    oracle_code_patched = oracle_slice.code
    lifted_code_patched = lifted_slice.code
    if not is_leaf and allow_stubs:
        from stubs import (classify_relocations, patch_dir32_relocs,
                           patch_rel32_calls, StubManager, GLOBALS_BASE, GLOBALS_SIZE,
                           StubArgTracer, compare_stub_arg_traces)
        # Same discriminator as the leaf gate above: a raw slice's empty reloc
        # table would classify as "leaf, no relocations", which is what
        # suppresses oracle-side stub sentinels under hazard H1.
        orc_cls = oracle_raw_class or classify_relocations(
            oracle_slice.relocs,
            getattr(oracle_slice, 'defined_symbols', set()))
        lft_cls = classify_relocations(lifted_slice.relocs,
                                       getattr(lifted_slice, 'defined_symbols', set()))
        info(f"  oracle class: {orc_cls.category} ({orc_cls.reason})")
        info(f"  lifted class: {lft_cls.category} ({lft_cls.reason})")

        # Load state snapshot BEFORE the DIR32 patch so (a) identity
        # relocation can point snapshot-covered DAT_ symbols at their real
        # addresses and (b) _build_globals_seeds can seed the remaining slots
        # from real game-state data.  Snapshot regions are {addr: bytes}.
        if state_snapshot:
            from state_snapshot import load_snapshot, load_stub_writes
            snapshot_overrides, snapshot_arg_overrides, snapshot_stub_returns = \
                load_snapshot(str(state_snapshot))
            snapshot_stub_writes = load_stub_writes(str(state_snapshot))
            if snapshot_stub_writes:
                info(f"  stub writes: {list(snapshot_stub_writes.keys())}")
            info(f"  snapshot: {len(snapshot_overrides)} region(s) from {state_snapshot.name}")
            if snapshot_arg_overrides:
                info(f"  arg overrides: {list(snapshot_arg_overrides.keys())}")
            if snapshot_stub_returns:
                info(f"  stub returns: {snapshot_stub_returns}")

        # Patch DIR32 relocations for both, with non-overlapping slot ranges
        orc_defined = getattr(oracle_slice, 'defined_symbols', set())
        lft_defined = getattr(lifted_slice, 'defined_symbols', set())
        orc_rdata, orc_switch_identity_seeds = _apply_oracle_switch_table_fixups(
            int(addr, 16), oracle_slice, getattr(oracle_slice, 'rdata_map', {}),
            oracle_text is not None)
        orc_rdata = _relocate_rdata_text_refs(
            oracle_slice, orc_rdata, oracle_text is not None)
        lft_rdata = getattr(lifted_slice, 'rdata_map', {})
        lft_rdata = _relocate_rdata_text_refs(lifted_slice, lft_rdata, False)
        oracle_code_patched, orc_data_slots, orc_rdata_seeds = patch_dir32_relocs(
            oracle_slice.code, oracle_slice.relocs, orc_defined,
            return_slots=True, rdata_map=orc_rdata,
            snapshot_regions=snapshot_overrides)
        oracle_code_patched = bytes(oracle_code_patched)
        # Rebase intra-.text label refs (MSVC/VC71 switch jump tables) that
        # patch_dir32_relocs leaves untouched because their labels are
        # defined_symbols. Skipped harmlessly when no such labels exist.
        oracle_code_patched = _relocate_text_label_refs(
            oracle_slice, oracle_code_patched, oracle_text is not None)
        lft_globals_base = GLOBALS_BASE + len(orc_data_slots) * 256
        # Under --oracle=xbe both instances map the pristine image, so a
        # candidate DIR32 whose real address is in the span is pointed AT that
        # address rather than at a private slot.  Both sides then read and
        # write one address set, which is what makes --mem-trace mean anything
        # (two disjoint sets are 100% false positives) and what lets a
        # pointer-returning function's EAX be compared at all.
        _lft_image_span = _image_span_cached() if _oracle_xbe else None
        lifted_code_patched, lft_data_slots, lft_rdata_seeds = patch_dir32_relocs(
            lifted_slice.code, lifted_slice.relocs, lft_defined,
            globals_base=lft_globals_base,
            return_slots=True, rdata_map=lft_rdata,
            snapshot_regions=snapshot_overrides,
            image_span=_lft_image_span)
        lifted_code_patched = bytes(lifted_code_patched)
        lifted_code_patched = _relocate_text_label_refs(
            lifted_slice, lifted_code_patched, False)

        # A candidate DIR32 that still holds a private SLOT after identity
        # relocation is a site where the two sides do not share storage: the
        # oracle reads and writes the real address, the candidate a 256-byte
        # stand-in seeded from whatever evidence we had.  Every such site is a
        # place --mem-trace can report a divergence that is only an address
        # mismatch, so the count is a confidence input, not a failure.
        # Two kinds of slot are excluded because they are correct, not
        # unresolved:
        #   `__imp_X`  is SUPPOSED to keep a slot -- the extra dereference
        #              resolves through it to the real address.
        #   an rdata_map symbol is the candidate's OWN constant (a string
        #              literal like ??_C@_01NOFIACDB@w?$AA@), which lives in
        #              its .obj and not in the image at all; its slot is
        #              seeded from the real section bytes, and a read of it
        #              cannot produce a write-trace divergence.
        unresolved_dir32 = 0
        if _oracle_xbe:
            unresolved_dir32 = sum(1 for _s in lft_data_slots
                                   if not _s.startswith("__imp_")
                                   and _s not in lft_rdata)
            if unresolved_dir32:
                info(f"  unresolved dir32: {unresolved_dir32} candidate "
                     f"site(s) kept a private slot (no real address resolved)")

        # The candidate's slots sit above the oracle's, so with enough oracle
        # slots the candidate arena runs past GLOBALS_BASE+GLOBALS_SIZE. Tell
        # the stub-arg comparator how far it actually reaches, or every such
        # candidate slot pointer reads as a hard argument mismatch.
        from stubs import (set_globals_arena_top, reset_globals_arena_top,
                           set_globals_slot_real_map,
                           reset_globals_slot_real_map)
        reset_globals_arena_top()
        set_globals_arena_top(lft_globals_base + len(lft_data_slots) * 256)

        # Resolve the oracle's DIR32 symbols against the pristine XBE, so a
        # Ghidra label with no kb.json counterpart still seeds its slot.
        orc_addr_hints = _xbe_dir32_symbol_addrs(int(addr, 16),
                                                 oracle_slice.relocs)

        # Tell the stub-arg comparator which real global each oracle slot
        # stands for, so `&global` passed as an argument compares equal
        # across the two address spaces instead of reading as a wrong arg.
        reset_globals_slot_real_map()
        set_globals_slot_real_map({
            slot: orc_addr_hints[sym]
            for sym, slot in orc_data_slots.items() if sym in orc_addr_hints})

        # dllimport indirection first, so real snapshot / known-globals data
        # from _build_globals_seeds overrides it rather than the reverse.
        globals_seeds = _seed_dllimport_indirection(orc_data_slots,
                                                    lft_data_slots,
                                                    orc_addr_hints)
        globals_seeds.update(_build_globals_seeds(
            orc_data_slots, lft_data_slots,
            snapshot_overrides=snapshot_overrides,
            sym_addr_hints=orc_addr_hints,
            image_mapped=_oracle_xbe))
        globals_seeds.update(orc_rdata_seeds)
        globals_seeds.update(lft_rdata_seeds)
        # Seed MSVC two-level switch index-maps at their original VA so the
        # oracle's absolute (non-reloc'd) index-map read resolves; the reloc'd
        # pointer-table read keeps using its globals slot from orc_rdata_seeds.
        globals_seeds.update(orc_switch_identity_seeds)
        shared_stub_sentinels = {}
        orc_stub_map = {}
        lft_stub_map = {}

        def _canonicalize_relocs(relocs, defined_symbols):
            """Rewrite 'FUN_XXXXXXXX' reloc symbols to their kb.json friendly
            name so the oracle and candidate assign the SAME stub sentinel to
            the same real callee.  The delinked oracle often has no name for
            a callee (still raw FUN_<addr>) even after kb.json gives it one
            for the candidate — without this, patch_rel32_calls's name-keyed
            sentinel map treats them as two different callees, which
            corrupts the stub-arg-diff call sequence from that point on.

            Skips symbols already in defined_symbols (intra-object siblings):
            patch_rel32_calls's own "sym in defined_symbols" skip-check
            compares against the ORIGINAL raw name, so renaming those first
            would make a real sibling look external."""
            from coff_loader import CoffReloc
            out = []
            for r in relocs:
                if r.symbol_name in defined_symbols:
                    out.append(r)
                    continue
                stripped = r.symbol_name.lstrip('_')
                canon = _canonicalize_callee_key(stripped)
                if canon != stripped:
                    out.append(CoffReloc(virtual_address=r.virtual_address,
                                          symbol_name=canon,
                                          reloc_type=r.reloc_type,
                                          symbol_index=r.symbol_index))
                else:
                    out.append(r)
            return out

        orc_relocs_canon = _canonicalize_relocs(oracle_slice.relocs, orc_defined)
        lft_relocs_canon = _canonicalize_relocs(lifted_slice.relocs, lft_defined)

        # Patch REL32 calls for oracle.
        # When oracle_text is set the full .text section is mapped at CODE_BASE
        # and the function lives at CODE_BASE+section_offset — use that base.
        # When oracle_text is None the function is loaded directly at CODE_BASE.
        if orc_cls.call_count > 0:
            orc_code_base = (CODE_BASE + oracle_slice.section_offset) if oracle_text is not None else CODE_BASE
            oracle_code_patched, orc_stub_map = patch_rel32_calls(
                bytes(oracle_code_patched), orc_relocs_canon, orc_defined,
                code_base=orc_code_base,
                symbol_sentinels=shared_stub_sentinels,
                force_redirect_names=StubManager._INTERCEPT_NAMES)

        # For lifted code, also patch REL32 if present.
        # Lifted runs from CODE_BASE directly (no section prefix).
        #
        # include_defined: the single-function lifted extraction maps only the
        # target body, so a call to a DEFINED intra-object sibling (e.g.
        # 2290->FUN_001a0f10, 0680->FUN_001a03c0) would be left as an unpatched
        # rel32 pointing outside the loaded range -> wild fetch crash
        # (eip=0x1ffffc).  The oracle resolves such siblings via its whole-.text
        # mapping (oracle_text); to stay SYMMETRIC the candidate must redirect
        # the same sibling calls to shared sentinels.  Combined with
        # --real-callees, _load_callee_code loads the *delinked* sibling body for
        # BOTH sides (same bytes), giving a valid, lift-isolating comparison.
        # Gated (additive) so the rest of the suite is unperturbed.
        # Defined-sibling redirect is REQUIRED whenever we load real callee
        # bodies: the candidate maps only the target function slice, so a call
        # to a DEFINED intra-object sibling (whole-.obj candidate) is otherwise
        # left at its raw rel32 (disp 0 for a clang obj) — a `call $+5` that
        # pushes a return address the (absent) callee never pops.  Each such
        # call leaks 4 bytes; enough of them corrupt the frame so the epilogue
        # RETs into a stack value and control escapes (eip=0x1ffffc wild fetch).
        # Under --real-callees the sibling body is loaded at a shared sentinel
        # for BOTH sides, so redirecting is both crash-safe and symmetric.
        # Intercept-named siblings still route to their Python model: they are
        # in force_redirect_names (→ the intercept sentinel), _load_real_callees
        # skips _INTERCEPT_NAMES (no real code written), and should_intercept
        # fires the model via hook_code before any sentinel byte executes.
        _sibling_resolve = (os.environ.get("BIPED_SIBLING_RESOLVE") == "1"
                            or real_callees)
        # call_count counts EXTERN calls only; a candidate whose only calls
        # are defined intra-object siblings (e.g. FUN_0018c370 -> FUN_0018c100)
        # has call_count == 0 yet still needs sibling-resolve patching, or its
        # unpatched rel32 (disp 0) falls through leaving the return address on
        # the stack -> epilogue RETs into the saved-EBP slot.
        # Intercept-named callees (object_get_and_verify_type, datum_get, ...)
        # must always route to their Python model, even when the candidate's
        # WHOLE .obj defines them as intra-object siblings while the oracle's
        # per-function ref sees them as external.  Otherwise the defined
        # sibling call is left unpatched (disp 0, no-op) and falls through with
        # the caller's EAX standing in for the callee's result — see the
        # force_redirect_names doc in patch_rel32_calls.
        _intercept_names = StubManager._INTERCEPT_NAMES
        if lft_cls.call_count > 0 or _sibling_resolve or (
                lft_defined & {("_" + n) for n in _intercept_names}
                or (lft_defined & _intercept_names)):
            lifted_code_patched, lft_stub_map = patch_rel32_calls(
                bytes(lifted_code_patched), lft_relocs_canon, lft_defined,
                symbol_sentinels=shared_stub_sentinels,
                include_defined=_sibling_resolve,
                force_redirect_names=_intercept_names)

        # --- Raw-XBE oracle: intercept the CALLEE, not the call site (H9) ---
        #
        # The delinked oracle gets its sentinels from patch_rel32_calls, which
        # needs one relocation per call.  Raw image bytes have none: every call
        # is a finished E8 with a correct displacement into real code.  Left
        # alone the oracle therefore runs the whole engine natively while the
        # candidate hits return-0 trampolines, which compares "lift" against
        # "lift plus engine" and makes every verdict a false divergence.
        #
        # So the patch goes at the CALLEE's real entry VA -- `E9 <rel32>` to a
        # sentinel -- which catches every route into it: a call from the target
        # body, a call from a sibling the oracle also executes, a tail jump.
        # Rewriting call SITES could not do that; there is no relocation list
        # naming them, and a sibling's sites are not in the target's bytes at
        # all.
        #
        # SYMBOL identity is what pairs the sides up, not address identity: a
        # callee the candidate already stubs reuses that same sentinel, so the
        # stub-arg differential lines the two call sequences up.  A callee the
        # candidate never calls gets a FRESH sentinel rather than running for
        # real, because the point is symmetry -- an oracle-only call becomes a
        # visible one-sided stub record, which `comparable_stub_sentinels`
        # already knows how to report, instead of an escape into the image.
        #
        # The call targets come out of `_classify_raw_oracle`'s
        # `external_symbols`, which is where the disassembly that found them
        # already lives -- re-scanning here would be a second, drifting copy of
        # the operand rules that classifier documents at length.
        oracle_intercept_map = {}
        if _oracle_xbe and orc_cls is not None:
            from stubs import STUB_BASE as _SB, STUB_SLOT as _SS
            for _ext in orc_cls.external_symbols:
                _m = re.match(r'FUN_([0-9a-fA-F]{8})$', _ext)
                if not _m:
                    continue          # DAT_ ref, or the undecoded-tail marker
                _cva = int(_m.group(1), 16)
                _name = _canonicalize_callee_key("FUN_%08x" % _cva)
                _key = _name.lstrip("_")
                _sent = shared_stub_sentinels.get(_key)
                if _sent is None:
                    _sent = _SB + len(shared_stub_sentinels) * _SS
                    shared_stub_sentinels[_key] = _sent
                orc_stub_map[_sent] = _name
                oracle_intercept_map[_cva] = _sent
            if oracle_intercept_map:
                info(f"  oracle interception: {len(oracle_intercept_map)} "
                     f"callee VA(s) patched to sentinels")

        combined_stub_map = dict(orc_stub_map)
        combined_stub_map.update(lft_stub_map)
        # A stub-arg record exists only for an INTERCEPTED call, and the two
        # sides do not intercept the same set. Excuse a one-sided absence
        # ONLY when that side provably resolves the callee internally --
        # never merely because it produced no record, since "no record" is
        # also what a genuinely DROPPED call looks like (FUN_00019110 omits
        # the oracle's leading datum_get; that must keep failing).
        #   candidate silent  -> excused iff it DEFINES the symbol (whole .obj
        #                        sibling, executed natively)
        #   oracle silent     -> excused iff oracle_text is mapped (raw
        #                        intra-.text calls resolve inside the mapping)
        #                        or it defines the symbol
        def _defines(defined_set, sym):
            s = sym.lstrip("_")
            return s in defined_set or ("_" + s) in defined_set

        _excused = set()
        for _sent, _sym in combined_stub_map.items():
            _in_orc, _in_lft = _sent in orc_stub_map, _sent in lft_stub_map
            if _in_orc and not _in_lft:
                if _defines(lft_defined, _sym):
                    _excused.add(_sent)
            elif _in_lft and not _in_orc:
                if oracle_text is not None or _defines(orc_defined, _sym):
                    _excused.add(_sent)
        comparable_stub_sentinels = set(combined_stub_map) - _excused
        if _excused:
            info(f"  stub-arg: {len(_excused)} callee(s) excused from the "
                 f"call-sequence compare (resolved internally on one side)")
        if combined_stub_map:
            stub_mgr = StubManager(KB_JSON, DELINKED_DIR)
            stub_mgr.stub_return_overrides = snapshot_stub_returns
            stub_mgr.stub_write_overrides = snapshot_stub_writes
            # Allocate callee globals slots past the caller's own oracle+lifted
            # slots so they never overlap.
            callee_globals_base = lft_globals_base + len(lft_data_slots) * 256
            n_prepared = stub_mgr.prepare_stubs(
                combined_stub_map,
                globals_base=callee_globals_base,
                shared_sentinels=shared_stub_sentinels,
                real_callees=real_callees,
                snapshot_regions=snapshot_overrides)
            info(f"  stubs prepared: {n_prepared}/{len(combined_stub_map)}")
            if real_callees and stub_mgr._callee_dir32_slots:
                # Seed the globals the loaded callee code reads (DAT_ -> snapshot
                # / known_globals), same path as the caller's own globals.
                callee_hints = _xbe_addrs_at_sites(
                    stub_mgr._callee_dir32_sites)
                globals_seeds.update(_build_globals_seeds(
                    stub_mgr._callee_dir32_slots,
                    snapshot_overrides=snapshot_overrides,
                    sym_addr_hints=callee_hints))
                # Extend the slot -> real-global map with the callee slots, so
                # a `&global` argument coming out of real callee code compares
                # across address spaces the same way the target's own does.
                set_globals_slot_real_map({
                    **{slot: orc_addr_hints[sym]
                       for sym, slot in orc_data_slots.items()
                       if sym in orc_addr_hints},
                    **{slot: callee_hints[sym]
                       for sym, slot in stub_mgr._callee_dir32_slots.items()
                       if sym in callee_hints}})
                globals_seeds.update(stub_mgr._extra_rdata_seeds)
                info(f"  real callees: {stub_mgr._real_code_count} loaded, "
                     f"{len(stub_mgr._callee_dir32_slots)} callee globals seeded")
            # --oracle-native-callees: drop the H9 patch for a callee the
            # CANDIDATE also runs for real, and declare its VA range native so
            # the H5 guard lets the oracle execute it in place.  Symmetry is
            # preserved by only doing it where the candidate is native too --
            # the flag cannot be used to let the oracle run code the candidate
            # stubs, which is the asymmetry H9 exists to prevent.  Off by
            # default: the candidate's native callee body is DELINKED bytes at
            # a sentinel while the oracle's is image bytes at its real VA, so
            # the two are only as equivalent as the delink is faithful, and
            # that is the artifact this migration is removing.
            if oracle_native_callees and oracle_intercept_map:
                _native = []
                _kept = {}
                for _cva, _sent in oracle_intercept_map.items():
                    _stub = stub_mgr._stubs.get(_sent)
                    if _stub is None or not _stub.has_real_code:
                        _kept[_cva] = _sent
                        continue
                    _ext = _xbe_function_extent(_cva)
                    if _ext is None:
                        # No committed bound: the range would be a guess, and
                        # an over-wide native range is a blind spot in the
                        # escape guard, not a convenience.
                        _kept[_cva] = _sent
                        continue
                    _native.append((_cva, _ext))
                oracle_intercept_map = _kept
                if _native:
                    oracle_native_ranges = tuple(_native)
                    info(f"  oracle native callees: {len(_native)} range(s) "
                         f"left unpatched (candidate runs them for real too)")
            if stub_mgr.convention_mismatches:
                # Both oracle and candidate stubs honor the declared (wrong)
                # convention, so the differential is blind to this — but the
                # box is not (ESP drift, lift-learnings §30).
                #
                # The VERDICT is deferred to after the seed loop: at this point
                # _load_real_callees has not run, so we cannot yet tell which of
                # these callees will execute their own oracle bytes (decl never
                # consulted) or will never be called at all. Failing here blocked
                # 64 targets on 15 unported CRT decls, none of them reached by
                # lifted C. See blocking_convention_mismatches().
                n_mm = len(stub_mgr.convention_mismatches)
                log(f"WARNING: {n_mm} stub convention mismatch(es) — kb.json "
                    f"decl vs binary RET (lift-learnings §30):")
                for m in stub_mgr.convention_mismatches:
                    log(f"  {m}")
                log("  Fix the kb.json decl (check_stdcall_ret.py --addr "
                    "0xADDR). The run fails only if one of these stubs is "
                    "actually executed"
                    + ("" if stub_conv_check else " (disabled: "
                       "--no-stub-conv-check)") + ".")
            stub_manager = stub_mgr
            use_stubs = True

        if orc_cls.category in ("data_only", "leaf"):
            use_stubs = True  # DIR32 patching alone is enough

    if not is_leaf and not use_stubs:
        log("ERROR: function has external relocations — cannot emulate without full linker.")
        log("  (This function calls other functions or references globals.)")
        log("  Solution: use --allow-stubs, or choose a pure leaf function.")
        return finish("not_applicable", False, "external_relocations", 2)

    # --- Z3 formal equivalence proof (optional) ---
    #
    # A proof is ADDITIVE evidence, not a substitute for the seed sweep. This
    # used to `return finish("pass", ..., seeds=0)`, so a proven function was
    # never emulated at all -- meaning any flaw in the proof (see the strict-lift
    # soundness gate in z3_equiv/x86_to_z3, and test_z3_strict_lift.py) silenced
    # every other check on that function. Now we record the proof and fall
    # through to Unicorn; the two are independent oracles and a disagreement
    # between them is itself a finding worth surfacing.
    z3_proven = False
    _z3_block = _z3_gate_reason(oracle_slice, lifted_slice,
                                oracle_raw_class) if z3_equiv and is_leaf else None
    if _z3_block:
        info(f"\n  Z3 proof skipped: {_z3_block}")
    if z3_equiv and is_leaf and not _z3_block:
        try:
            from z3_equiv import prove_equivalence
            info("\n  Attempting Z3 formal equivalence proof...")
            eq_result = prove_equivalence(oracle_slice.code, lifted_slice.code, abi)
            if eq_result.proven:
                log(f"  Z3 PROVEN EQUIVALENT (continuing to Unicorn for confirmation)")
                z3_proven = True
                # Update cache
                if record_leaf:
                    try:
                        cache_data = json.loads(_LEAF_CACHE_PATH.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        cache_data = {}
                    norm = addr.lower() if addr.startswith("0x") else hex(int(addr, 0)).lower()
                    entry = cache_data.get(norm, {})
                    if isinstance(entry, str):
                        entry = {"class": entry}
                    entry["z3_proven"] = True
                    cache_data[norm] = entry
                    _LEAF_CACHE_PATH.write_text(
                        json.dumps(dict(sorted(cache_data.items())), indent=2) + "\n",
                        encoding="utf-8")
            elif eq_result.counterexample:
                log(f"  Z3 found divergence: {eq_result.counterexample}")
                log(f"  Falling through to Unicorn to confirm...")
            elif eq_result.not_applicable:
                log(f"  Z3 not applicable: {eq_result.reason}")
            elif eq_result.timeout:
                log(f"  Z3 timeout: {eq_result.reason}")
        except ImportError:
            pass
        except Exception as e:
            log(f"  Z3 equiv error: {e}")

    # --- Generate seeds (Z3 branch-coverage + random/corner) ---
    # Stubbable non-leaf functions (all calls intercepted) are safe for Z3
    # seeds and full corner-case values — the stub manager handles callees.
    seed_safe_mode = not is_leaf and not use_stubs
    z3_extra = []
    if seed_safe_mode:
        info("  seed mode: non-leaf valid-path execution (z3 branch seeds disabled)")
    elif not is_leaf:
        info("  seed mode: stubbable non-leaf (z3 branch seeds + full corners enabled)")
    if not seed_safe_mode:
        try:
            from z3_seeds import extract_branch_seeds
            z3_extra = extract_branch_seeds(oracle_slice.code, abi)
            if z3_extra:
                info(f"  z3 branch seeds: {len(z3_extra)}")
        except ImportError:
            pass
        except Exception as e:
            log(f"  z3 seed warning: {e}")

    params = abi['params']
    seeds = generate_seeds(params, num_seeds=num_seeds, base_seed=base_seed,
                           z3_seeds=z3_extra, safe_mode=seed_safe_mode)

    # Apply snapshot arg overrides: constrain specific params to fixed/range values
    # For pointer params, use hex string value as scratch buffer content.
    # For scalar params, use int or [lo, hi] range.
    if snapshot_arg_overrides:
        import random as _rng_mod
        _ov_rng = _rng_mod.Random(base_seed + 0x55AA)
        param_idx_map = {}
        for pi, p in enumerate(params):
            param_idx_map[p.name] = pi
            if p.reg:
                param_idx_map[p.reg.lower()] = pi
        for key, val in snapshot_arg_overrides.items():
            idx = param_idx_map.get(key)
            if idx is None:
                continue
            for sv in seeds:
                if isinstance(val, str):
                    sv[idx] = bytes.fromhex(val)
                elif isinstance(val, list) and len(val) == 2:
                    sv[idx] = _ov_rng.randint(val[0], val[1])
                elif val is None:
                    sv[idx] = None   # NULL pointer pin (JSON null)
                elif isinstance(val, float):
                    sv[idx] = val    # float pin — setup_args packs '<f'
                else:
                    sv[idx] = int(val)

    info(f"\n  Running {len(seeds)} seeds...")
    info("")

    passed = 0
    failed = 0
    seq_detail_logged = 0  # cap call-seq dumps; see the emit site below
    errors = 0
    # Seeds where BOTH sides escaped to the same address: no evidence either
    # way, so neither a pass nor an error.  See `_symmetric_escape`.
    domain_skipped = 0
    error_details = []
    first_diff = None
    trace_diff_count = 0
    # Affirmative heap-output evidence accumulated across seeds (BIPED_HEAP_COMPARE).
    # Keyed by (addr, size) -> last observed sample, so the report can show the
    # exact output offsets witnessed and their oracle/candidate values.
    heap_matched = {}          # (addr,size) -> value (bit-exact)
    heap_matched_tol = {}      # (addr,size) -> (oracle, lifted, ulp)
    heap_value_diffs = {}      # (addr,size) -> (oracle, lifted)  REAL-BUG candidates
    heap_oracle_only = set()   # addresses written only by oracle (disjoint base?)
    heap_lifted_only = set()   # addresses written only by candidate
    _heap_compare_on = os.environ.get("BIPED_HEAP_COMPARE") == "1"
    all_visited_pcs = {}
    oracle_returns = set()
    oracle_scratch_digests = set()
    oracle_write_digests = set()
    merged_global_reads = {}
    merged_auto_mapped_pages = set()
    # The oracle's code lives at its real VA under --oracle=xbe, so every
    # coverage/gap computation below keys off that instead of CODE_BASE.
    oracle_image = None
    oracle_entry_va = None
    if _oracle_xbe:
        import xbe_image
        xbe_image.assert_pristine()
        oracle_image = xbe_image.load_xbe()
        oracle_entry_va = addr_int
        oracle_func_base = addr_int
    else:
        oracle_func_base = (CODE_BASE + oracle_slice.section_offset) if oracle_text else CODE_BASE
    oracle_func_end = oracle_func_base + len(oracle_code_patched)

    # H12.  `globals_seeds` is one dict of ABSOLUTE addresses handed to both
    # sides.  Once the image is mapped, an in-image address already holds the
    # right bytes -- the file's where the file backs it, the capture's over the
    # BSS holes (`_seed_capture_over_bss`) -- so a seed there would overwrite
    # ground truth with a slot value.  Withheld from BOTH sides under
    # --oracle=xbe, because under step 6b both instances map the image.
    #
    # What survives the filter is the slot seeds at GLOBALS_BASE, which is
    # outside the span by construction (test_memmap pins that), so the
    # candidate's unresolved DIR32 slots keep working exactly as before.
    orc_globals_seeds = lft_globals_seeds = globals_seeds
    if _oracle_xbe and globals_seeds:
        _img_lo, _img_hi = _image_span_cached()
        _filtered = {a: d for a, d in globals_seeds.items()
                     if not (_img_lo <= a < _img_hi)}
        orc_globals_seeds = lft_globals_seeds = _filtered
        _dropped = len(globals_seeds) - len(_filtered)
        if _dropped:
            info(f"  seeds: {_dropped} in-image seed(s) withheld from both "
                 f"sides; the shared image supplies those bytes")
    enable_trace = mem_trace or (use_stubs and not is_leaf)
    # Stub-arg tracing is enabled when --allow-stubs is on AND --no-stub-arg-trace
    # was not given AND there are actual stub addresses to trace.
    enable_stub_arg_trace = (stub_arg_trace and use_stubs
                             and stub_manager is not None
                             and bool(stub_manager.get_stub_addresses()))

    # Accumulated stub-arg diff statistics across all seeds
    stub_arg_total_calls = 0
    stub_arg_total_mismatches = 0
    stub_arg_total_soft = 0
    # {reason: count} for args excused by a callee-contract rule (assert
    # metadata, memset fill width). Reported so an exemption stays visible.
    stub_arg_soft_reasons = {}
    stub_arg_mismatch_details = []  # (seed_label, seq, callee_name, arg_pos, o_val, c_val)
    stub_arg_failed_seeds = 0

    for si, seed_vec in enumerate(seeds):
        seed_label = f"seed[{si:3d}]"

        # Run oracle
        oracle_tracer = StubArgTracer() if enable_stub_arg_trace else None
        try:
            oracle_state = _run_function(oracle_code_patched, abi, seed_vec,
                                         verbose=verbose, map_globals=use_stubs,
                                         auto_map_unmapped=True,
                                         stub_manager=stub_manager,
                                         globals_seeds=orc_globals_seeds,
                                         section_code=oracle_text,
                                         func_offset=oracle_slice.section_offset,
                                         collect_mem_trace=enable_trace,
                                         memory_overrides=snapshot_overrides,
                                         stub_arg_tracer=oracle_tracer,
                                         max_insn=_max_insn,
                                         image=oracle_image,
                                         entry_va=oracle_entry_va,
                                         native_callee_ranges=oracle_native_ranges,
                                         intercept_vas=oracle_intercept_map)
        except Exception as exc:
            log(f"  {seed_label} ORACLE-ERROR: {exc}")
            error_details.append(f"{seed_label} ORACLE-ERROR: {exc}")
            errors += 1
            continue

        # Run lifted
        cand_tracer = StubArgTracer() if enable_stub_arg_trace else None
        try:
            lifted_state = _run_function(lifted_code_patched, abi, seed_vec,
                                         verbose=verbose, map_globals=use_stubs,
                                         auto_map_unmapped=True,
                                         stub_manager=stub_manager,
                                         globals_seeds=lft_globals_seeds,
                                         lifted=True,
                                         collect_mem_trace=enable_trace,
                                         memory_overrides=snapshot_overrides,
                                         max_insn=_max_insn,
                                         stub_arg_tracer=cand_tracer,
                                         image=oracle_image,
                                         intercept_vas=oracle_intercept_map)
        except Exception as exc:
            log(f"  {seed_label} LIFTED-ERROR: {exc}")
            error_details.append(f"{seed_label} LIFTED-ERROR: {exc}")
            errors += 1
            continue

        _sym = _symmetric_escape(oracle_state.error, lifted_state.error)
        if _sym is not None:
            log(f"  {seed_label} DOMAIN-SKIP: both sides escaped to "
                f"{_sym:#x}; the seed leaves the function's domain, so it is "
                f"scored as neither a pass nor an error")
            domain_skipped += 1
            continue
        if oracle_state.error:
            log(f"  {seed_label} ORACLE-CRASH: {oracle_state.error}")
            error_details.append(f"{seed_label} ORACLE-CRASH: {oracle_state.error}")
            errors += 1
            continue
        if lifted_state.error:
            log(f"  {seed_label} LIFTED-CRASH: {lifted_state.error}")
            error_details.append(f"{seed_label} LIFTED-CRASH: {lifted_state.error}")
            if os.environ.get("BIPED_CRASH_PCS") == "1":
                _pcs = sorted(lifted_state.visited_pcs)[-12:] if lifted_state.visited_pcs else []
                log(f"    last visited PCs (sorted tail): {[hex(p) for p in _pcs]}")
            errors += 1
            continue
        # If either side hit the instruction limit, the register state is
        # garbage from an aborted loop — treat as error, not a divergence.
        if oracle_state.insn_count >= _max_insn or lifted_state.insn_count >= _max_insn:
            msg = f"{seed_label} INSN-LIMIT: oracle={oracle_state.insn_count} lifted={lifted_state.insn_count}"
            log(f"  {msg} (skipped — likely assert→stub loop)")
            error_details.append(msg)
            errors += 1
            continue

        # Collect coverage data and global reads from oracle execution
        for pc, sz in oracle_state.visited_pcs.items():
            if oracle_func_base <= pc < oracle_func_end and pc not in all_visited_pcs:
                all_visited_pcs[pc] = sz
        if not abi['ret_void']:
            oracle_returns.add(oracle_state.eax)
        # Observable-output evidence beyond EAX. A function can be fully
        # exercised and still return the same value every seed (e.g. one that
        # returns its own out-param pointer) -- judging path diversity on EAX
        # alone marks those "weak" at 100% coverage. Scratch payloads and
        # memory writes are the outputs that actually vary there.
        if oracle_state.scratch_data:
            oracle_scratch_digests.add(hash(oracle_state.scratch_data))
        if oracle_state.mem_writes:
            oracle_write_digests.add(
                hash(tuple(sorted((w.address, w.size, w.value)
                                  for w in oracle_state.mem_writes))))
        for addr, val in oracle_state.global_reads.items():
            if addr not in merged_global_reads:
                merged_global_reads[addr] = val
        merged_auto_mapped_pages.update(oracle_state.auto_mapped_pages)

        # Memory-trace comparison (side-effect writes)
        if enable_trace and oracle_state.mem_writes and lifted_state.mem_writes:
            tdiff = state_mod.compare_mem_traces(
                oracle_state, lifted_state,
                float_tolerance_ulp=(float_tolerance_ulp if _heap_compare_on else 0))
            if tdiff.has_differences():
                trace_diff_count += 1
                if verbose:
                    log(f"  {seed_label} TRACE-DIFF: {tdiff.summary()}")
                    _ovals = {w.address: w.value for w in oracle_state.mem_writes}
                    _lvals = {w.address: w.value for w in lifted_state.mem_writes}
                    for _a in sorted(tdiff.oracle_only)[:40]:
                        log(f"      oracle-only  0x{_a:08x} = {_ovals.get(_a, 0):#010x}")
                    for _a in sorted(tdiff.lifted_only)[:40]:
                        log(f"      lifted-only  0x{_a:08x} = {_lvals.get(_a, 0):#010x}")
                    for _a, _ov, _lv in tdiff.value_diffs[:40]:
                        log(f"      value-diff   0x{_a:08x}  oracle={_ov:#x} lifted={_lv:#x}")
            if _heap_compare_on:
                # Accumulate affirmative output evidence (heap = >= HEAP_LINE,
                # i.e. the live biped object). Restrict to heap so we do not
                # clutter the report with low-VA globals scratch.
                for addr, size, val in tdiff.matched:
                    if addr >= HEAP_LINE:
                        heap_matched[(addr, size)] = val
                for addr, size, ov, lv, ulp in tdiff.matched_within_tol:
                    if addr >= HEAP_LINE:
                        heap_matched_tol[(addr, size)] = (ov, lv, ulp)
                for addr, ov, lv in tdiff.value_diffs:
                    if addr >= HEAP_LINE:
                        heap_value_diffs[(addr, None)] = (ov, lv)
                for addr in tdiff.oracle_only:
                    if addr >= HEAP_LINE:
                        heap_oracle_only.add(addr)
                for addr in tdiff.lifted_only:
                    if addr >= HEAP_LINE:
                        heap_lifted_only.add(addr)

        # Determine what to compare based on return type
        ret_eax = not abi['ret_void'] and not abi['ret_st0'] and not abi.get('ret_is_ptr', False)
        diff = state_mod.compare(
            oracle_state, lifted_state,
            check_scratch=True,
            ret_eax=ret_eax,
            ret_eax_bits=abi.get('ret_bits', 32),
            ret_edx_eax=abi['ret_edx_eax'],
            ret_st0=abi['ret_st0'],
            check_st_count=1 if abi['ret_st0'] else 0,
            scratch_float_tolerance_ulp=float_tolerance_ulp,
            scratch_float_params=float_tolerance_slot_indices,
            st_tolerance_ulp=float_tolerance_ulp,
            st_compare_as_f32=abi['ret_st0'] and not abi.get('ret_double', False),
            check_esp=is_leaf if not skip_esp else False,
        )

        # Stub-arg trace comparison (before deciding pass/fail for this seed)
        stub_arg_diff = None
        if enable_stub_arg_trace and oracle_tracer and cand_tracer:
            stub_arg_diff = compare_stub_arg_traces(
                oracle_tracer, cand_tracer, seed_label=seed_label,
                comparable_sentinels=comparable_stub_sentinels)
            stub_arg_total_calls += stub_arg_diff.total_calls
            stub_arg_total_mismatches += stub_arg_diff.arg_mismatches
            stub_arg_total_soft += stub_arg_diff.soft_stack_ptr_matches
            for _reason, _n in stub_arg_diff.soft_reasons.items():
                stub_arg_soft_reasons[_reason] = \
                    stub_arg_soft_reasons.get(_reason, 0) + _n
            if stub_arg_diff.has_differences():
                stub_arg_failed_seeds += 1
                stub_arg_mismatch_details.extend(stub_arg_diff.details)
                if verbose:
                    log(f"  {seed_label} STUB-ARG-DIFF: {stub_arg_diff.summary()}")

        if diff.has_differences() or (stub_arg_diff is not None
                                      and stub_arg_diff.has_differences()):
            failed += 1
            if first_diff is None:
                first_diff = (si, seed_vec, diff, oracle_state, lifted_state)
            if verbose:
                log(f"  {seed_label} FAIL: {diff.summary()}")
                log(state_mod.format_state_verbose(oracle_state, "oracle"))
                log(state_mod.format_state_verbose(lifted_state, "lifted"))
                if diff.scratch_differs:
                    for line in _summarize_scratch_diff(params, oracle_state.scratch_data,
                                                        lifted_state.scratch_data):
                        log(line)
            else:
                if diff.has_differences():
                    log(f"  {seed_label} FAIL: {diff.summary()}")
                if stub_arg_diff is not None and stub_arg_diff.has_differences():
                    log(f"  {seed_label} FAIL: {stub_arg_diff.summary()}")
                    # Only for the first few diverging seeds: the sequences are
                    # identical across seeds in every case observed, so logging
                    # all 50 adds bulk without adding evidence.
                    if seq_detail_logged < 2:
                        for line in stub_arg_diff.sequence_detail():
                            log(line)
                        if stub_arg_diff.sequence_diverged:
                            seq_detail_logged += 1
        else:
            passed += 1
            if verbose and si < 3:
                info(f"  {seed_label} PASS")
                info(state_mod.format_state_verbose(oracle_state, "oracle"))

    # Coverage and confidence analysis
    oracle_func_size = len(oracle_code_patched)
    covered_bytes = sum(all_visited_pcs.values())
    coverage_pct = covered_bytes / oracle_func_size * 100 if oracle_func_size > 0 else 0
    unique_returns = len(oracle_returns)
    monotonic_return = unique_returns <= 1 and not abi['ret_void'] and passed > 0
    output_varied = (unique_returns > 1
                     or len(oracle_scratch_digests) > 1
                     or len(oracle_write_digests) > 1)

    confidence = _classify_confidence(coverage_pct, output_varied, passed)

    info("")
    info(f"  coverage: {covered_bytes}/{oracle_func_size} bytes ({coverage_pct:.1f}%) — confidence: {confidence}")
    if os.environ.get("BIPED_COVERAGE_GAPS") == "1":
        _gap_start = None
        _pc = oracle_func_base
        _covered_set = set()
        for _p, _s in all_visited_pcs.items():
            for _b in range(_p, _p + _s):
                _covered_set.add(_b)
        _gaps = []
        while _pc < oracle_func_end:
            if _pc not in _covered_set:
                if _gap_start is None:
                    _gap_start = _pc
            else:
                if _gap_start is not None:
                    _gaps.append((_gap_start, _pc))
                    _gap_start = None
            _pc += 1
        if _gap_start is not None:
            _gaps.append((_gap_start, oracle_func_end))
        for _g0, _g1 in _gaps:
            info(f"    gap: +0x{_g0 - oracle_func_base:x}..+0x{_g1 - oracle_func_base:x} ({_g1 - _g0}B)")
    if monotonic_return:
        ret_val = next(iter(oracle_returns)) if oracle_returns else 0
        if output_varied:
            # Constant EAX but the buffers/writes differ — normal for a
            # function that returns its own out-param. Not a diversity problem.
            info(f"  note: all {passed} seeds returned identical value "
                 f"(0x{ret_val:08x}), but scratch/memory output varied")
        else:
            log(f"  WARNING: all {passed} seeds returned identical value "
                f"(0x{ret_val:08x}) and no scratch/memory output varied "
                f"— low path diversity")
    if coverage_pct < 30 and passed > 0:
        log(f"  WARNING: only {coverage_pct:.1f}% code coverage — likely testing only early-exit path")
    if confidence == "none" and passed > 0:
        log(f"  WARNING: coverage {coverage_pct:.1f}% is below the "
            f"{COVERAGE_FLOOR_PCT:.0f}% floor — recording no confidence tier")

    # --- Concolic Phase 2: coverage-guided memory injection ---
    phase1_coverage = coverage_pct
    concolic_seeds_run = 0
    if (coverage_pct < 60 and not no_concolic and passed > 0
            and use_stubs and merged_global_reads):
        try:
            from concolic import (disassemble_branches, find_uncovered,
                                  generate_memory_injections, load_value_corpus)

            branches = disassemble_branches(oracle_code_patched, oracle_func_base)
            uncovered = find_uncovered(branches, all_visited_pcs, oracle_func_base)

            if uncovered:
                # Step 4: ground residual-branch injection in real frame values.
                corpus = load_value_corpus(value_corpus)
                z3_stats = {}
                injections = generate_memory_injections(
                    uncovered, merged_global_reads, oracle_func_base,
                    value_corpus=corpus,
                    code=oracle_code_patched,
                    visited_pcs=all_visited_pcs,
                    z3_stats=z3_stats)
                if corpus:
                    info(f"  concolic: using real-frame value corpus "
                         f"({len(corpus)} globals)")
                if z3_stats.get("stats") is not None:
                    info(f"  concolic: z3 path solve — "
                         f"{z3_stats['stats'].summary()}")

                if injections:
                    info(f"\n  concolic: {len(uncovered)} uncovered branch(es), "
                         f"{len(injections)} injection(s)")

                    phase2_seeds = generate_seeds(
                        params, num_seeds=min(10, num_seeds),
                        base_seed=base_seed ^ 0xC0FC011C, safe_mode=seed_safe_mode)

                    for inj_idx, injection in enumerate(injections[:8]):
                        merged_overrides = dict(snapshot_overrides or {})
                        merged_overrides.update(injection)

                        for seed_vec in phase2_seeds[:5]:
                            concolic_seeds_run += 1
                            sl = f"conc[{inj_idx}:{concolic_seeds_run:2d}]"

                            try:
                                orc_s = _run_function(
                                    oracle_code_patched, abi, seed_vec,
                                    verbose=verbose, map_globals=True,
                                    stub_manager=stub_manager,
                                    globals_seeds=orc_globals_seeds,
                                    section_code=oracle_text,
                                    func_offset=oracle_slice.section_offset,
                                    collect_mem_trace=enable_trace,
                                    memory_overrides=merged_overrides,
                                    max_insn=_max_insn,
                                    image=oracle_image,
                                    entry_va=oracle_entry_va,
                                    native_callee_ranges=oracle_native_ranges,
                                    intercept_vas=oracle_intercept_map)
                            except Exception as exc:
                                msg = f"{sl} ORACLE-ERROR: {exc}"
                                log(f"  {msg}")
                                error_details.append(msg)
                                errors += 1
                                continue

                            try:
                                lft_s = _run_function(
                                    lifted_code_patched, abi, seed_vec,
                                    verbose=verbose, map_globals=True,
                                    stub_manager=stub_manager,
                                    globals_seeds=lft_globals_seeds,
                                    lifted=True,
                                    collect_mem_trace=enable_trace,
                                    memory_overrides=merged_overrides,
                                    max_insn=_max_insn,
                                    image=oracle_image,
                                    intercept_vas=oracle_intercept_map)
                            except Exception as exc:
                                msg = f"{sl} LIFTED-ERROR: {exc}"
                                log(f"  {msg}")
                                error_details.append(msg)
                                errors += 1
                                continue

                            _sym = _symmetric_escape(orc_s.error, lft_s.error)
                            if _sym is not None:
                                log(f"  {sl} DOMAIN-SKIP: both sides escaped "
                                    f"to {_sym:#x}")
                                domain_skipped += 1
                                continue
                            if orc_s.error or lft_s.error:
                                if orc_s.error:
                                    msg = f"{sl} ORACLE-CRASH: {orc_s.error}"
                                    log(f"  {msg}")
                                    error_details.append(msg)
                                if lft_s.error:
                                    msg = f"{sl} LIFTED-CRASH: {lft_s.error}"
                                    log(f"  {msg}")
                                    error_details.append(msg)
                                errors += 1
                                continue
                            if orc_s.insn_count >= MAX_INSN or lft_s.insn_count >= MAX_INSN:
                                msg = f"{sl} INSN-LIMIT: oracle={orc_s.insn_count} lifted={lft_s.insn_count}"
                                log(f"  {msg}")
                                error_details.append(msg)
                                errors += 1
                                continue

                            for pc, sz in orc_s.visited_pcs.items():
                                if (oracle_func_base <= pc < oracle_func_end
                                        and pc not in all_visited_pcs):
                                    all_visited_pcs[pc] = sz
                            if not abi['ret_void']:
                                oracle_returns.add(orc_s.eax)
                            if orc_s.scratch_data:
                                oracle_scratch_digests.add(hash(orc_s.scratch_data))
                            if orc_s.mem_writes:
                                oracle_write_digests.add(
                                    hash(tuple(sorted((w.address, w.size, w.value)
                                                      for w in orc_s.mem_writes))))

                            ret_eax = (not abi['ret_void'] and not abi['ret_st0']
                                       and not abi.get('ret_is_ptr', False))
                            d = state_mod.compare(
                                orc_s, lft_s, check_scratch=True,
                                ret_eax=ret_eax,
                                ret_eax_bits=abi.get('ret_bits', 32),
                                ret_edx_eax=abi['ret_edx_eax'],
                                ret_st0=abi['ret_st0'],
                                check_st_count=1 if abi['ret_st0'] else 0,
                                scratch_float_tolerance_ulp=float_tolerance_ulp,
                                scratch_float_params=float_tolerance_slot_indices,
                                st_tolerance_ulp=float_tolerance_ulp,
                                st_compare_as_f32=abi['ret_st0'] and not abi.get('ret_double', False),
                                check_esp=False)

                            if d.has_differences():
                                failed += 1
                                if first_diff is None:
                                    first_diff = (sl, seed_vec, d, orc_s, lft_s)
                                log(f"  {sl} FAIL: {d.summary()}")
                            else:
                                passed += 1

                    # Recompute coverage after Phase 2
                    covered_bytes = sum(all_visited_pcs.values())
                    coverage_pct = (covered_bytes / oracle_func_size * 100
                                    if oracle_func_size > 0 else 0)
                    unique_returns = len(oracle_returns)
                    monotonic_return = (unique_returns <= 1
                                        and not abi['ret_void'] and passed > 0)
                    output_varied = (unique_returns > 1
                                     or len(oracle_scratch_digests) > 1
                                     or len(oracle_write_digests) > 1)

                    confidence = _classify_confidence(coverage_pct,
                                                      output_varied, passed)

                    info(f"  concolic result: {concolic_seeds_run} seeds, "
                         f"coverage {phase1_coverage:.1f}% → {coverage_pct:.1f}%, "
                         f"confidence: {confidence}")

        except ImportError:
            pass
        except Exception as e:
            info(f"  concolic error: {e}")

    info("")
    total_seeds = len(seeds) + concolic_seeds_run
    log(f"=== RESULTS: {passed} passed, {failed} failed, {errors} errors "
        f"/ {total_seeds} seeds ===")
    if domain_skipped:
        log(f"  domain-skipped: {domain_skipped} seed(s) drove BOTH sides out "
            f"of the function body to the same address (no evidence either "
            f"way; not counted as passes or errors)")

    if trace_diff_count and not quiet:
        log(f"  mem-trace: {trace_diff_count} seed(s) with write-trace divergences")

    if enable_stub_arg_trace:
        _soft_extra = "".join(
            f", {n} {reason}" for reason, n in sorted(stub_arg_soft_reasons.items()))
        log(f"  stub-arg differential: {stub_arg_total_calls} calls, "
            f"{stub_arg_total_mismatches} arg mismatch(es), "
            f"{stub_arg_total_soft} soft-matched stack ptr(s){_soft_extra}")
        if stub_arg_mismatch_details and not quiet:
            for (sl, seq, callee, ai, o_val, c_val) in stub_arg_mismatch_details[:20]:
                log(f"    {sl} call[{seq}] {callee} arg[{ai}]: "
                    f"oracle=0x{o_val:08x} candidate=0x{c_val:08x}")

    # Heap-output (live biped object) witness report. This is the deliverable
    # of BIPED_HEAP_COMPARE: affirmative evidence that the object-output writes
    # were compared at the SAME address and agreed (or disagreed = real bug).
    if _heap_compare_on:
        import struct as _struct

        def _as_f32(v):
            try:
                return _struct.unpack('<f', _struct.pack('<I', v & 0xFFFFFFFF))[0]
            except Exception:
                return float('nan')

        n_evidence = (len(heap_matched) + len(heap_matched_tol)
                      + len(heap_value_diffs) + len(heap_oracle_only)
                      + len(heap_lifted_only))
        log("")
        log("=== HEAP-OUTPUT WITNESS (BIPED_HEAP_COMPARE) ===")
        if n_evidence == 0:
            log("  NO heap-output writes witnessed inside any snapshot-mapped "
                "region.")
            log("  -> outputs unwitnessed: CONTROL-FLOW-ONLY (golden harness "
                "required), OR the run exercised no output path.")
        else:
            if heap_matched:
                log(f"  EXACT matches ({len(heap_matched)}):")
                for (addr, size) in sorted(heap_matched):
                    val = heap_matched[(addr, size)]
                    extra_f = f"  (~f32 {_as_f32(val):.6g})" if size == 4 else ""
                    log(f"    [0x{addr:08x}/{size}] oracle==candidate="
                        f"0x{val:0{size*2}x}{extra_f}")
            if heap_matched_tol:
                log(f"  WITHIN-TOL float matches ({len(heap_matched_tol)}):")
                for (addr, size) in sorted(heap_matched_tol):
                    ov, lv, ulp = heap_matched_tol[(addr, size)]
                    log(f"    [0x{addr:08x}/{size}] oracle=0x{ov:08x} "
                        f"({_as_f32(ov):.6g}) candidate=0x{lv:08x} "
                        f"({_as_f32(lv):.6g})  ulp={ulp}")
            if heap_value_diffs:
                log(f"  *** VALUE DIFFS ({len(heap_value_diffs)}) — REAL-BUG "
                    f"candidates (same addr, value differs beyond tol): ***")
                for (addr, _sz) in sorted(heap_value_diffs):
                    ov, lv = heap_value_diffs[(addr, _sz)]
                    log(f"    [0x{addr:08x}] oracle=0x{ov:08x} ({_as_f32(ov):.6g})"
                        f" candidate=0x{lv:08x} ({_as_f32(lv):.6g})")
            if heap_oracle_only or heap_lifted_only:
                log(f"  DISJOINT writes: {len(heap_oracle_only)} oracle-only, "
                    f"{len(heap_lifted_only)} candidate-only")
                log("  -> if these are the object outputs, oracle/candidate "
                    "resolved different bases -> CONTROL-FLOW-ONLY.")
                for a in sorted(heap_oracle_only)[:12]:
                    log(f"    oracle-only  0x{a:08x}")
                for a in sorted(heap_lifted_only)[:12]:
                    log(f"    cand-only    0x{a:08x}")

    if first_diff and not verbose:
        label, seed_vec, diff, oracle_state, lifted_state = first_diff
        log(f"\nFirst divergence at {label}:")
        log(f"  inputs: {_format_inputs(params, seed_vec)}")
        log(f"  diff  : {diff.summary()}")
        log(state_mod.format_state_verbose(oracle_state, "oracle"))
        log(state_mod.format_state_verbose(lifted_state, "lifted"))
        if diff.scratch_differs:
            for line in _summarize_scratch_diff(params, oracle_state.scratch_data,
                                                lifted_state.scratch_data):
                log(line)

    if record_leaf:
        # target_addr, NOT addr -- see the capture near the top of run_diff.
        _record_confidence(target_addr, confidence, round(coverage_pct, 1),
                           oracle=oracle)

    extra = dict(
        passed=passed, failed=failed, errors=errors,
        error_details=error_details,
        seeds=total_seeds,
        z3_proven=z3_proven,
        coverage_pct=round(coverage_pct, 1),
        func_size=oracle_func_size,
        # {hex pc: instruction size} of every oracle PC visited across all seeds
        # (post-concolic). Lets a multi-frame sweep union real-state coverage:
        # union_bytes = sum of sizes over the unioned PC set / func_size.
        covered_pcs={f"0x{pc:08x}": sz for pc, sz in sorted(all_visited_pcs.items())},
        unique_returns=unique_returns,
        confidence=confidence,
        trace_diffs=trace_diff_count,
        concolic_seeds=concolic_seeds_run,
        heap_matched=len(heap_matched),
        heap_matched_tol=len(heap_matched_tol),
        heap_value_diffs=len(heap_value_diffs),
        heap_oracle_only=len(heap_oracle_only),
        heap_lifted_only=len(heap_lifted_only),
        # Candidate DIR32 sites still backed by a private slot rather than the
        # shared image.  A confidence input: each one is a place a --mem-trace
        # divergence could be an address mismatch instead of a real one.
        unresolved_dir32=unresolved_dir32,
        # Seeds that carried no information because both sides escaped
        # identically.  Reported so a low `passed` count can be read against
        # the number of seeds that actually said anything.
        domain_skipped=domain_skipped,
    )
    if merged_global_reads:
        reads = []
        for addr, (size, value) in sorted(merged_global_reads.items()):
            reads.append({
                "address": f"0x{addr:08x}",
                "size": size,
                "value": f"0x{value:0{max(2, size * 2)}x}",
            })
        extra["global_reads"] = reads
    if merged_auto_mapped_pages:
        extra["auto_mapped_pages"] = [
            f"0x{addr:08x}" for addr in sorted(merged_auto_mapped_pages)
        ]
    if concolic_seeds_run:
        extra["phase1_coverage_pct"] = round(phase1_coverage, 1)

    # A Z3 proof and a seed divergence cannot both be right. Surface the
    # contradiction explicitly rather than letting the "fail" verdict quietly
    # coexist with a z3_proven=True flag downstream.
    if z3_proven and failed > 0:
        log("")
        log("  *** CONTRADICTION: Z3 proved equivalence but Unicorn found "
            f"{failed} diverging seed(s).")
        log("      One of the two oracles is wrong -- treat the proof as "
            "unsound until this is explained.")
        extra["z3_proof_contradicted"] = True

    # Stub-convention verdict (lift-learnings §30), deferred from setup: a wrong
    # decl only corrupts ESP if the SYNTHETIC stub honoring it actually ran. Both
    # sides drift identically, so a pass here is not evidence of anything --
    # report it even when every seed agreed.
    if stub_conv_check and stub_manager is not None:
        blocking = stub_manager.blocking_convention_mismatches()
        if blocking:
            log("")
            log(f"ERROR: {len(blocking)} EXECUTED stub(s) honored a wrong "
                f"convention — kb.json decl vs binary RET:")
            for m in blocking:
                log(f"  {m}")
            log("  Both sides drift ESP identically, so the differential is "
                "blind; fix the decl (check_stdcall_ret.py --addr 0xADDR).")
            return finish("error", True, "stub_convention_mismatch", 2, **extra)
        if stub_manager.convention_mismatches:
            # State WHY the mismatches did not block. Without this a gate that
            # silently stopped observing stub execution would look identical to
            # one correctly finding nothing to complain about.
            n_exec = len(stub_manager._executed_stubs)
            log(f"  stub-conv: {len(stub_manager.convention_mismatches)} "
                f"mismatch(es) did not block — {n_exec} synthetic stub(s) "
                f"executed this run, none of them wrongly declared")

    if failed > 0:
        return finish("fail", True, "divergence", 1, **extra)
    if errors > 0 and passed == 0:
        return finish("error", True, "emulation_error", 2, **extra)

    # A run that never entered the function body, or that observed exactly one
    # behaviour across every seed, has not compared anything -- both sides
    # agreed because neither side did any work. Reporting that as "pass" is
    # worse than reporting nothing: FUN_001c6900 printed "100 passed" at 5.7%
    # coverage with every seed bailing out of the same guard, and that line
    # reads downstream exactly like a differential that found no divergence.
    # Emit a distinct status/exit code so callers can tell "no divergence" from
    # "no evidence". A bare RET is exempt: there is no body to cover and byte
    # match already settles it.
    vacuous_reason = None
    if passed > 0 and oracle_func_size > 2:
        if coverage_pct < COVERAGE_FLOOR_PCT:
            vacuous_reason = (f"vacuous_coverage: {coverage_pct:.1f}% < "
                              f"{COVERAGE_FLOOR_PCT:.0f}% floor")
        elif not output_varied and passed >= VACUOUS_OUTPUT_MIN_SEEDS:
            # Only meaningful with a real sample. A pinned single-input probe
            # (test_player_control_angle_endpoint drives one hand-built seed at
            # a specific endpoint) is SUPPOSED to observe exactly one
            # behaviour; flagging that as vacuous would be a false positive.
            vacuous_reason = (f"vacuous_output: {unique_returns} unique "
                              f"return(s), no scratch or memory variation "
                              f"across {passed} seeds")
    if vacuous_reason:
        log("")
        log(f"  INCONCLUSIVE: {vacuous_reason}")
        log("      Every seed agreed because the differential never observed "
            "differing behaviour -- this is NOT evidence of equivalence.")
        log("      Drive the real paths with --state-snapshot (see the "
            "lift-synthetic-equivalence skill) before trusting this target.")
        return finish("inconclusive", True, vacuous_reason, 3, **extra)

    return finish("pass", True, None, 0, **extra)


def _format_inputs(params, seed_vec) -> str:
    parts = []
    for p, v in zip(params, seed_vec):
        if isinstance(v, bytes):
            # Show first few floats
            floats = []
            for i in range(min(3, len(v) // 4)):
                fv, = struct.unpack_from('<f', v, i * 4)
                floats.append(f"{fv:.4g}")
            parts.append(f"{p.name}=[{', '.join(floats)}, ...]")
        elif isinstance(v, float):
            parts.append(f"{p.name}={v:.6g}")
        else:
            parts.append(f"{p.name}={v}")
    return ", ".join(parts)


def _summarize_scratch_diff(params, oracle_scratch: bytes, lifted_scratch: bytes) -> list[str]:
    from abi import POINTER_SLOT

    lines = []
    slot_index = 0
    for p in params:
        if not p.is_pointer:
            continue

        start = slot_index * POINTER_SLOT
        end = start + POINTER_SLOT
        slot_index += 1

        oracle_slot = oracle_scratch[start:end]
        lifted_slot = lifted_scratch[start:end]
        if oracle_slot == lifted_slot:
            continue

        diff_offsets = []
        i = 0
        limit = min(len(oracle_slot), len(lifted_slot))
        while i < limit and len(diff_offsets) < 4:
            if oracle_slot[i] != lifted_slot[i]:
                diff_offsets.append(i)
            i += 1

        if not diff_offsets:
            lines.append(f"    scratch {p.name}: size mismatch")
            continue

        parts = []
        for off in diff_offsets:
            o_word = oracle_slot[off:off + 4].hex()
            l_word = lifted_slot[off:off + 4].hex()
            parts.append(f"+0x{off:x} oracle={o_word} lifted={l_word}")
        lines.append(f"    scratch {p.name}: " + ", ".join(parts))

    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _run_batch_classify() -> int:
    """Classify every bounded function into leaf_cache.json from the XBE.

    Until the raw-XBE migration this iterated `delinked/*.obj` and read each
    function's relocation table.  `delinked/` is gitignored and holds ONE
    object on this tree, so the sweep covered 20 functions out of ~8000 and
    every other row in the cache was whatever an older, better-stocked
    checkout had left behind.  `function_bounds.json` plus the pristine image
    cover all of them from two committed, reviewable inputs with no Ghidra in
    the loop, and `_classify_raw_oracle` recovers by disassembly what the
    relocation table used to supply.

    Bounds that cannot found a verdict are SKIPPED, not guessed.  A `class`
    derived from a run-time-computed extent, from a `table_data` range, or
    from a `no_terminator` end that is really just the next symbol's start
    would sit in the cache looking exactly like a reviewed one -- and
    `--allow-stubs` budgets, the z3 gate and target selection all read this
    file.  `_oracle_bound_unreliable` is the same gate the live oracle uses,
    so the cache cannot claim a classification for a function the oracle
    would refuse to run.
    """
    sys.path.insert(0, str(_SCRIPT_DIR))
    sys.path.insert(0, str(_REPO_ROOT / "tools" / "verify"))
    import xbe_image
    import xbe_reference

    bounds_path = _REPO_ROOT / "tools" / "verify" / "function_bounds.json"
    try:
        bounds = json.loads(bounds_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {bounds_path}: {exc}", file=sys.stderr)
        return 2

    addrs = []
    for key in bounds:
        if key.startswith("_"):
            continue
        try:
            addrs.append(int(key, 16))
        except ValueError:
            continue
    addrs.sort()

    cache = {}
    counts = {"leaf": 0, "data_only": 0, "stubbable": 0, "non_leaf": 0}
    skipped = {}

    for va in addrs:
        why = _oracle_bound_unreliable(va)
        if why is not None:
            skipped[hex(va)] = why
            continue
        code, err = xbe_reference.function_bytes(va)
        if not code:
            skipped[hex(va)] = err or "no bytes for %#x" % va
            continue
        cls = _classify_raw_oracle(code, va)
        entry = {"class": cls.category}
        if cls.dir32_count > 0:
            entry["dir32_count"] = cls.dir32_count
        if cls.call_count > 0:
            entry["call_count"] = cls.call_count
        if cls.category == "non_leaf":
            entry["reason"] = cls.reason
        cache[hex(va)] = entry
        counts[cls.category] = counts.get(cls.category, 0) + 1

    try:
        if _LEAF_CACHE_PATH.exists():
            existing = json.loads(_LEAF_CACHE_PATH.read_text(encoding="utf-8"))
        else:
            existing = {}
    except (OSError, json.JSONDecodeError):
        existing = {}

    for addr, old_val in list(existing.items()):
        if isinstance(old_val, str):
            existing[addr] = {"class": old_val}

    # Canonicalize the key form to unpadded lowercase `0x<hex>`, which is what
    # `_record_confidence` (hex(addr)), function_bounds.json and kb.json all
    # use.  The old batch sweep derived its key from a delinked symbol name
    # (`FUN_00012000` -> "0x00012000") and so wrote a ZERO-PADDED key, which
    # left 6125 padded rows beside 1278 unpadded ones -- 1223 addresses present
    # in both forms.  That was not cosmetic: populate_regression_targets.py
    # looks each key up in a kb.json-derived index, whose addresses are
    # unpadded, so every padded row was invisible to target selection, and
    # `--batch-classify` could never update the row a measurement had written.
    # Measurements win on collision: the padded rows carry only `class`, and
    # all 1066 coverage numbers and all 58 z3_proven flags live on the
    # unpadded side, so folding padded into unpadded loses nothing.
    renamed = 0
    for addr in sorted(existing):
        if addr.startswith("_"):
            continue
        try:
            canon = hex(int(addr, 16))
        except ValueError:
            continue
        if canon == addr:
            continue
        src = existing.pop(addr)
        dst = existing.get(canon)
        if isinstance(dst, dict) and isinstance(src, dict):
            for k, v in src.items():
                dst.setdefault(k, v)
        elif canon not in existing:
            existing[canon] = src
        renamed += 1

    # H8.  `class`/`dir32_count`/`call_count` are re-derived above, but
    # `coverage_pct` and `confidence` are MEASUREMENTS from an emulation run,
    # and `oracle_func_size` changed from a delinked slice length to a
    # bounds-table length -- so every pre-migration coverage number describes a
    # different denominator than the one it will be compared against.  They are
    # dropped rather than carried forward or re-estimated: the nightly refills
    # them so each number has a run behind it, and a missing number reads as
    # "not measured yet" where a stale one reads as fact.  `z3_proven` is NOT
    # dropped -- re-validating those proofs is its own reviewed step
    # (tools/audit/revalidate_z3_proofs.py) and deleting the flags first would
    # destroy the very list that step works from.
    invalidated = 0
    for addr, val in existing.items():
        if addr.startswith("_") or not isinstance(val, dict):
            continue
        if val.get("oracle") == "xbe":
            continue            # already measured under this oracle
        if "coverage_pct" in val or "confidence" in val:
            val.pop("coverage_pct", None)
            val.pop("confidence", None)
            val.pop("oracle", None)
            invalidated += 1

    # Field-wise merge, not `existing.update(cache)`.  A wholesale replacement
    # would drop `z3_proven`, and the 58 flags in this file ARE the worklist
    # tools/audit/revalidate_z3_proofs.py reads -- deleting them would destroy
    # the list before anything re-validated it.  The four classification fields
    # are re-derived here and so are cleared first: an entry that used to carry
    # `dir32_count` and no longer does must lose it, or the stale count would
    # read as a fresh measurement.
    _DERIVED = ("class", "dir32_count", "call_count", "reason")
    for addr, entry in cache.items():
        dst = existing.get(addr)
        if not isinstance(dst, dict):
            existing[addr] = entry
            continue
        for field in _DERIVED:
            dst.pop(field, None)
        dst.update(entry)

    # Rows this sweep could not reproduce.  Two kinds: addresses the bounds
    # table does not cover (XDK/library thunks in the D3D..XPP sections, plus a
    # handful of mid-function addresses the old delinked sweep mistook for
    # entry points), and rows the H8 invalidation emptied out completely --
    # junk keys like the 0x3f800034 / 0xccccccda ones test_leaf_cache_key.py
    # documents, which held a measurement and nothing else.  An empty dict
    # asserts nothing, so it is dropped; a carried-over class is kept, because
    # deleting it would discard the only claim anyone ever made about that
    # address, but `_meta.carried_over` says how many rows the current inputs
    # cannot re-derive.
    dropped_empty = 0
    for addr in [a for a in existing if not a.startswith("_")]:
        val = existing[addr]
        if isinstance(val, dict) and not val:
            del existing[addr]
            dropped_empty += 1
    carried_over = sum(1 for a in existing
                       if not a.startswith("_") and a not in cache)

    existing["_meta"] = {
        "oracle": "xbe",
        "schema": 2,
        "xbe_md5": xbe_image.PRISTINE_MD5,
        "classified": len(cache),
        "bounds_entries": len(addrs),
        "carried_over": carried_over,
        "note": ("class/dir32_count/call_count are derived from "
                 "tools/verify/function_bounds.json + the pristine "
                 "cachebeta.xbe by _classify_raw_oracle. coverage_pct and "
                 "confidence are measurements and are absent until a run "
                 "under this oracle records them."),
    }

    _LEAF_CACHE_PATH.write_text(
        json.dumps(dict(sorted(existing.items())), indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Classified {len(cache)} of {len(addrs)} bounded function(s) "
          f"from the pristine image:")
    for cat, cnt in sorted(counts.items()):
        print(f"  {cat}: {cnt}")
    if skipped:
        reasons = {}
        for why in skipped.values():
            key = why.split(" ")[0] if " " in why else why
            # Group by the phrase after the address, which is what differs.
            for tag in ("table_data", "no_terminator", "computed at run time",
                        "no usable bound"):
                if tag in why:
                    key = tag
                    break
            reasons[key] = reasons.get(key, 0) + 1
        print(f"Skipped {len(skipped)} unreliable bound(s):")
        for key, cnt in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {key}: {cnt}")
    if invalidated:
        print(f"Invalidated coverage_pct/confidence on {invalidated} entry/ies "
              f"measured under the delinked oracle (H8); the nightly refills "
              f"them.")
    if renamed:
        print(f"Folded {renamed} zero-padded key(s) into the canonical "
              f"unpadded form")
    if dropped_empty:
        print(f"Dropped {dropped_empty} row(s) left with no content at all "
              f"(a measurement under a junk key and nothing else)")
    if carried_over:
        print(f"Carried over {carried_over} row(s) this sweep cannot "
              f"re-derive (no bounds-table entry)")
    print(f"Cache written to {_LEAF_CACHE_PATH} "
          f"({sum(1 for k in existing if not k.startswith('_'))} function "
          f"entries)")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Differential emulation tester: oracle .obj vs. lifted .obj"
    )
    parser.add_argument("func_name", nargs="?",
                        help="Function name or hex address (e.g. vector3d_scale_add or 0x12f80)")
    parser.add_argument("--seeds", type=int, default=100,
                        help="Number of test seeds (default: 100)")
    parser.add_argument("--seed", type=lambda x: int(x, 0), default=0,
                        help="Base RNG seed (default: 0)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print per-seed state dump")
    parser.add_argument("--self-test", action="store_true",
                        help=f"Run smoke test against {SELF_TEST_FUNC}")
    parser.add_argument("--list-funcs", metavar="OBJ",
                        help="List function symbols in a .obj file and exit")
    parser.add_argument("--output-json", type=Path, default=None,
                        help="Write structured result JSON to this path")
    parser.add_argument("--no-leaf-cache", action="store_true",
                        help="Do not update tools/equivalence/leaf_cache.json")
    parser.add_argument("--batch-classify", action="store_true",
                        help="Classify every function in tools/verify/function_bounds.json against the pristine XBE and update leaf_cache.json")
    parser.add_argument("--z3-equiv", action="store_true",
                        help="Attempt Z3 formal equivalence proof before Unicorn testing")
    parser.add_argument("--allow-stubs", action="store_true",
                        help="Enable non-leaf emulation with callee stubbing and DIR32 patching")
    parser.add_argument("--oracle", choices=("delinked", "xbe"),
                        default="xbe",
                        help="Reference side: 'xbe' (a VA range of the "
                             "pristine cachebeta.xbe mapped at its real "
                             "addresses, nothing to relocate) or 'delinked' "
                             "(a Ghidra-delinked COFF, relocatable, needs "
                             "relocation synthesis). Default: xbe, behind "
                             "tools/equivalence/"
                             "oracle_migration_expected_deltas.json. "
                             "'delinked' is retained only to reproduce a "
                             "pre-migration verdict and is scheduled for "
                             "removal. See docs/raw-xbe-oracle-migration.md")
    parser.add_argument("--oracle-native-callees", action="store_true",
                        help="--oracle=xbe only: let the oracle execute a "
                             "callee IN PLACE, at its real VA, instead of "
                             "diverting it to a sentinel -- but only where "
                             "the candidate also runs that callee for real "
                             "(--real-callees) and the callee has a committed "
                             "bound. Off by default: the candidate's native "
                             "body is delinked bytes at a sentinel while the "
                             "oracle's is image bytes in place, so the two "
                             "agree only as far as the delink is faithful, "
                             "which is the artifact this lane is removing.")
    parser.add_argument("--rich-stub-returns", action="store_true",
                        help="Return scratch pointers (not 0) from stubbed pointer-returning "
                             "accessors so callers get past their NULL check. Raises coverage, "
                             "but the scratch page is not a real object, so a caller that calls "
                             "through a function pointer in it will crash. Coverage exploration "
                             "only — prefer --real-callees or --state-snapshot for verdicts.")
    parser.add_argument("--max-insn", type=int, default=None, metavar="N",
                        help="Maximum instructions per emulation run (default: 1M with --allow-stubs, 100K otherwise)")
    parser.add_argument("--float-tolerance", type=int, default=0, metavar="ULP",
                        help="Allow N ULP difference for float pointer params in scratch comparison")
    parser.add_argument("--float-params", type=str, default=None, metavar="NAMES",
                        help="Comma-separated param names treated as float arrays (default: auto-detect)")
    parser.add_argument("--skip-esp", action="store_true",
                        help="Skip ESP delta comparison (expected to differ for non-leaf functions)")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress setup config and per-seed PASS lines; print only RESULTS and first failure")
    parser.add_argument("--mem-trace", action="store_true",
                        help="Enable memory-write trace differential (compare side-effect writes)")
    parser.add_argument("--state-snapshot", type=Path, default=None, metavar="PATH",
                        help="Load state snapshot JSON for memory initialization (replaces zero-fill)")
    parser.add_argument("--from-halorec", type=Path, default=None, metavar="REC",
                        help="Convert a .halorec recording frame to a state snapshot "
                             "(real object/actor/prop heap). Mutually exclusive with --state-snapshot.")
    parser.add_argument("--halorec-frame", default=None, metavar="SEL",
                        help="Frame selector for --from-halorec: index (int), 'first', 'last', "
                             "0.0-1.0 fraction, 't=SECONDS', or 'handle=0xHANDLE' (default: last)")
    parser.add_argument("--no-concolic", action="store_true",
                        help="Disable automatic concolic Phase 2 when coverage is low")
    parser.add_argument("--value-corpus", type=Path, default=None, metavar="PATH",
                        help="Real-frame value corpus {global: [observed values]} from "
                             "halorec_frame_sweep.py --emit-value-corpus; concolic Phase 2 "
                             "injects these feasible engine-produced values for residual "
                             "uncovered branches instead of invented constants.")
    parser.add_argument("--real-callees", action="store_true", default=None,
                        help="Run callees as native oracle code (loops iterate over "
                             "snapshot data) instead of return-0 stubs. Implies "
                             "--allow-stubs. ON BY DEFAULT since 2026-07-28; use "
                             "--no-real-callees to get return-0 stubs back.")
    parser.add_argument("--no-real-callees", dest="real_callees",
                        action="store_false",
                        help="Stub every callee to return 0 instead of sub-emulating "
                             "it. Measured cost of doing so, on 60 targets under 30%% "
                             "coverage: 10 that pass with real callees only error, and "
                             "2 more fail.")
    parser.add_argument("--no-stub-arg-trace", action="store_true",
                        help="Disable stub-argument differential (default: enabled with "
                             "--allow-stubs). When enabled, oracle and candidate argument "
                             "values for each callee stub hit are compared and mismatches "
                             "are reported.")
    parser.add_argument("--no-stub-conv-check", action="store_true",
                        help="Do not fail when a stubbed callee's kb.json calling "
                             "convention disagrees with its RET immediate in the "
                             "pristine XBE (lift-learnings §30). Both stubs honor "
                             "the declared convention, so the differential cannot "
                             "see the mismatch — the box crashes instead.")
    args = parser.parse_args()
    # Default ON, but only where stubbing is already in play.  The A/B evidence
    # for this default (60 targets under 30% coverage, plus 22 healthy controls)
    # compared --allow-stubs against --real-callees -- both with stubs enabled --
    # so it says nothing about runs that deliberately use neither.  Letting the
    # new default force allow_stubs on would silently change those too, well
    # beyond what was measured.
    # ...and not when a state snapshot is driving the run.  A snapshot already
    # supplies the real engine state that sub-emulating a callee only tries to
    # approximate, and its stub_returns are curated by whoever captured it --
    # some callee bodies asserts against synthetic state, which is exactly why
    # they were pinned to a return value instead.  Measured on FUN_000d04d0:
    # loading 29 real callees changed coverage not at all (14.3% either way) and
    # turned a passing seed into "call-seq diverged at index 3, 4 arg
    # mismatch(es)".  Snapshot runs were tuned against return-0 stubs; three of
    # the 62 regression targets (all --state-snapshot) failed on the flip.
    if args.real_callees is None:
        args.real_callees = bool(args.allow_stubs) and not args.state_snapshot
    elif args.real_callees:
        args.allow_stubs = True  # explicit --real-callees still implies stubs
    if args.rich_stub_returns:
        from stubs import set_accessor_stub_returns
        set_accessor_stub_returns(True)
        args.allow_stubs = True

    if args.from_halorec:
        # Convert a recorded frame into a state snapshot, then reuse the existing
        # --state-snapshot plumbing (load_snapshot maps regions into Unicorn).
        if args.state_snapshot:
            parser.error("--from-halorec and --state-snapshot are mutually exclusive")
        import json as _json
        import tempfile
        from halorec_to_snapshot import build_snapshot
        sel = args.halorec_frame
        fkw = {}
        if sel is None or sel in ("last", "first"):
            fkw["frame"] = sel
        elif sel.startswith("t="):
            fkw["t"] = float(sel[2:])
        elif sel.startswith("handle="):
            fkw["handle"] = int(sel[7:], 0)
        else:
            fkw["frame"] = sel
        snap, idx, t = build_snapshot(str(args.from_halorec), **fkw)
        tf = tempfile.NamedTemporaryFile("w", suffix=".json", prefix="halorec_snap_",
                                         delete=False, encoding="utf-8")
        _json.dump(snap, tf)
        tf.close()
        args.state_snapshot = Path(tf.name)
        print(f"  [from-halorec] {Path(str(args.from_halorec)).name} frame {idx} "
              f"(t={t:.3f}s) -> {len(snap['regions'])} regions -> {tf.name}")

    if args.batch_classify:
        sys.exit(_run_batch_classify())

    if args.list_funcs:
        sys.path.insert(0, str(_SCRIPT_DIR))
        from coff_loader import list_functions
        fns = list_functions(args.list_funcs)
        for f in fns:
            print(f)
        return 0

    if args.self_test:
        sys.exit(_run_self_test(verbose=args.verbose, oracle=args.oracle))

    if not args.func_name:
        parser.print_help()
        sys.exit(1)

    sys.exit(run_diff(
        args.func_name,
        num_seeds=args.seeds,
        base_seed=args.seed,
        verbose=args.verbose,
        save_log=True,
        output_json=args.output_json,
        record_leaf=not args.no_leaf_cache,
        z3_equiv=args.z3_equiv,
        allow_stubs=args.allow_stubs,
        float_tolerance_ulp=args.float_tolerance,
        float_tolerance_params=args.float_params.split(",") if args.float_params else None,
        skip_esp=args.skip_esp,
        quiet=args.quiet,
        mem_trace=args.mem_trace,
        state_snapshot=args.state_snapshot,
        no_concolic=args.no_concolic,
        real_callees=args.real_callees,
        max_insn=args.max_insn,
        stub_arg_trace=not args.no_stub_arg_trace,
        stub_conv_check=not args.no_stub_conv_check,
        value_corpus=args.value_corpus,
        oracle=args.oracle,
        oracle_native_callees=args.oracle_native_callees,
    ))


if __name__ == "__main__":
    main()
