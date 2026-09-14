#!/usr/bin/env python3
"""The equivalence harness's Unicorn address-space map, in one place.

WHY THIS MODULE EXISTS
----------------------
The layout used to be declared three times with no cross-check:
``unicorn_diff.py`` owned CODE/STACK/SCRATCH, ``stubs.py`` owned GLOBALS and
re-hardcoded the stack bounds as ``_STACK_BASE``/``_STACK_TOP``, and ``abi.py``
re-declared SCRATCH.  Nothing verified the copies agreed, and nothing verified
the regions did not overlap each other.  They in fact did: ``STUB_OBJECT_ARENA``
was numerically equal to ``SCRATCH_BASE`` (see below).

It matters more now than it used to.  The raw-XBE oracle maps the pristine
image at its REAL virtual addresses, and three of the old harness bases sit
inside that image:

    STACK_BASE    0x00100000  ->  inside .text   (0x012000..0x1e69cc)
    CODE_BASE     0x00400000  ->  inside .data   (0x2c84c0..0x632dd8)
    GLOBALS_BASE  0x00500000  ->  inside .data

Mapping the image would have collided with all three.  They are moved here,
once, with a self-test that fails if any future base wanders back in.

THE 0x20000000 LINE IS LOAD-BEARING
-----------------------------------
``unicorn_diff.hook_mem_write`` drops every write at or above ``HEAP_LINE``
unless it lands in a snapshot-mapped region, and ``_heap_compare`` keys its
"affirmative output evidence" on the same threshold.  So STACK, CODE and
GLOBALS must all stay strictly BELOW it: moving GLOBALS above the line would
make ``--mem-trace`` silently vacuous for the candidate instead of noisy.
That constraint is asserted in ``_self_test`` rather than left as a comment.

WHAT DID NOT MOVE, AND WHY
--------------------------
``SCRATCH_BASE`` stays at 0x10000000.  Pointer arguments are written there and
the raw pointer VALUE is compared between the two runs, so moving it would
change every pointer-returning function's EAX comparison for no reason.
``STUB_BASE`` stays at 0x40000000 and ``FXSAVE_BASE`` at 0x20000000.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ---------------------------------------------------------------------------
# Emulated regions
# ---------------------------------------------------------------------------

# Everything below this line is treated as harness-owned memory whose writes
# are recorded; at or above it, writes are dropped unless snapshot-mapped, and
# are counted as heap output evidence.  See module docstring.
HEAP_LINE      = 0x20000000

STACK_BASE     = 0x08000000   # was 0x00100000 -- landed inside .text
STACK_SIZE     = 0x00100000   # 1 MB
STACK_TOP      = STACK_BASE + STACK_SIZE

CODE_BASE      = 0x09000000   # was 0x00400000 -- landed inside .data
CODE_SIZE      = 0x00040000   # 256 KB; fits the largest .text section slice

GLOBALS_BASE   = 0x0A000000   # was 0x00500000 -- landed inside .data
GLOBALS_SIZE   = 0x00100000   # 1 MB
# The candidate's slot arena starts at GLOBALS_BASE + len(orc_data_slots)*256
# and is allowed to run past GLOBALS_BASE+GLOBALS_SIZE, so this region needs
# room to grow upward.  Nothing else is placed before SCRATCH_BASE, leaving
# 96 MB of headroom.

SCRATCH_BASE   = 0x10000000   # pointer-argument buffer -- DO NOT MOVE
SCRATCH_SIZE   = 0x00010000   # 64 KB

# Scratch arena handed back by pointer-returning stubs (debug_malloc, csmemcpy,
# and the --rich-stub-returns accessor table).
#
# This used to be 0x10000000, i.e. *exactly* SCRATCH_BASE, with a page stride
# exactly equal to SCRATCH_SIZE.  So a target that called debug_malloc and
# wrote through the result wrote into the pointer-argument buffer, which
# `state.capture` reads back and compares byte-for-byte -- while arena pages
# 1..6 (at +0x10000 and up) fell outside that 64 KB window and were compared
# only under --mem-trace.  Page 0 was special purely by accident.
#
# Giving the arena its own base makes all seven pages consistent: none of them
# is inside the captured scratch window, all of them are visible to
# --mem-trace, and a stub's return value can no longer alias an argument
# buffer.  Both sides are affected identically, so the differential stays
# sound.
STUB_OBJECT_ARENA = 0x11000000
ARENA_PAGE        = 0x10000
ARENA_PAGES       = 7         # page 0 (malloc/memcpy) + accessor pages 1..6
ARENA_SIZE        = ARENA_PAGES * ARENA_PAGE

# Instrumentation trampolines (the FXSAVE stub).  It used to be written at
# CODE_BASE + CODE_SIZE - 16, which only works while the oracle maps CODE_BASE
# at all; under the raw-XBE oracle it does not, and the write landed in a bare
# `except: pass` that made every float-returning function's ST0 comparison
# silently vacuous.  A page neither side owns removes that coupling.
TRAMP_BASE     = 0x18000000
TRAMP_SIZE     = 0x00001000

# FXSAVE destination.  Deliberately equal to HEAP_LINE: it is above the line so
# the instrumentation's own 512-byte store is dropped by hook_mem_write instead
# of showing up as a memory difference, and it is never inside a snapshot
# region, so the snapshot exemption does not re-admit it.
FXSAVE_BASE    = 0x20000000
FXSAVE_SIZE    = 0x00001000

# Callee sentinel arena.  Must stay within a signed rel32 displacement of both
# CODE_BASE (the candidate's code) and the XBE image's .text (the raw oracle's
# code), because CALL targets are patched as rel32.
STUB_BASE      = 0x40000000
STUB_SLOT      = 0x4000       # 16 KB per sentinel -- fits real callee code

FAKE_RET_ADDR  = 0xDEADC0DE   # termination sentinel pushed as the return addr

_REL32_MAX = 0x7FFFFFFF

#: (name, base, size) for every fixed-size region, for the disjointness check.
REGIONS = (
    ("stack",       STACK_BASE,        STACK_SIZE),
    ("code",        CODE_BASE,         CODE_SIZE),
    ("globals",     GLOBALS_BASE,      GLOBALS_SIZE),
    ("scratch",     SCRATCH_BASE,      SCRATCH_SIZE),
    ("stub_arena",  STUB_OBJECT_ARENA, ARENA_SIZE),
    ("tramp",       TRAMP_BASE,        TRAMP_SIZE),
    ("fxsave",      FXSAVE_BASE,       FXSAVE_SIZE),
)

#: Regions that must stay below HEAP_LINE to remain visible to --mem-trace.
BELOW_HEAP_LINE = ("stack", "code", "globals")


def image_span():
    """`(lo, hi)` of the pristine XBE image, page-aligned.

    Imported lazily and cached by `xbe_image` itself, so importing `memmap`
    does not force a 3.4 MB file read on callers that only want constants.
    """
    import xbe_image

    _raw, secs = xbe_image.load_xbe()
    return xbe_image.image_span(secs)


def _overlaps(a_base, a_size, b_base, b_size):
    return a_base < b_base + b_size and b_base < a_base + a_size


def _self_test():
    """Assert every invariant the layout is chosen to satisfy. -> exit code."""
    fail = []

    for i, (n1, b1, s1) in enumerate(REGIONS):
        if s1 <= 0:
            fail.append(f"{n1}: non-positive size {s1:#x}")
        for n2, b2, s2 in REGIONS[i + 1:]:
            if _overlaps(b1, s1, b2, s2):
                fail.append(f"{n1} [{b1:#x}+{s1:#x}) overlaps "
                            f"{n2} [{b2:#x}+{s2:#x})")

    for name in BELOW_HEAP_LINE:
        base, size = next((b, s) for n, b, s in REGIONS if n == name)
        if base + size > HEAP_LINE:
            fail.append(f"{name} ends at {base + size:#x}, at or above "
                        f"HEAP_LINE {HEAP_LINE:#x} -- hook_mem_write would "
                        f"drop its writes and --mem-trace would go vacuous")

    try:
        lo, hi = image_span()
    except Exception as exc:                      # pragma: no cover
        fail.append(f"could not compute the image span: {exc}")
    else:
        for name, base, size in REGIONS:
            if _overlaps(base, size, lo, hi - lo):
                fail.append(f"{name} [{base:#x}+{size:#x}) is inside the XBE "
                            f"image span [{lo:#x}..{hi:#x})")
        # Sentinels must be reachable by rel32 from both code sources.
        for label, src in (("CODE_BASE", CODE_BASE), ("image .text", lo),
                           ("image end", hi)):
            disp = abs(STUB_BASE - src)
            if disp > _REL32_MAX:
                fail.append(f"STUB_BASE {STUB_BASE:#x} is {disp:#x} from "
                            f"{label} {src:#x} -- beyond rel32 reach")
        if not (lo <= 0x12000 and hi > 0x632dd8):
            fail.append(f"image span [{lo:#x}..{hi:#x}) does not look like "
                        f"cachebeta.xbe -- wrong XBE loaded?")

    if FXSAVE_BASE < HEAP_LINE:
        fail.append(f"FXSAVE_BASE {FXSAVE_BASE:#x} is below HEAP_LINE, so the "
                    f"instrumentation's own store would be compared as data")

    if STUB_OBJECT_ARENA == SCRATCH_BASE:
        fail.append("STUB_OBJECT_ARENA aliases SCRATCH_BASE: a stub's return "
                    "pointer would overlap the compared argument buffer")

    if fail:
        for line in fail:
            print(f"FAIL {line}")
        return 1

    print(f"memmap self-test OK ({len(REGIONS)} regions, image span "
          f"{image_span()[0]:#x}..{image_span()[1]:#x})")
    return 0


if __name__ == "__main__":
    sys.exit(_self_test())
