#!/usr/bin/env python3
"""Pins the SHARED data image: one address set for both sides (step 6b).

WHY
---
Mapping the pristine XBE into the oracle only is half a design.  The oracle
then reads and writes globals at their real VAs while the candidate reads and
writes 256-byte stand-in slots at GLOBALS_BASE, so `--mem-trace` compares two
disjoint address sets: every write is "oracle-only" plus "lifted-only", which
is 100% false positives, and a pointer-returning function's EAX cannot be
compared at all.

So the image is mapped into BOTH instances and the candidate's DIR32 sites are
identity-relocated onto it.  Three pieces make that work, and each is a way to
get it wrong:

  * `image` stops implying `entry_va`.  The candidate maps the image for its
    DATA and still runs its own clang bytes at CODE_BASE; only `entry_va`
    decides where code goes.
  * a `__imp_X` slot is seeded with X's real ADDRESS whenever X is in the span,
    with no byte evidence required -- the mapped image IS the evidence.  Before
    this, a BSS global absent from the capture (game_state_globals at 0x4ea990)
    left the slot at zero and the candidate wrote through a NULL pointer.
  * `_seed_capture_over_bss` puts the live capture into the image's
    uninitialised holes, on both sides.  Without it, sharing the image would
    TAKE AWAY the capture the candidate used to get from the auto-map path and
    replace it with load-time zeros -- H10's vacuous NULL-guard early-out.

MEASURED
--------
FUN_001bfb60 under --oracle=xbe: 20 seeds with write-trace divergences -> 0,
verdict unchanged at pass/100%/high.  The divergence was
`oracle-only 0x4ea990` against `lifted-only 0x0`: one store, two address
spaces.  `unresolved_dir32` is 0 on every target in the set that runs.

Run:  .venv/bin/python tools/equivalence/test_shared_data_image.py
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
sys.path.insert(0, str(_HERE))

import unicorn_diff as ud
import xbe_image
from stubs import patch_dir32_relocs, GLOBALS_BASE

# A target whose single DIR32 is a dllimport global in .data BSS -- the case
# that used to leave the slot at zero.
SHARED_WRITE = "FUN_001bfb60"
GAME_STATE_GLOBALS = 0x4EA990

_PY = _ROOT / ".venv" / "bin" / "python"
if not _PY.exists():
    _PY = Path(sys.executable)


def _run(name, *flags, seeds=20):
    fd, jpath = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        proc = subprocess.run(
            [str(_PY), str(_HERE / "unicorn_diff.py"), name, "--allow-stubs",
             "--mem-trace", "--seeds", str(seeds), "--seed", "1",
             "--output-json", jpath, *flags],
            capture_output=True, text=True, timeout=900, cwd=str(_ROOT))
        try:
            payload = json.loads(Path(jpath).read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        return payload, proc.stdout + proc.stderr
    finally:
        os.unlink(jpath)


class TestTheAddressSetsAreShared(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (_ROOT / "halo-patched" / "cachebeta.xbe").exists():
            raise unittest.SkipTest("no pristine XBE on this host")
        cls.payload, cls.out = _run(SHARED_WRITE, "--oracle=xbe")

    def test_the_write_traces_no_longer_diverge(self):
        self.assertEqual(self.payload.get("trace_diffs"), 0,
                         "write traces still disagree; with one shared "
                         "address set a divergence should now be a real "
                         "behavioural difference, not an address mismatch\n"
                         + self.out[-2000:])

    def test_the_verdict_is_unchanged(self):
        self.assertEqual(self.payload.get("status"), "pass")
        self.assertEqual(self.payload.get("coverage_pct"), 100.0)

    def test_no_candidate_site_kept_a_private_slot(self):
        self.assertEqual(self.payload.get("unresolved_dir32"), 0, self.out[-2000:])

    def test_the_count_is_reported_even_when_zero(self):
        """It is a confidence input, so its absence and its being zero must not
        look the same to a downstream reader."""
        self.assertIn("unresolved_dir32", self.payload)


class TestTheDelinkedLaneIsUnchanged(unittest.TestCase):
    """6b touches `patch_dir32_relocs`, `_build_globals_seeds` and
    `_run_function`'s mapping -- all shared with the delinked oracle.  It must
    be inert there: identity relocation is gated on an image_span that is None,
    and `image_mapped` on `_oracle_xbe`."""

    @classmethod
    def setUpClass(cls):
        if not (_ROOT / "delinked" / "game_state.obj").exists():
            raise unittest.SkipTest("no delinked game_state.obj on this host")
        cls.payload, cls.out = _run(SHARED_WRITE, "--oracle=delinked")

    def test_the_verdict_matches_the_recorded_baseline(self):
        self.assertEqual(self.payload.get("status"), "pass")
        self.assertEqual(self.payload.get("coverage_pct"), 42.3)
        self.assertEqual(self.payload.get("trace_diffs"), 0)

    def test_identity_relocation_did_not_engage(self):
        self.assertNotIn("unresolved dir32:", self.out)


class TestIdentityRelocationRules(unittest.TestCase):
    """`_identity_addr` decides, per site, whether the candidate points at the
    real address or keeps a slot.  Both answers are load-bearing."""

    def setUp(self):
        if not xbe_image.PRISTINE_XBE.exists():
            self.skipTest("no pristine XBE")
        self.span = ud._image_span_cached()

    @staticmethod
    def _reloc(sym, off=0):
        from coff_loader import CoffReloc
        return CoffReloc(virtual_address=off, symbol_name=sym,
                         reloc_type=0x6, symbol_index=0)

    def _patch(self, sym, **kw):
        code = b"\xa1\x00\x00\x00\x00\xc3"        # mov eax,[imm32]; ret
        out, slots, _ = patch_dir32_relocs(
            code, [self._reloc(sym, 1)], set(), return_slots=True, **kw)
        return int.from_bytes(bytes(out)[1:5], "little"), slots

    def test_an_in_image_dat_symbol_is_pointed_at_its_real_address(self):
        addr, slots = self._patch("DAT_004ea990", image_span=self.span)
        self.assertEqual(addr, GAME_STATE_GLOBALS)
        self.assertEqual(slots, {},
                         "an identity-relocated site must not also burn a slot")

    def test_without_an_image_span_it_still_gets_a_slot(self):
        """The delinked lane's behaviour, unchanged."""
        addr, slots = self._patch("DAT_004ea990")
        self.assertEqual(addr, GLOBALS_BASE)
        self.assertEqual(list(slots), ["DAT_004ea990"])

    def test_an_out_of_image_dat_symbol_still_gets_a_slot(self):
        lo, _hi = self.span
        addr, slots = self._patch(f"DAT_{lo - 0x2000:08x}", image_span=self.span)
        self.assertEqual(addr, GLOBALS_BASE)

    def test_a_dllimport_symbol_is_never_identity_relocated(self):
        """Its slot carries an extra dereference: `mov eax,[slot]; mov
        eax,[eax]`.  Pointing the site at the global itself would make the
        second deref read the global's VALUE as a pointer."""
        addr, slots = self._patch("__imp__game_state_globals",
                                  image_span=self.span)
        self.assertEqual(addr, GLOBALS_BASE)
        self.assertEqual(list(slots), ["__imp__game_state_globals"])

    def test_a_named_symbol_is_not_resolved_by_name(self):
        """Only the address-encoding forms (DAT_/PTR_/FLOAT_) are trusted.
        Resolving a bare name through kb.json would reintroduce the
        _GLOBAL_NAME_ALIASES hazard -- a label matching a DIFFERENT kb global
        at a different address -- and here the consequence is not a wrong seed
        but a wrong STORE ADDRESS."""
        addr, slots = self._patch("game_state_globals", image_span=self.span)
        self.assertEqual(addr, GLOBALS_BASE)


