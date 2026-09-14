#!/usr/bin/env python3
"""Pins on the harness's Unicorn address-space map (`memmap.py`).

Three classes of invariant, each of which was violated at some point by the
three independent copies of these constants that memmap replaced:

  1. The regions do not overlap each other.  ``STUB_OBJECT_ARENA`` was
     numerically equal to ``SCRATCH_BASE``, so a pointer-returning stub handed
     back the pointer-argument buffer.
  2. No region lands inside the pristine XBE's image span.  ``STACK_BASE``,
     ``CODE_BASE`` and ``GLOBALS_BASE`` all did (.text, .data, .data), which is
     what blocked mapping the image at real VAs in the first place.
  3. Every consumer agrees with memmap.  ``stubs.py`` re-hardcoded the stack
     bounds and ``abi.py`` re-hardcoded the scratch bounds, with nothing
     checking that the copies matched.

Run:  python3 tools/equivalence/test_memmap.py
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import memmap


def _span(name):
    base, size = next((b, s) for n, b, s in memmap.REGIONS if n == name)
    return base, base + size


class TestRegionsAreDisjoint(unittest.TestCase):
    def test_every_pair_is_disjoint(self):
        for i, (n1, b1, s1) in enumerate(memmap.REGIONS):
            for n2, b2, s2 in memmap.REGIONS[i + 1:]:
                self.assertFalse(
                    memmap._overlaps(b1, s1, b2, s2),
                    f"{n1} [{b1:#x}+{s1:#x}) overlaps {n2} [{b2:#x}+{s2:#x})")

    def test_every_region_has_a_positive_size(self):
        for name, _base, size in memmap.REGIONS:
            self.assertGreater(size, 0, name)

    def test_the_stub_arena_no_longer_aliases_the_scratch_buffer(self):
        """The specific bug: a stub's return pointer == the argument buffer.

        `state.capture` reads SCRATCH_BASE..+SCRATCH_SIZE back and compares it
        byte-for-byte, so with the alias in place a target that called
        debug_malloc and wrote through the result was writing into compared
        argument memory -- while arena pages 1..6 sat outside that window and
        were not compared.  Page 0 was special by accident.
        """
        self.assertNotEqual(memmap.STUB_OBJECT_ARENA, memmap.SCRATCH_BASE)
        a_lo, a_hi = _span("stub_arena")
        s_lo, s_hi = _span("scratch")
        self.assertFalse(a_lo < s_hi and s_lo < a_hi)

    def test_the_arena_covers_every_page_the_stub_tables_use(self):
        """ARENA_SIZE must cover the highest page any stub table hands out."""
        import stubs

        highest = max(stubs.ACCESSOR_STUB_RETURNS.values())
        highest = max(highest, stubs.DEFAULT_STUB_RETURNS["debug_malloc"])
        lo, hi = _span("stub_arena")
        self.assertGreaterEqual(highest, lo)
        self.assertLess(highest + memmap.ARENA_PAGE, hi + 1)


class TestNothingLandsInTheImage(unittest.TestCase):
    """The reason the layout moved at all."""

    @classmethod
    def setUpClass(cls):
        cls.lo, cls.hi = memmap.image_span()

    def test_the_span_is_the_pristine_image(self):
        # Sanity: a wrong XBE would make every other assertion here vacuous.
        self.assertLessEqual(self.lo, 0x12000)       # .text start
        self.assertGreater(self.hi, 0x632dd8)        # .data end

    def test_no_region_intersects_the_image(self):
        for name, base, size in memmap.REGIONS:
            self.assertFalse(
                memmap._overlaps(base, size, self.lo, self.hi - self.lo),
                f"{name} [{base:#x}+{size:#x}) is inside the image span "
                f"[{self.lo:#x}..{self.hi:#x})")

    def test_the_old_bases_really_were_inside_the_image(self):
        """Guards the premise: if these are not inside, the move was pointless
        and someone has changed `image_span`."""
        for old in (0x00100000, 0x00400000, 0x00500000):
            self.assertTrue(self.lo <= old < self.hi,
                            f"{old:#x} should be inside the image span")

    def test_sentinels_are_rel32_reachable_from_both_code_sources(self):
        """CALL targets are patched as rel32, and under the raw-XBE oracle the
        calling code is the image itself, not CODE_BASE."""
        for label, src in (("CODE_BASE", memmap.CODE_BASE),
                           ("image lo", self.lo), ("image hi", self.hi)):
            with self.subTest(src=label):
                self.assertLessEqual(abs(memmap.STUB_BASE - src),
                                     memmap._REL32_MAX)


class TestHeapLine(unittest.TestCase):
    """`hook_mem_write` drops writes at or above HEAP_LINE unless they land in
    a snapshot region, and `_heap_compare` keys its output evidence on the same
    threshold.  A harness region above the line is a region --mem-trace cannot
    see."""

    def test_stack_code_and_globals_stay_below_the_line(self):
        for name in memmap.BELOW_HEAP_LINE:
            _lo, hi = _span(name)
            self.assertLessEqual(hi, memmap.HEAP_LINE, name)

    def test_the_globals_arena_has_room_to_grow_before_scratch(self):
        """The candidate's slot arena starts at
        GLOBALS_BASE + len(orc_data_slots)*256 and may run past
        GLOBALS_BASE+GLOBALS_SIZE, so the next region must not be adjacent."""
        _lo, g_hi = _span("globals")
        self.assertGreaterEqual(memmap.SCRATCH_BASE - g_hi, 0x01000000)

    def test_fxsave_is_at_or_above_the_line(self):
        """So the instrumentation's own 512-byte store is dropped rather than
        compared as program data."""
        self.assertGreaterEqual(memmap.FXSAVE_BASE, memmap.HEAP_LINE)

    def test_the_tramp_page_is_below_the_line_and_outside_code(self):
        t_lo, t_hi = _span("tramp")
        self.assertLess(t_hi, memmap.HEAP_LINE)
        c_lo, c_hi = _span("code")
        self.assertFalse(t_lo < c_hi and c_lo < t_hi)


class TestConsumersAgree(unittest.TestCase):
    def test_stubs_stack_bounds(self):
        import stubs

        self.assertEqual(stubs._STACK_BASE, memmap.STACK_BASE)
        self.assertEqual(stubs._STACK_TOP, memmap.STACK_TOP)

    def test_stubs_globals_and_sentinels(self):
        import stubs

        self.assertEqual(stubs.GLOBALS_BASE, memmap.GLOBALS_BASE)
        self.assertEqual(stubs.GLOBALS_SIZE, memmap.GLOBALS_SIZE)
        self.assertEqual(stubs.STUB_BASE, memmap.STUB_BASE)
        self.assertEqual(stubs.STUB_SLOT, memmap.STUB_SLOT)
        self.assertEqual(stubs.STUB_OBJECT_ARENA, memmap.STUB_OBJECT_ARENA)
        self.assertEqual(stubs._ARENA_PAGE, memmap.ARENA_PAGE)

    def test_abi_scratch_bounds(self):
        import abi

        self.assertEqual(abi.SCRATCH_BASE, memmap.SCRATCH_BASE)
        self.assertEqual(abi.SCRATCH_SIZE, memmap.SCRATCH_SIZE)

    def test_unicorn_diff_layout(self):
        import unicorn_diff as ud

        for name in ("CODE_BASE", "CODE_SIZE", "STACK_BASE", "STACK_SIZE",
                     "STACK_TOP", "SCRATCH_BASE", "SCRATCH_SIZE",
                     "TRAMP_BASE", "FXSAVE_BASE", "HEAP_LINE",
                     "FAKE_RET_ADDR"):
            with self.subTest(name=name):
                self.assertEqual(getattr(ud, name), getattr(memmap, name))

    def test_no_module_redeclares_a_base_address(self):
        """Catches a future copy being pasted back in.  The old values are the
        ones that matter: any of them reappearing as a literal in these modules
        means the layout has forked again."""
        stale = ("0x00100000", "0x00400000", "0x00500000", "0x00200000",
                 "0x20000000")
        for fname in ("unicorn_diff.py", "stubs.py", "abi.py", "state.py"):
            text = (_HERE / fname).read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                code = line.split("#", 1)[0]
                for lit in stale:
                    self.assertNotIn(
                        lit, code,
                        f"{fname}:{lineno} re-declares {lit}: {line.strip()}")

    def test_patch_rel32_calls_defaults_to_the_real_code_base(self):
        import stubs
        import inspect

        sig = inspect.signature(stubs.patch_rel32_calls)
        self.assertEqual(sig.parameters["code_base"].default, memmap.CODE_BASE)

    def test_patch_dir32_relocs_defaults_to_the_real_globals_base(self):
        import stubs
        import inspect

        sig = inspect.signature(stubs.patch_dir32_relocs)
        self.assertEqual(sig.parameters["globals_base"].default,
                         memmap.GLOBALS_BASE)


class TestSelfTestAgrees(unittest.TestCase):
    def test_module_self_test_passes(self):
        self.assertEqual(memmap._self_test(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
