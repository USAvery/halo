#!/usr/bin/env python3
"""Pins the data-execution guard's recovery rule.

WHY
---
`hook_code`'s data-exec guard fires when emulation starts executing inside a
page the harness auto-mapped for DATA, which means an indirect call went
through synthetic state.  Its recovery is "EAX=0, pop the return address,
continue", which is exactly right when a CALL got there: the CPU really did
push a return address, so the top of the stack really is one, and pretending
the bogus callee returned 0 is sound and symmetric across both sides.

It is wrong when a RET got there.  A RET has already consumed the return
address, so popping again takes whatever is underneath -- typically a saved
register or the address of a local, i.e. a STACK address.  Jumping to a stack
address means decoding that address's own bytes as instructions, so whether the
run faults or stalls harmlessly is decided by the numeric value of STACK_BASE.

That is not a hypothetical.  `game_state_memory_pool_new` RETs to 0x00000000
because a sibling call's delinked relocation resolves to nothing; it scored
40/40 PASS at 60% coverage with STACK_BASE=0x00100000 and ERROR with
STACK_BASE=0x08000000, on identical code.  The verdict was a coin flip on an
address constant.

So the guard now refuses a popped target inside the stack and reports the
escape.  These tests pin that, plus the stack-address predicate it relies on.

Run:  python3 tools/equivalence/test_escape_guard.py
"""
import re
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import memmap


class TestGuardSourceShape(unittest.TestCase):
    """Source-level pins.  The guard lives inside a closure in `_run_function`
    and needs a live Unicorn instance plus a delinked object to exercise end to
    end, which this suite deliberately does not require -- `regression_test.py`
    covers that path.  What must not regress silently is the rule itself."""

    @classmethod
    def setUpClass(cls):
        cls.src = (_HERE / "unicorn_diff.py").read_text(encoding="utf-8")

    def test_the_guard_declines_stack_targets(self):
        self.assertIn("if STACK_BASE <= _ret < STACK_TOP:", self.src,
                      "the data-exec guard no longer range-checks the popped "
                      "return target against the stack")

    def test_declining_stops_emulation_rather_than_jumping(self):
        block = self._guard_block()
        decline = block[block.index("if STACK_BASE <= _ret < STACK_TOP:"):]
        decline = decline[:decline.index("uc.reg_write(_EAX_G, 0)")]
        self.assertIn("uc.emu_stop()", decline)
        self.assertIn("escape_reason[0] =", decline,
                      "a declined recovery must record a reason, or the seed "
                      "reads as a clean return")

    def test_the_reason_is_folded_into_err_msg(self):
        """emu_stop() does not raise, so without this the seed would be scored
        as a pass with whatever registers were left behind."""
        self.assertIn("if err_msg is None and escape_reason[0]:", self.src)
        self.assertRegex(
            self.src,
            r"if err_msg is None and escape_reason\[0\]:\s*\n\s*err_msg = escape_reason\[0\]")

    def test_the_call_case_still_recovers(self):
        """The guard must keep recovering from a genuine CALL-to-garbage: that
        is what it was added for, and two 100%-coverage regression targets pin
        it."""
        block = self._guard_block()
        self.assertIn("uc.reg_write(_EAX_G, 0)", block)
        self.assertIn("uc.reg_write(_ESP_G, _esp + 4)", block)
        self.assertIn("uc.reg_write(_EIP_G, _ret)", block)

    def test_escape_reason_is_reset_per_run(self):
        """It is a per-`_run_function` cell, not module state: a stale reason
        would poison the next seed's verdict."""
        self.assertIn("escape_reason = [None]", self.src)
        self.assertEqual(self.src.count("escape_reason = [None]"), 1)

    def _guard_block(self):
        start = self.src.index("if (_data_exec_guard and")
        end = self.src.index("if verbose and address in stub_addrs", start)
        return self.src[start:end]


class TestStackPredicate(unittest.TestCase):
    def test_the_stack_range_is_non_empty_and_from_memmap(self):
        self.assertLess(memmap.STACK_BASE, memmap.STACK_TOP)
        self.assertEqual(memmap.STACK_TOP,
                         memmap.STACK_BASE + memmap.STACK_SIZE)

    def test_no_legitimate_code_region_is_inside_the_stack(self):
        """The refusal must not be able to reject a real code address."""
        for name, base, size in memmap.REGIONS:
            if name == "stack":
                continue
            with self.subTest(region=name):
                self.assertFalse(
                    memmap._overlaps(base, size,
                                     memmap.STACK_BASE, memmap.STACK_SIZE))

    def test_the_stub_sentinel_arena_is_not_inside_the_stack(self):
        """Sentinels are legitimate jump targets; rejecting one would break
        every stubbed callee."""
        for i in range(64):
            sentinel = memmap.STUB_BASE + i * memmap.STUB_SLOT
            self.assertFalse(memmap.STACK_BASE <= sentinel < memmap.STACK_TOP)

    def test_the_image_span_is_not_inside_the_stack(self):
        """Under --oracle=xbe the oracle's own code is the image, so a return
        into it must never be refused."""
        lo, hi = memmap.image_span()
        self.assertFalse(memmap._overlaps(lo, hi - lo,
                                          memmap.STACK_BASE,
                                          memmap.STACK_SIZE))


class TestArtifactMarkerDiscipline(unittest.TestCase):
    """`regression_test.py` grew a `known_artifact` marker so an escape like
    this is neither counted as a pass nor allowed to redden the gate.  It must
    not become a way to quietly mute targets."""

    @classmethod
    def setUpClass(cls):
        import json

        cls.targets = json.loads(
            (_HERE / "regression_targets.json").read_text(encoding="utf-8")
        )["targets"]
        cls.src = (_HERE / "regression_test.py").read_text(encoding="utf-8")

    def test_every_marker_carries_a_written_reason(self):
        for t in self.targets:
            marker = t.get("known_artifact")
            if marker is None:
                continue
            with self.subTest(target=t["name"]):
                self.assertIsInstance(marker, str)
                self.assertGreater(len(marker), 80,
                                   "a marker must explain itself, not just "
                                   "assert that something is an artifact")

    def test_a_marked_target_that_passes_is_reported_as_a_failure(self):
        """Otherwise the marker would outlive the artifact and excuse a real
        regression on that target forever."""
        # The message is built from concatenated f-string literals, so match
        # the branch rather than the rendered text.
        self.assertRegex(
            self.src,
            r'if status == "pass":\s*\n\s*if artifact:(?:.|\n)*?failed \+= 1')
        self.assertIn("remove the marker", self.src)

    def test_markers_are_not_counted_as_passes(self):
        self.assertIn("artifacts += 1", self.src)
        self.assertNotIn("passed += 1\n            artifacts", self.src)

    def test_the_marker_count_is_reported(self):
        self.assertIn("known artifacts", self.src)

    def test_the_gate_still_fails_on_unmarked_failures(self):
        self.assertIn("return 1 if (failed > 0 or errors > 0) else 0", self.src)

    def test_the_marked_set_is_small_and_named(self):
        """A growing marked set means the harness is getting less useful, not
        that more targets are artifacts.  Fails loudly if it grows."""
        marked = sorted(t["name"] for t in self.targets if t.get("known_artifact"))
        self.assertEqual(marked, ["game_state_memory_pool_new"],
                         "the known_artifact set changed; if that is "
                         "deliberate, update this pin in the same commit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
