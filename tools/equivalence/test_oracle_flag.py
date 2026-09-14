#!/usr/bin/env python3
"""End-to-end pins on `--oracle={delinked,xbe}`.

WHY
---
`test_raw_oracle_classify.py` pins the raw oracle's pieces in isolation.  This
suite runs the whole differential both ways on a real function and checks the
things only a full run can show: that the mode reaches the emitted JSON, that
the XBE oracle actually executes from the image at real VAs, and that a
delinked run is untouched by any of it.

The corpus is `game_state.obj`, the one whole-object delink left on this host
(`delinked/` is gitignored).  Cases needing it SKIP where it is absent; the
XBE-only cases do not need it at all, which is the entire point of the
migration.

Run:  .venv/bin/python tools/equivalence/test_oracle_flag.py
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for _p in (str(_HERE), str(_ROOT / "tools" / "verify"),
           str(_ROOT / "tools" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import unicorn_diff as ud                                   # noqa: E402

_UD = _HERE / "unicorn_diff.py"
_DELINKED = _ROOT / "delinked" / "game_state.obj"
_PY = _ROOT / ".venv" / "bin" / "python3"

# A pure leaf in game_state.obj: no calls and no absolute data references, so it
# needs neither stub sentinels nor the symmetric interception of step 6.  It is
# also `unicorn_diff.SELF_TEST_FUNC`.
LEAF = "game_state_dispose_from_old_map"
# 0x1bf760 is a lone RET.  The delinked export gives it 38 bytes by swallowing
# alignment padding plus two following import thunks, so it is the one target
# where the two oracles are KNOWN to disagree -- and the XBE is right.
OVERWIDE_IN_DELINKED = "FUN_001bf760"


def _run(target, oracle, seeds=10, extra=()):
    """Run one differential and return its result JSON."""
    py = str(_PY) if _PY.exists() else sys.executable
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "r.json"
        cmd = [py, str(_UD), target, f"--oracle={oracle}",
               "--seeds", str(seeds), "--seed", "1", "--quiet",
               "--no-leaf-cache", "--output-json", str(out), *extra]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              cwd=str(_ROOT), timeout=180)
        if not out.exists():
            raise AssertionError(
                f"no JSON from {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")
        return json.loads(out.read_text(encoding="utf-8"))


class TestTheModeReachesTheArtifact(unittest.TestCase):
    """Every verdict means something different under each oracle, so no
    downstream artifact may be ambiguous about which produced it."""

    def test_the_xbe_run_stamps_itself(self):
        self.assertEqual(_run(LEAF, "xbe")["oracle"], "xbe")

    def test_the_delinked_run_stamps_itself(self):
        if not _DELINKED.exists():
            self.skipTest("delinked/game_state.obj absent")
        self.assertEqual(_run(LEAF, "delinked")["oracle"], "delinked")

    def test_the_default_is_xbe(self):
        """Flipped in step 7 behind tools/equivalence/
        oracle_migration_expected_deltas.json: every ported function in
        game_state.obj under both oracles, five deltas, all improvements, no
        regressions.  `test_oracle_ab_parity.py` keeps that artifact and this
        default tied together."""
        py = str(_PY) if _PY.exists() else sys.executable
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "r.json"
            subprocess.run(
                [py, str(_UD), LEAF, "--seeds", "2", "--quiet",
                 "--no-leaf-cache", "--output-json", str(out)],
                capture_output=True, text=True, cwd=str(_ROOT), timeout=180)
            self.assertEqual(
                json.loads(out.read_text(encoding="utf-8"))["oracle"],
                "xbe")


class TestBothOraclesAgreeOnTheLeaf(unittest.TestCase):
    def test_the_leaf_passes_under_the_xbe_oracle(self):
        r = _run(LEAF, "xbe")
        self.assertEqual(r["status"], "pass", r.get("reason"))
        self.assertEqual(r["failed"], 0)
        self.assertEqual(r["errors"], 0)
        self.assertEqual(r["coverage_pct"], 100.0)

    def test_the_leaf_passes_identically_under_the_delinked_oracle(self):
        if not _DELINKED.exists():
            self.skipTest("delinked/game_state.obj absent")
        d, x = _run(LEAF, "delinked"), _run(LEAF, "xbe")
        for key in ("status", "passed", "failed", "errors", "seeds",
                    "coverage_pct", "confidence"):
            with self.subTest(key=key):
                self.assertEqual(d.get(key), x.get(key))


class TestTheXbeBoundIsTheMoreAccurateOne(unittest.TestCase):
    """The migration is not only about deleting relocation synthesis: a
    per-function bound from the committed table is also narrower than a
    whole-object export's."""

    def test_a_lone_ret_is_38_bytes_delinked_and_1_byte_in_the_xbe(self):
        if not _DELINKED.exists():
            self.skipTest("delinked/game_state.obj absent")
        d = _run(OVERWIDE_IN_DELINKED, "delinked")
        x = _run(OVERWIDE_IN_DELINKED, "xbe")
        self.assertEqual(d["func_size"], 38)
        self.assertEqual(x["func_size"], 1)
        # The over-wide delinked slice reports 2.6% coverage and is discarded as
        # vacuous; the correct bound is fully covered.
        self.assertLess(d["coverage_pct"], 10.0)
        self.assertEqual(x["coverage_pct"], 100.0)
        self.assertEqual(d["status"], "inconclusive")
        self.assertEqual(x["status"], "pass")


