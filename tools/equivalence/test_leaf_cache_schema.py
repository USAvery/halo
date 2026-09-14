#!/usr/bin/env python3
"""Pins `leaf_cache.json`'s schema after the raw-XBE oracle migration.

WHY
---
This file is read by target selection (`populate_regression_targets.py`,
`llm_auto_lift.py`), by the stub budget, by the z3 re-validation worklist and
by the CI dashboard.  Three things about it were silently wrong before the
migration, and each is the kind of defect that reads as data rather than as a
bug:

1. **Two key forms.**  `--batch-classify` derived its key from a delinked
   symbol name (`FUN_00012000` -> `"0x00012000"`, zero-padded) while
   `_record_confidence` writes `hex(addr)` (unpadded).  6125 padded rows sat
   beside 1278 unpadded ones with 1223 addresses present in BOTH forms.
   `populate_regression_targets.py` looks each key up in a kb.json-derived
   index, whose addresses are unpadded, so every padded row was invisible to
   target selection -- and a measurement could never update the classified row
   for the same function.

2. **Cross-oracle measurements.**  `oracle_func_size` changed from a delinked
   slice length to a bounds-table length, so a pre-migration `coverage_pct`
   describes a different denominator than the number it would be compared
   against (hazard H8).  A row may therefore carry coverage only together with
   the `oracle` that measured it.

3. **Rows asserting nothing.**  Stripping an invalidated measurement from a
   junk key (the 0x3f800034 / 0xccccccda / 0x700804 keys
   `test_leaf_cache_key.py` documents) leaves `{}` behind, which looks like a
   function the sweep classified and says nothing at all.

Run:  python3 tools/equivalence/test_leaf_cache_schema.py
"""
import json
import re
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_HERE))

import xbe_image

CACHE = _HERE / "leaf_cache.json"
BOUNDS = _ROOT / "tools" / "verify" / "function_bounds.json"

_CLASSES = {"leaf", "data_only", "stubbable", "non_leaf"}


class TestTheMetaStamp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(CACHE.read_text(encoding="utf-8"))
        cls.meta = cls.data.get("_meta")

    def test_the_file_says_which_oracle_derived_it(self):
        """Without this the file is a pile of numbers with no provenance, and
        H8's whole point is that the two oracles do not measure the same
        thing."""
        self.assertIsInstance(self.meta, dict,
                              "leaf_cache.json has no _meta stamp; regenerate "
                              "with unicorn_diff.py --batch-classify")
        self.assertEqual(self.meta.get("oracle"), "xbe")
        self.assertEqual(self.meta.get("schema"), 2)

    def test_the_md5_agrees_with_the_pristine_image_and_the_bounds_table(self):
        """The load-bearing safety check of the whole migration: an oracle that
        ever read the PATCHED default.xbe would equal the candidate in every
        ported region and pass everything silently."""
        self.assertEqual(self.meta.get("xbe_md5"), xbe_image.PRISTINE_MD5)
        bmeta = json.loads(BOUNDS.read_text(encoding="utf-8")).get("_meta", {})
        self.assertEqual(self.meta.get("xbe_md5"), bmeta.get("xbe_md5"),
                         "leaf_cache.json and function_bounds.json disagree "
                         "about which XBE they describe")

    def test_the_counts_are_not_decorative(self):
        fn = [k for k in self.data if not k.startswith("_")]
        self.assertGreater(self.meta.get("classified", 0), 7000,
                           "a sweep that classified a few hundred functions "
                           "means it fell back to delinked/, which holds one "
                           "object")
        self.assertEqual(
            self.meta.get("classified", 0) + self.meta.get("carried_over", 0),
            len(fn),
            "_meta's classified + carried_over must account for every row")


