#!/usr/bin/env python3
"""Pins the raw-XBE oracle's symmetric interception (hazard H9).

WHY
---
A delinked COFF has a relocation per call, so `patch_rel32_calls` can rewrite
each call SITE to a sentinel and the oracle's callee set is whatever the
harness says it is.  Raw image bytes have no relocations: every call is a
finished E8 with a correct displacement into real code, and the whole engine
is mapped.  Left alone the oracle therefore executes the engine natively while
the candidate hits return-0 trampolines -- comparing "lift" against "lift plus
engine", which makes every verdict a false divergence, and in practice just
trips the H5 escape guard.

So the patch goes at the CALLEE's real entry VA instead: `E9 <rel32>` to the
sentinel the candidate's call site was already patched to.  That catches every
route into the callee -- from the target body, from a sibling the oracle also
runs, direct or tail -- which rewriting call sites could not, since there is no
list naming them and a sibling's sites are not in the target's bytes at all.

MEASURED
--------
Before interception, four of the 20 game_state.obj targets reached the seed
loop under --oracle=xbe and errored on every seed with `oracle_escaped`.  After
it, all four pass:

    FUN_001bfb60        error 0/20  ->  pass  100.0%  high
    game_state_malloc   error 0/20  ->  pass   69.9%  high
    game_state_data_new error 0/20  ->  pass  100.0%  high
    FUN_001c00c0        error 0/20  ->  pass   48.0%  moderate

The --oracle=delinked column is unchanged across all 23 exports.

Run:  .venv/bin/python tools/equivalence/test_oracle_intercept.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_ROOT / "tools" / "verify"))

import unicorn_diff as ud

# A target with three external calls whose oracle used to escape on every
# seed.  Chosen for being the cheapest of the four to run.
INTERCEPTED = "FUN_001bfb60"

_PY = _ROOT / ".venv" / "bin" / "python"
if not _PY.exists():
    _PY = Path(sys.executable)


def _run(name, *flags, seeds=5):
    fd, jpath = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        proc = subprocess.run(
            [str(_PY), str(_HERE / "unicorn_diff.py"), name,
             "--allow-stubs", "--seeds", str(seeds), "--seed", "1",
             "--output-json", jpath, *flags],
            capture_output=True, text=True, timeout=600, cwd=str(_ROOT))
        try:
            payload = json.loads(Path(jpath).read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        return payload, proc.stdout + proc.stderr
    finally:
        os.unlink(jpath)


class TestInterceptionReplacesTheEscape(unittest.TestCase):
    """End to end.  This is the whole point of the step, so it runs the real
    differential rather than inspecting source."""

    @classmethod
    def setUpClass(cls):
        if not (_ROOT / "halo-patched" / "cachebeta.xbe").exists():
            raise unittest.SkipTest("no pristine XBE on this host")
        cls.payload, cls.out = _run(INTERCEPTED, "--oracle=xbe")

    def test_the_target_has_calls_to_intercept(self):
        """Guards against the test passing because the oracle turned out to be
        a leaf and nothing was exercised."""
        self.assertRegex(self.out,
                         r"oracle interception: [1-9]\d* callee VA\(s\) patched")

    def test_the_oracle_no_longer_escapes(self):
        self.assertNotIn("oracle_escaped", self.out,
                         "the oracle left its body for something that is "
                         "neither the stub arena, an interception JMP, nor a "
                         "declared native range")

    def test_it_produces_a_verdict(self):
        self.assertEqual(self.payload.get("status"), "pass", self.out[-2000:])
        self.assertEqual(self.payload.get("errors"), 0)
        self.assertGreater(self.payload.get("coverage_pct", 0), 50.0)

    def test_the_verdict_is_stamped_with_the_oracle(self):
        self.assertEqual(self.payload.get("oracle"), "xbe")


class TestTheDelinkedLaneIsUntouched(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (_ROOT / "delinked" / "game_state.obj").exists():
            raise unittest.SkipTest("no delinked game_state.obj on this host")
        cls.payload, cls.out = _run(INTERCEPTED, "--oracle=delinked")

    def test_no_interception_map_is_built(self):
        """The delinked oracle intercepts by rewriting its own relocated call
        sites; a callee-VA patch there would be a second, conflicting
        mechanism."""
        self.assertNotIn("oracle interception:", self.out)

    def test_it_still_passes(self):
        self.assertEqual(self.payload.get("status"), "pass", self.out[-2000:])
        self.assertEqual(self.payload.get("oracle"), "delinked")


class TestTheDerivationIsSingleSourced(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (_HERE / "unicorn_diff.py").read_text(encoding="utf-8")
        cls.block = cls.src[cls.src.index("oracle_intercept_map = {}\n        if _oracle_xbe"):
                            cls.src.index("combined_stub_map = dict(orc_stub_map)")]

    def test_targets_come_from_the_classifier_not_a_second_scan(self):
        """`_classify_raw_oracle` already documents at length which operand
        forms are a call and which are a size constant.  A second disassembly
        pass here would be a copy of those rules, free to drift from them."""
        self.assertIn("orc_cls.external_symbols", self.block)
        self.assertNotIn("capstone", self.block,
                         "the intercept map is re-disassembling the body "
                         "instead of reusing the classifier's findings")

    def test_it_is_gated_on_the_xbe_oracle(self):
        self.assertIn("if _oracle_xbe", self.block)

    def test_a_shared_callee_reuses_the_candidates_sentinel(self):
        """Symbol identity is what pairs the two call sequences up for the
        stub-arg differential; a fresh sentinel per side would desynchronise
        it from the first shared call onward."""
        self.assertIn("shared_stub_sentinels.get(_key)", self.block)

    def test_an_oracle_only_callee_is_still_intercepted(self):
        """Not run natively: the point is symmetry.  An oracle-only call
        becomes a one-sided stub record, which the differential already knows
        how to report, rather than an escape."""
        self.assertIn("_SB + len(shared_stub_sentinels) * _SS", self.block)

    def test_the_name_is_canonicalised_through_kb(self):
        self.assertIn('_canonicalize_callee_key("FUN_%08x" % _cva)', self.block)


class TestThePatchIsWrittenSafely(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = (_HERE / "unicorn_diff.py").read_text(encoding="utf-8")
        # The loop moved out of the `entry_va is not None` branch in step 7:
        # since the data image is shared, the CANDIDATE can reach image code
        # too (through an identity-relocated pointer), so it needs the same
        # JMPs.  It now sits after the entry-point decision and keys off
        # `entry_point`, which is the oracle's real VA or CODE_BASE.
        cls.block = src[src.index("    for _cva, _sent in (intercept_vas or {}"):
                        src.index("    # Pre-map pages for indirect call")]
        cls.src = src

    def test_it_runs_for_both_instances(self):
        """A JMP in the oracle's copy of the image and not the candidate's
        would make the two sides call different things: the candidate would
        run the real callee natively while the oracle hit a stub.  That was
        `game_state_save`, escaping to 0x1bf760 on the LIFTED side after the
        oracle side was fixed."""
        self.assertNotIn("if entry_va is not None", self.block)
        for site in ("intercept_vas=oracle_intercept_map",):
            self.assertGreaterEqual(self.src.count(site), 3,
                                    "the intercept map must reach the "
                                    "candidate's _run_function calls too")

    def test_it_never_patches_over_the_code_under_test(self):
        self.assertIn("if entry_point <= _cva < entry_point + len(code):",
                      self.block)

    def test_it_never_patches_outside_the_mapped_image(self):
        self.assertIn("if not (_image_lo <= _cva < _image_hi - 5):", self.block)

    def test_it_writes_a_five_byte_near_jump(self):
        self.assertIn('b"\\xe9" + _rel.to_bytes(4, "little")', self.block)

    def test_a_failed_write_is_not_recorded_as_a_site(self):
        """A site in `_intercept_sites` is a hole in the H5 escape guard.  One
        that was never written is a hole with no JMP behind it, so a genuine
        escape to that address would read as interception."""
        self.assertIn("except unicorn.UcError:\n            continue",
                      self.block)
        self.assertLess(self.block.index("except unicorn.UcError"),
                        self.block.index("_intercept_sites[_cva] = _sent"))


class TestTheEscapeGuardAllowsTheArenaNotTheSentinels(unittest.TestCase):
    """A sentinel address is only the FIRST instruction of what runs there.
    Allowing exact addresses fired the guard on the second byte of the first
    trampoline the oracle jumped to (eip=0x40000002) -- reporting an escape
    for the interception working."""

    @classmethod
    def setUpClass(cls):
        cls.src = (_HERE / "unicorn_diff.py").read_text(encoding="utf-8")

    def test_the_guard_range_checks_the_arena(self):
        self.assertIn("if (not (_stub_lo <= address < _stub_hi)", self.src)

    def test_the_arena_bounds_match_the_mapping(self):
        """Two expressions for one range would drift; the guard must compute
        exactly what the arena mem_map computes."""
        self.assertIn("_stub_lo = min(stub_addrs) & ~0xFFFF", self.src)
        self.assertIn("_stub_hi = (max(stub_addrs) & ~0xFFFF) + 0x20000",
                      self.src)
        self.assertIn("stub_page_base = min(stub_addrs) & ~0xFFFF", self.src)
        self.assertIn("stub_page_end = (max(stub_addrs) & ~0xFFFF) + 0x20000",
                      self.src)

    def test_the_interception_sites_are_in_the_allowed_set(self):
        self.assertIn("and address not in _intercept_sites", self.src)

    def test_an_empty_arena_does_not_allow_address_zero(self):
        """`_stub_lo == _stub_hi == 0` when there are no stubs: the range must
        be empty, not `[0, 0]` inclusive, or a jump to NULL would be excused."""
        self.assertIn("_stub_lo = _stub_hi = 0", self.src)
        self.assertIn("_stub_lo <= address < _stub_hi", self.src)


class TestNativeCalleesAreOptInAndBounded(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (_HERE / "unicorn_diff.py").read_text(encoding="utf-8")
        cls.block = cls.src[cls.src.index("if oracle_native_callees and oracle_intercept_map:"):
                            cls.src.index("if stub_mgr.convention_mismatches:")]

    def test_the_flag_is_off_by_default(self):
        self.assertIn("oracle_native_callees: bool = False", self.src)
        self.assertIn('parser.add_argument("--oracle-native-callees", '
                      'action="store_true"', self.src)

    def test_it_only_frees_a_callee_the_candidate_also_runs_natively(self):
        """Otherwise it becomes a switch for the exact asymmetry H9 exists to
        prevent: real engine code on one side, a trampoline on the other."""
        self.assertIn("not _stub.has_real_code", self.block)

    def test_an_unbounded_callee_stays_patched(self):
        """A guessed range widens the H5 guard's allowed set, which turns a
        missed escape into a silent run through unrelated engine code."""
        self.assertIn("_xbe_function_extent(_cva)", self.block)
        self.assertIn("if _ext is None:", self.block)

    def test_the_ranges_survive_to_the_run(self):
        """`oracle_native_ranges` is set inside the stub block and read by the
        oracle's _run_function calls, so a later re-initialisation would
        silently discard it -- which is what a plain `= None` after the block
        used to do."""
        self.assertEqual(self.src.count("oracle_native_ranges = None"), 1)
        self.assertLess(self.src.index("oracle_native_ranges = None"),
                        self.src.index("if oracle_native_callees"))


class TestTheExtentHelperRefusesToGuess(unittest.TestCase):
    def test_a_bounded_function_resolves(self):
        ext = ud._xbe_function_extent(0x1BFB60)
        self.assertIsNotNone(ext)
        lo, hi = ext
        self.assertEqual(lo, 0x1BFB60)
        self.assertGreater(hi, lo)

    def test_an_unbounded_address_returns_none(self):
        self.assertIsNone(ud._xbe_function_extent(0x1BFB61),
                          "a mid-function address has no bounds entry and must "
                          "not be given an invented range")


if __name__ == "__main__":
    unittest.main(verbosity=2)