class TestTheDllimportSlotPointsAtMappedStorage(unittest.TestCase):
    def setUp(self):
        if not xbe_image.PRISTINE_XBE.exists():
            self.skipTest("no pristine XBE")

    def test_bss_with_no_evidence_is_unseeded_without_the_image(self):
        """The bug: game_state_globals is .data past raw_size and absent from
        the capture, so there was nothing to seed the slot with, and the
        candidate dereferenced zero."""
        seeds = ud._build_globals_seeds(
            {"__imp__game_state_globals": 0x0A000000})
        self.assertEqual(seeds, {})

    def test_the_mapped_image_is_itself_the_evidence(self):
        import struct
        seeds = ud._build_globals_seeds(
            {"__imp__game_state_globals": 0x0A000000}, image_mapped=True)
        self.assertEqual(seeds.get(0x0A000000),
                         struct.pack("<I", GAME_STATE_GLOBALS))

    def test_an_out_of_span_symbol_is_still_not_invented(self):
        seeds = ud._build_globals_seeds({"__imp__nosuchglobal": 0x0A000000},
                                        image_mapped=True)
        self.assertEqual(seeds, {})


class TestCaptureOverBss(unittest.TestCase):
    def setUp(self):
        try:
            import unicorn  # noqa: F401
        except ImportError:
            self.skipTest("unicorn not installed")
        if not xbe_image.PRISTINE_XBE.exists():
            self.skipTest("no pristine XBE")
        self.raw, self.secs = xbe_image.load_xbe()

    def test_it_seeds_something(self):
        """If this ever returns 0, sharing the image silently replaced the
        candidate's capture-backed globals with load-time zeros (H10)."""
        import unicorn
        uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_32)
        xbe_image.map_image(uc, self.raw, self.secs)
        n = ud._seed_capture_over_bss(uc, self.raw, self.secs)
        self.assertGreater(n, 0)

    def test_it_leaves_file_backed_bytes_exactly_as_mapped(self):
        """H11.  A file-backed address is ground truth from this build; the
        capture must never land on top of it."""
        import unicorn
        uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_32)
        xbe_image.map_image(uc, self.raw, self.secs)
        backed = [a for a, v in ud._KNOWN_GLOBAL_BYTES.items()
                  if xbe_image.read_va_raw(self.raw, self.secs, a, len(v))]
        self.assertGreater(len(backed), 100)
        before = {a: bytes(uc.mem_read(a, 4)) for a in sorted(backed)[:200]}
        ud._seed_capture_over_bss(uc, self.raw, self.secs)
        for a, b in before.items():
            with self.subTest(addr=hex(a)):
                self.assertEqual(bytes(uc.mem_read(a, 4)), b)

    def test_it_runs_for_both_instances(self):
        """A capture seeded on one side only would be a divergence the harness
        manufactured, so the call sits in the shared mapping path rather than
        in either side's setup."""
        src = (_HERE / "unicorn_diff.py").read_text(encoding="utf-8")
        self.assertEqual(
            src.count("_seed_capture_over_bss(uc, _img_raw, _img_secs)"), 1)
        block = src[src.index("if image is not None:\n        import xbe_image"):
                    src.index("if entry_va is None:")]
        self.assertIn("_seed_capture_over_bss", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
