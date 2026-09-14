#!/usr/bin/env python3
"""The go/no-go gate for flipping the equivalence oracle to the raw XBE.

WHY
---
Steps 1-6 of the raw-XBE oracle migration are each individually gated, but
"the new oracle is better" is not a claim any of those gates can make.  It is a
claim about VERDICTS, and it can only be checked by running both oracles over
the same corpus and reading the two columns side by side.

`oracle_migration_expected_deltas.json` is that reading, committed: every
ported function in `game_state.obj` -- the only object the delinked lane still
has on this tree -- under both oracles, 50 seeds, fixed base seed, and for each
row where the two disagree a written reason and a declared direction.  The
discipline is `batch_verify_baseline.json`'s: a delta is allowed to exist, but
not to be anonymous.

This suite keeps the artifact honest in three separate ways, because the
artifact can rot in three separate ways:

  1. Its own internal claims must hold -- no row may be labelled an
     improvement while its numbers say otherwise.  A hand-edited reason that
     excuses a regression is the failure mode this catches.
  2. Every delta must carry a reason, and every row present under both oracles
     must be classified as one or the other.  Silence is not parity.
  3. The xbe column must still REPRODUCE.  A recorded artifact whose numbers no
     longer match the code is worse than no artifact, so the delta rows are
     re-run live.  Only the deltas: they are the rows the flip rests on, and
     re-running all twenty under both oracles takes several minutes, which is
     what `--regenerate` is for.

The live re-run needs the pristine XBE; the full regeneration additionally
needs `delinked/game_state.obj`.  Both self-skip where the input is absent, so
this is runnable on a host that has neither.

Run:  .venv/bin/python tools/equivalence/test_oracle_ab_parity.py
      .venv/bin/python tools/equivalence/test_oracle_ab_parity.py --regenerate
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
ARTIFACT = _HERE / "oracle_migration_expected_deltas.json"

_PY = _ROOT / ".venv" / "bin" / "python"
if not _PY.exists():
    _PY = Path(sys.executable)

# A verdict ranking, used only to check a row's DECLARED direction against its
# own numbers.  `not_applicable` and `error` both mean "no evidence"; `error`
# ranks lower because it also means the harness could not finish.
_RANK = {"error": 0, "not_applicable": 1, "inconclusive": 2, "fail": 3,
         "pass": 3}
_CONF = {None: 0, "none": 0, "low": 1, "moderate": 2, "high": 3}


def _load():
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def _run(name, oracle, seeds, base_seed, flags):
    fd, jpath = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        subprocess.run(
            [str(_PY), str(_HERE / "unicorn_diff.py"), name,
             f"--oracle={oracle}", "--seeds", str(seeds),
             "--seed", str(base_seed), "--output-json", jpath, *flags],
            capture_output=True, text=True, timeout=1800, cwd=str(_ROOT))
        try:
            return json.loads(Path(jpath).read_text(encoding="utf-8"))
        except Exception:
            return {"status": "error", "confidence": None, "coverage_pct": 0.0}
    except subprocess.TimeoutExpired:
        return {"status": "error", "confidence": None, "coverage_pct": 0.0}
    finally:
        os.unlink(jpath)


class TestTheArtifactExists(unittest.TestCase):
    def test_it_is_committed(self):
        self.assertTrue(ARTIFACT.exists(),
                        "the default may not be flipped to --oracle=xbe "
                        "without a committed A/B artifact")

    def test_it_declares_the_run_that_produced_it(self):
        """A verdict table with no recorded seed count or seed is not
        reproducible, and an irreproducible artifact cannot gate anything."""
        meta = _load()["_meta"]
        for key in ("seeds", "base_seed", "flags", "xbe_md5", "what"):
            self.assertIn(key, meta)
        self.assertGreaterEqual(meta["seeds"], 50)

    def test_it_names_the_pristine_xbe(self):
        """Loading the PATCHED default.xbe would make oracle == candidate in
        every ported region and pass everything silently.  The md5 recorded
        here is the one `xbe_image.assert_pristine` enforces at run time."""
        import xbe_image
        self.assertEqual(_load()["_meta"]["xbe_md5"], xbe_image.PRISTINE_MD5)


class TestEveryDeltaIsJustified(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = _load()

    def test_every_delta_carries_a_written_reason(self):
        for name, row in self.data["deltas"].items():
            with self.subTest(target=name):
                self.assertIn("reason", row)
                self.assertGreater(
                    len(row["reason"]), 80,
                    "a delta must explain what changed and why, not just "
                    "assert that it is expected")

    def test_every_delta_declares_a_direction(self):
        for name, row in self.data["deltas"].items():
            with self.subTest(target=name):
                self.assertIn(row.get("direction"),
                              ("improvement", "regression"))

    def test_no_row_is_both_a_delta_and_unchanged(self):
        overlap = set(self.data["deltas"]) & set(self.data["unchanged"])
        self.assertEqual(overlap, set())

    def test_a_row_labelled_unchanged_really_is(self):
        for name, row in self.data["unchanged"].items():
            with self.subTest(target=name):
                self.assertEqual(
                    (row["delinked"]["status"], row["delinked"]["confidence"]),
                    (row["xbe"]["status"], row["xbe"]["confidence"]))

    def test_a_row_labelled_an_improvement_really_is(self):
        """The reason field is prose and cannot be checked, but the numbers
        next to it can.  An improvement must not lose verdict rank, and must
        not lose confidence or coverage while holding rank."""
        for name, row in self.data["deltas"].items():
            if row.get("direction") != "improvement":
                continue
            d, x = row["delinked"], row["xbe"]
            with self.subTest(target=name):
                self.assertGreaterEqual(_RANK[x["status"]], _RANK[d["status"]],
                                        f"{name}: {d['status']} -> "
                                        f"{x['status']} is not an improvement")
                if _RANK[x["status"]] == _RANK[d["status"]]:
                    self.assertGreaterEqual(
                        (_CONF[x["confidence"]], x["coverage_pct"] or 0.0),
                        (_CONF[d["confidence"]], d["coverage_pct"] or 0.0),
                        f"{name}: same verdict but weaker evidence")

    def test_the_meta_counts_match_the_body(self):
        meta = self.data["_meta"]
        self.assertEqual(meta["deltas"], len(self.data["deltas"]))
        self.assertEqual(
            meta["functions"],
            len(self.data["deltas"]) + len(self.data["unchanged"]))
        self.assertEqual(
            meta["regressions"],
            sum(1 for r in self.data["deltas"].values()
                if r.get("direction") == "regression"))

    def test_no_regression_is_recorded(self):
        """If this ever fails, the artifact is telling you the flip was made
        on a target that got worse.  Either fix the harness or move the
        default back."""
        regressions = {n: r for n, r in self.data["deltas"].items()
                       if r.get("direction") == "regression"}
        self.assertEqual(regressions, {})


class TestTheXbeColumnReproduces(unittest.TestCase):
    """The artifact's numbers against the code as it stands now, on the delta
    rows -- the ones the flip actually rests on."""

    @classmethod
    def setUpClass(cls):
        import xbe_image
        if not xbe_image.PRISTINE_XBE.exists():
            raise unittest.SkipTest("no pristine XBE on this host")
        cls.data = _load()
        meta = cls.data["_meta"]
        cls.live = {}
        for name in cls.data["deltas"]:
            cls.live[name] = _run(name, "xbe", meta["seeds"],
                                  meta["base_seed"], meta["flags"])

    def test_the_recorded_verdicts_still_hold(self):
        for name, row in self.data["deltas"].items():
            got = self.live[name]
            with self.subTest(target=name):
                self.assertEqual(
                    (got.get("status"), got.get("confidence")),
                    (row["xbe"]["status"], row["xbe"]["confidence"]),
                    f"{name}: artifact says {row['xbe']}, live run says "
                    f"{got.get('status')}/{got.get('confidence')}; "
                    f"regenerate the artifact in the same commit as whatever "
                    f"changed the verdict")

    def test_the_recorded_coverage_still_holds(self):
        """Coverage is the oracle's own byte count, so it moves when the
        oracle's bound or interception changes -- exactly the things this
        migration touches.  A small band, because a seed-domain change can
        legitimately shift it a little."""
        for name, row in self.data["deltas"].items():
            got = self.live[name].get("coverage_pct") or 0.0
            want = row["xbe"]["coverage_pct"] or 0.0
            with self.subTest(target=name):
                self.assertAlmostEqual(got, want, delta=2.0)


class TestTheDefaultMatchesTheArtifact(unittest.TestCase):
    def test_the_default_oracle_is_xbe(self):
        """The flip and the artifact are one commit: a tree carrying this
        artifact with a `delinked` default would mean the go/no-go decision was
        recorded and then not taken."""
        src = (_HERE / "unicorn_diff.py").read_text(encoding="utf-8")
        i = src.index('parser.add_argument("--oracle"')
        self.assertIn('default="xbe"', src[i:i + 400])


def _regenerate():
    """Re-run the whole corpus under both oracles and rewrite the artifact.

    Deliberately not a test: it takes minutes and it WRITES the file the tests
    read, so it must be an explicit human action, never something a green run
    can do on its own."""
    sys.path.insert(0, str(_HERE))
    import coff_loader

    obj = _ROOT / "delinked" / "game_state.obj"
    if not obj.exists():
        print(f"cannot regenerate: {obj} is absent", file=sys.stderr)
        return 2

    data = _load()
    meta = data["_meta"]
    kb = json.loads((_ROOT / "kb.json").read_text(encoding="utf-8"))
    names = {}
    for o in kb["objects"]:
        if o.get("name") != "game_state.obj":
            continue
        for fn in o["functions"]:
            if not fn.get("ported"):
                continue
            import re as _re
            m = _re.search(r'\b(\w+)\s*\(', fn.get("decl", ""))
            if m:
                names[fn["addr"]] = m.group(1)

    deltas, unchanged = {}, {}
    for addr, name in sorted(names.items(), key=lambda kv: int(kv[0], 16)):
        d = _run(name, "delinked", meta["seeds"], meta["base_seed"],
                 meta["flags"])
        x = _run(name, "xbe", meta["seeds"], meta["base_seed"], meta["flags"])
        keep = ("status", "confidence", "coverage_pct")
        rec = {"addr": addr,
               "delinked": {k: d.get(k) for k in keep},
               "xbe": {k: x.get(k) for k in keep}}
        differs = ((d.get("status"), d.get("confidence"))
                   != (x.get("status"), x.get("confidence")))
        prev = (data["deltas"].get(name) or data["unchanged"].get(name) or {})
        if differs:
            rec["reason"] = prev.get("reason", "TODO: explain this delta")
            rec["direction"] = prev.get("direction", "TODO")
            deltas[name] = rec
        else:
            if prev.get("note"):
                rec["note"] = prev["note"]
            unchanged[name] = rec
        print(f"  {'DELTA' if differs else 'same '}  {name}", flush=True)

    meta["functions"] = len(deltas) + len(unchanged)
    meta["deltas"] = len(deltas)
    meta["regressions"] = sum(1 for r in deltas.values()
                              if r.get("direction") == "regression")
    ARTIFACT.write_text(
        json.dumps({"_meta": meta, "deltas": deltas, "unchanged": unchanged},
                   indent=1) + "\n", encoding="utf-8")
    print(f"wrote {ARTIFACT} ({len(deltas)} delta(s))")
    print("Review every TODO before committing.")
    return 0


if __name__ == "__main__":
    if "--regenerate" in sys.argv:
        sys.exit(_regenerate())
    sys.path.insert(0, str(_HERE))
    unittest.main(verbosity=2)