class TestAnUnusableBoundIsRefused(unittest.TestCase):
    """H2's gate, end to end: missing evidence must not become a verdict."""

    # 0x84520 is recorded in the committed table as `table_data` -- the range is
    # data, not a function body -- and it has a built candidate, so the run
    # reaches the bound gate rather than dying earlier on a missing symbol.
    TABLE_DATA = 0x84520

    def test_a_table_data_range_is_not_applicable(self):
        import xbe_reference
        entry = xbe_reference.bounds_entry(self.TABLE_DATA)
        if entry is None or entry.get("kind") != "table_data":
            self.skipTest(f"{self.TABLE_DATA:#x} is no longer table_data; "
                          f"pick another entry or drop this pin")
        r = _run(hex(self.TABLE_DATA), "xbe", seeds=2)
        self.assertEqual(r["status"], "not_applicable", r.get("reason"))
        self.assertEqual(r["reason"], "oracle_bound_unreliable")
        self.assertEqual(r["oracle_bound_kind"], "table_data")

    def test_the_same_target_is_not_silently_passed(self):
        """The failure mode this guards against is a confident verdict over
        bytes that are not the function -- which is what the delinked lane's
        one-byte-slice case produced at 100% coverage."""
        r = _run(hex(self.TABLE_DATA), "xbe", seeds=2)
        self.assertNotEqual(r["status"], "pass")
        self.assertEqual(r["passed"], 0)


class TestSeedAndSnapshotHazards(unittest.TestCase):
    """H11 and H12 are both "do not write over the image".  Neither is
    observable from a verdict, so they are pinned at the source."""

    @classmethod
    def setUpClass(cls):
        cls.src = (_UD).read_text(encoding="utf-8")

    def test_in_image_globals_seeds_are_withheld_from_the_oracle(self):
        """H12.  One dict of absolute addresses was passed to both sides; a
        slot value or a cross-build known-globals capture written at an
        in-image address would replace the real load-time value."""
        self.assertIn("orc_globals_seeds = lft_globals_seeds = globals_seeds",
                      self.src)
        self.assertIn("globals_seeds=orc_globals_seeds", self.src)
        self.assertIn("globals_seeds=lft_globals_seeds", self.src)
        self.assertEqual(self.src.count("globals_seeds=globals_seeds"), 0,
                         "a _run_function call still shares one seed dict "
                         "between the two sides")

    def test_a_snapshot_never_zero_fills_an_image_page(self):
        """H11.  The override loop maps, ZERO-FILLS and seeds each page it
        touches.  On an image page that would discard ground truth before the
        snapshot write lands."""
        self.assertRegex(
            self.src,
            r"if page in _image_pages:(?:.|\n)*?_override_mapped\.add\(page\)"
            r"\s*\n\s*continue")

    def test_the_pristine_image_is_asserted_before_use(self):
        """Loading halo-patched/default.xbe would make oracle == candidate in
        every region we have already ported, and pass everything."""
        self.assertIn("xbe_image.assert_pristine()", self.src)


class TestLeafCacheIsOracleScoped(unittest.TestCase):
    """H8.  `func_size` changes from a delinked slice length to a bounds-table
    length, so coverage shifts for every target.  `_record_confidence` keeps the
    BETTER of old and new, which across oracles would let an inflated
    pre-migration number permanently shadow an honest post-migration one -- and
    nothing in the file said which oracle produced any row."""

    def setUp(self):
        self._real = ud._LEAF_CACHE_PATH
        self._tmp = tempfile.TemporaryDirectory()
        ud._LEAF_CACHE_PATH = Path(self._tmp.name) / "leaf_cache.json"

    def tearDown(self):
        ud._LEAF_CACHE_PATH = self._real
        self._tmp.cleanup()

    def _read(self):
        return json.loads(ud._LEAF_CACHE_PATH.read_text(encoding="utf-8"))

    def test_a_row_records_which_oracle_measured_it(self):
        ud._record_confidence("0x1000", "high", 90.0, oracle="xbe")
        self.assertEqual(self._read()["0x1000"]["oracle"], "xbe")

    def test_best_of_still_applies_within_one_oracle(self):
        ud._record_confidence("0x1000", "high", 90.0, oracle="delinked")
        ud._record_confidence("0x1000", "weak", 20.0, oracle="delinked")
        row = self._read()["0x1000"]
        self.assertEqual(row["coverage_pct"], 90.0,
                         "a re-measurement under the same oracle must not "
                         "downgrade a recorded entry")

    def test_a_different_oracle_replaces_rather_than_competes(self):
        ud._record_confidence("0x1000", "high", 90.0, oracle="delinked")
        ud._record_confidence("0x1000", "weak", 20.0, oracle="xbe")
        row = self._read()["0x1000"]
        self.assertEqual(row["coverage_pct"], 20.0,
                         "an XBE measurement must replace a delinked one, not "
                         "lose a best-of comparison to it")
        self.assertEqual(row["oracle"], "xbe")

    def test_an_unstamped_row_counts_as_delinked(self):
        """7397 existing rows predate the flag."""
        ud._LEAF_CACHE_PATH.write_text(json.dumps(
            {"0x1000": {"class": "leaf", "confidence": "high",
                        "coverage_pct": 90.0}}), encoding="utf-8")
        ud._record_confidence("0x1000", "weak", 20.0, oracle="delinked")
        self.assertEqual(self._read()["0x1000"]["coverage_pct"], 90.0)
        ud._record_confidence("0x1000", "weak", 20.0, oracle="xbe")
        self.assertEqual(self._read()["0x1000"]["coverage_pct"], 20.0)

    def test_the_run_threads_its_mode_into_the_write(self):
        src = (_UD).read_text(encoding="utf-8")
        self.assertRegex(
            src,
            r"_record_confidence\(target_addr, confidence, "
            r"round\(coverage_pct, 1\),\s*\n\s*oracle=oracle\)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