class TestKeysAreCanonical(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(CACHE.read_text(encoding="utf-8"))

    def test_every_key_is_unpadded_lowercase_hex(self):
        bad = []
        for k in self.data:
            if k.startswith("_"):
                continue
            try:
                if hex(int(k, 16)) != k:
                    bad.append(k)
            except ValueError:
                bad.append(k)
        self.assertEqual(bad[:20], [],
                         f"{len(bad)} key(s) are not canonical `hex(addr)`; a "
                         f"zero-padded key is invisible to "
                         f"populate_regression_targets.py's kb index")

    def test_no_address_appears_in_two_forms(self):
        seen = {}
        for k in self.data:
            if k.startswith("_"):
                continue
            try:
                n = int(k, 16)
            except ValueError:
                continue
            seen.setdefault(n, []).append(k)
        dupes = {n: ks for n, ks in seen.items() if len(ks) > 1}
        self.assertEqual(dupes, {})

    def test_the_padded_form_is_actually_gone(self):
        """Pins the fold rather than trusting the count: the old sweep wrote
        6125 of these."""
        padded = [k for k in self.data if re.fullmatch(r"0x0[0-9a-f]{7}", k)]
        self.assertEqual(padded, [])


class TestEntriesAssertSomething(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(CACHE.read_text(encoding="utf-8"))
        cls.entries = {k: v for k, v in cls.data.items()
                       if not k.startswith("_")}

    def test_no_entry_is_empty(self):
        empty = sorted(k for k, v in self.entries.items()
                       if isinstance(v, dict) and not v)
        self.assertEqual(empty, [],
                         "an empty row looks like a classified function and "
                         "claims nothing; --batch-classify drops these")

    def test_every_entry_is_a_dict(self):
        """The legacy form was a bare class string."""
        for k, v in self.entries.items():
            with self.subTest(addr=k):
                self.assertIsInstance(v, dict)

    def test_classes_are_from_the_known_set(self):
        for k, v in self.entries.items():
            cls = v.get("class")
            if cls is None:
                continue
            with self.subTest(addr=k):
                self.assertIn(cls, _CLASSES)

    def test_a_measurement_names_the_oracle_that_made_it(self):
        """H8.  Coverage without an oracle is a number whose denominator is
        unknown, and `_record_confidence` treats a missing `oracle` as
        `delinked` -- so an un-stamped post-migration row would be compared
        best-of against the wrong baseline."""
        orphans = sorted(k for k, v in self.entries.items()
                         if ("coverage_pct" in v or "confidence" in v)
                         and "oracle" not in v)
        self.assertEqual(orphans[:20], [],
                         f"{len(orphans)} row(s) carry a measurement with no "
                         f"oracle stamp")


class TestTheZ3WorklistSurvives(unittest.TestCase):
    """`tools/audit/revalidate_z3_proofs.py` reads its worklist out of this
    file.  A sweep that replaced each row wholesale would delete the flags
    before anything re-validated them -- destroying the list rather than
    invalidating it, which is the one failure mode that cannot be undone by
    re-running a tool."""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(CACHE.read_text(encoding="utf-8"))
        cls.src = (_HERE / "unicorn_diff.py").read_text(encoding="utf-8")

    def test_the_flags_are_still_there(self):
        """49, not the 58 the sweep started from: `revalidate_z3_proofs.py
        --apply` stripped 9 under the strict lifter -- 3 disproven by a Z3
        counterexample (tea_encrypt, tea_decrypt, ui_widget_find_by_tag) and 6
        that could not be re-established because the gate now correctly
        declines on code containing calls (hazard H1). Both kinds lose the
        flag, since `z3_proven` asserts a proof HOLDS; only the first three
        were shown false."""
        n = sum(1 for k, v in self.data.items()
                if not k.startswith("_") and isinstance(v, dict)
                and v.get("z3_proven") is True)
        self.assertGreaterEqual(
            n, 49,
            "z3_proven flags disappeared from leaf_cache.json; if proofs were "
            "deliberately revoked, do it through "
            "tools/audit/revalidate_z3_proofs.py and update this floor in the "
            "same commit")

    def test_the_sweep_merges_field_wise_rather_than_replacing(self):
        start = self.src.index("def _run_batch_classify() -> int:")
        end = self.src.index("\n\ndef main():", start)
        code = "\n".join(ln for ln in self.src[start:end].splitlines()
                         if not ln.lstrip().startswith("#"))
        self.assertNotIn("existing.update(cache)", code,
                         "a wholesale update drops z3_proven")
        self.assertIn('_DERIVED = ("class", "dir32_count", "call_count", '
                      '"reason")', code,
                      "the sweep must clear exactly the fields it re-derives, "
                      "so a stale dir32_count cannot survive as a fresh one")


class TestTheSweepReadsTheBoundsTable(unittest.TestCase):
    """The sweep used to iterate `delinked/*.obj`, which is gitignored and
    holds ONE object on this tree -- 20 functions out of ~8000.  Every other
    row was whatever an older, better-stocked checkout left behind."""

    @classmethod
    def setUpClass(cls):
        cls.src = (_HERE / "unicorn_diff.py").read_text(encoding="utf-8")
        start = cls.src.index("def _run_batch_classify() -> int:")
        end = cls.src.index("\n\ndef main():", start)
        cls.block = cls.src[start:end]

    def test_it_no_longer_globs_delinked(self):
        self.assertNotIn("DELINKED_DIR.glob", self.block)

    def test_it_iterates_the_committed_bounds_table(self):
        self.assertIn("function_bounds.json", self.block)

    def test_it_refuses_an_unreliable_bound_instead_of_guessing(self):
        """A class derived from a run-time-computed extent, a `table_data`
        range or a `no_terminator` end would sit in the cache looking exactly
        like a reviewed one."""
        self.assertIn("_oracle_bound_unreliable(va)", self.block)
        self.assertIn("skipped[", self.block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
