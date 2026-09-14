#!/usr/bin/env python3
"""Pins the layering between `known_globals.json` and the XBE's own bytes.

WHY
---
Globals seeding has two sources and they are not interchangeable.

`known_globals.json` is a LIVE CAPTURE.  Its strength is that it holds runtime
values for addresses the image says nothing about; its weakness is that a
capture has a WIDTH -- whatever the extractor sampled.  `0x2533d0` is the
worked example: it is the 1e-4 epsilon double, the capture holds only its low
dword, and seeding four bytes leaves the oracle comparing against 4.6e-310
instead of 1e-4 and taking the other branch.

The pristine XBE has no width limit and is the binary of truth, but it only
speaks for addresses the FILE backs.  Everything past a section's `raw_size`
is BSS, where the image's honest answer is "zero at load" -- which is
indistinguishable from a real zero-initialised global and would shadow the
capture if it were seeded.

So the rule is: image where the image knows, capture where it does not.  This
suite pins that rule, the measured overlap it rests on, and H11 -- the capture
must never be stamped over real image bytes.

It deliberately does NOT assert that `known_globals.json` as a whole equals
the XBE.  998 of its entries have no load-time value at all, and asserting
equality there would be asserting that a running game looks like a file on
disk.  The equality claim is scoped to the file-backed subset, where it is
measured to hold exactly.

Run:  .venv/bin/python tools/equivalence/test_known_globals_vs_xbe.py
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import unicorn_diff as ud
import xbe_image

# Addresses where a file-backed capture is allowed to disagree with the image.
# Empty, and it should stay that way: a disagreement means either the capture
# came from a different build than cachebeta.xbe (in which case seeding it is
# unsound) or the game rewrites that address before the capture point (in which
# case it belongs in a state snapshot, not in a static seed table).  Adding an
# entry here needs a written reason in the same commit.
REVIEWED_DISAGREEMENTS = {}

EPSILON_DOUBLE = 0x2533D0          # 1e-4, .rdata, captured 4 bytes wide


def _image():
    if not xbe_image.PRISTINE_XBE.exists():
        raise unittest.SkipTest(f"no pristine XBE at {xbe_image.PRISTINE_XBE}")
    return xbe_image.load_xbe()


class TestTheOverlapIsAnEcho(unittest.TestCase):
    """The two sources are not independent opinions about .rdata.  Most of the
    capture is the image, sampled narrowly."""

    @classmethod
    def setUpClass(cls):
        cls.raw, cls.secs = _image()
        cls.kg = ud._KNOWN_GLOBAL_BYTES
        cls.backed = {
            a: v for a, v in cls.kg.items()
            if xbe_image.read_va_raw(cls.raw, cls.secs, a, len(v))
        }

    def test_the_file_is_not_empty(self):
        self.assertGreater(len(self.kg), 1000,
                           "known_globals.json failed to load; every seeding "
                           "test below would pass vacuously")

    def test_most_of_the_capture_is_file_backed(self):
        """If this ever inverts, the layering below stops mattering and the
        comment in unicorn_diff.py describing the split is wrong."""
        self.assertGreater(len(self.backed), len(self.kg) // 2)

    def test_every_file_backed_capture_agrees_with_the_image(self):
        """The load-bearing check.  This is what makes it safe to prefer the
        image: on the 6183 addresses where both sources speak, they say the
        same thing, so preferring the image changes only the WIDTH."""
        bad = []
        for addr, cap in sorted(self.backed.items()):
            if addr in REVIEWED_DISAGREEMENTS:
                continue
            img = xbe_image.read_va_raw(self.raw, self.secs, addr, len(cap))
            if img != cap:
                sec = xbe_image.section_at(self.secs, addr)
                bad.append(f"{addr:#010x} ({sec.name if sec else '?'}): "
                           f"capture={cap.hex()} image={img.hex()}")
        self.assertEqual(bad, [],
                         "known_globals.json disagrees with cachebeta.xbe at "
                         "addresses the image file backs; either the capture "
                         "is from another build or the game rewrites these "
                         "before capture. Investigate before adding to "
                         "REVIEWED_DISAGREEMENTS:\n  " + "\n  ".join(bad[:20]))

    def test_the_unbacked_remainder_is_exempt_by_design(self):
        """These are the addresses the capture exists for.  The test asserts
        they are genuinely unbacked, i.e. that the exemption is a property of
        the image and not a way to excuse a mismatch."""
        unbacked = set(self.kg) - set(self.backed)
        self.assertGreater(len(unbacked), 0)
        for addr in sorted(unbacked)[:200]:
            with self.subTest(addr=hex(addr)):
                self.assertEqual(
                    xbe_image.read_va_raw(self.raw, self.secs, addr, 1), b"",
                    "address counted as unbacked but the file backs it")


class TestTheImageReaderRefusesToInvent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw, cls.secs = _image()

    def test_a_file_backed_address_reads_back(self):
        self.assertEqual(ud._xbe_global_bytes(0x253080, 4),
                         xbe_image.read_va_raw(self.raw, self.secs, 0x253080, 4))

    def test_bss_reads_none_rather_than_zeros(self):
        """`read_va` would hand back zeros here with correct loader semantics.
        Seeding those would overwrite the capture with a value that looks
        identical to a real zero -- so this must use `read_va_raw`."""
        data = xbe_image.as_section(
            [s for s in self.secs if xbe_image.as_section(s).name == ".data"][0])
        bss_addr = data.va + data.raw_size + 0x100
        self.assertTrue(data.contains_va(bss_addr), "picked a non-BSS address")
        self.assertEqual(xbe_image.read_va(self.raw, self.secs, bss_addr, 4),
                         b"\x00\x00\x00\x00")
        self.assertIsNone(ud._xbe_global_bytes(bss_addr, 4))

    def test_an_address_in_no_section_reads_none(self):
        lo, hi = xbe_image.image_span(self.secs)
        self.assertIsNone(ud._xbe_global_bytes(lo - 0x1000, 4))
        self.assertIsNone(ud._xbe_global_bytes(hi + 0x1000, 4))

    def test_a_short_tail_is_returned_short_not_padded(self):
        """A read straddling the end of raw data must not be padded up to
        `size`, or a caller could not tell data from fill."""
        data = xbe_image.as_section(
            [s for s in self.secs if xbe_image.as_section(s).name == ".data"][0])
        straddle = data.va + data.raw_size - 8
        got = ud._xbe_global_bytes(straddle, 64)
        self.assertEqual(len(got), 8)


class TestTheEpsilonWidthBug(unittest.TestCase):
    """The concrete regression this step retires."""

    @classmethod
    def setUpClass(cls):
        cls.raw, cls.secs = _image()

    def test_the_capture_is_narrower_than_the_constant(self):
        cap = ud._KNOWN_GLOBAL_BYTES.get(EPSILON_DOUBLE)
        if cap is None:
            self.skipTest("0x2533d0 not in this known_globals.json")
        self.assertEqual(len(cap), 4,
                         "the capture widened; if 0x2533d0 is now 8 bytes the "
                         "worked example in this suite needs repointing, not "
                         "deleting -- the width hazard is general")

    def test_the_image_supplies_the_whole_double(self):
        import struct
        got = ud._xbe_global_bytes(EPSILON_DOUBLE, 8)
        self.assertEqual(len(got), 8)
        # The constant is a float widened to double -- MSVC materialised
        # (double)(float)1e-4, not 1e-4 -- so compare against that, not
        # against the decimal literal.
        self.assertEqual(struct.unpack("<d", got)[0],
                         float(struct.unpack("<f", struct.pack("<f", 1e-4))[0]))

    def test_a_dat_slot_for_it_is_seeded_at_full_width(self):
        """End to end through `_build_globals_seeds`: the DAT_ name encodes the
        address, so this is the exact path a delinked DIR32 site takes."""
        seeds = ud._build_globals_seeds({"DAT_002533d0": 0x0A000100})
        self.assertIn(0x0A000100, seeds)
        blob = seeds[0x0A000100]
        self.assertGreaterEqual(len(blob), 8)
        import struct
        self.assertEqual(struct.unpack("<d", blob[:8])[0],
                         float(struct.unpack("<f", struct.pack("<f", 1e-4))[0]))

    def test_a_seed_never_reaches_the_next_slot(self):
        """Slots are 256 bytes apart; a wider seed would corrupt its
        neighbour's value with bytes from an unrelated address."""
        self.assertEqual(ud._GLOBALS_SLOT_STRIDE, 256)
        seeds = ud._build_globals_seeds({"DAT_002533d0": 0x0A000100})
        self.assertLessEqual(len(seeds[0x0A000100]), ud._GLOBALS_SLOT_STRIDE)


class TestSeedPrecedence(unittest.TestCase):
    def test_a_live_snapshot_still_outranks_the_image(self):
        """A snapshot is the state of a running game at a chosen moment; the
        image is only its state at load.  For a target captured mid-game the
        snapshot is the more specific evidence."""
        _image()
        snap = {0x253300: bytes(0x200)}
        snap = {0x253300: snap[0x253300][:EPSILON_DOUBLE - 0x253300]
                          + b"\xde\xad\xbe\xef"
                          + bytes(0x200 - (EPSILON_DOUBLE - 0x253300) - 4)}
        seeds = ud._build_globals_seeds({"DAT_002533d0": 0x0A000100},
                                        snapshot_overrides=snap)
        self.assertEqual(seeds[0x0A000100][:4], b"\xde\xad\xbe\xef")

    def test_a_bss_address_still_falls_through_to_the_capture(self):
        """The image says zero here and must not be allowed to say it."""
        raw, secs = _image()
        cand = [a for a, v in ud._KNOWN_GLOBAL_BYTES.items()
                if not xbe_image.read_va_raw(raw, secs, a, len(v))
                and xbe_image.section_at(secs, a) is not None
                and any(b for b in v)]
        if not cand:
            self.skipTest("no non-zero BSS capture to test with")
        addr = sorted(cand)[0]
        seeds = ud._build_globals_seeds({f"DAT_{addr:08x}": 0x0A000200})
        self.assertEqual(seeds[0x0A000200][:len(ud._KNOWN_GLOBAL_BYTES[addr])],
                         ud._KNOWN_GLOBAL_BYTES[addr])

    def test_an_unknown_address_seeds_nothing(self):
        """No entry must be invented: an unseeded slot reads as zero, which is
        at least symmetric across both sides."""
        _image()
        lo, _ = xbe_image.image_span(_image()[1])
        seeds = ud._build_globals_seeds({f"DAT_{lo - 0x2000:08x}": 0x0A000300})
        self.assertNotIn(0x0A000300, seeds)


class TestPageSeedingRespectsTheImage(unittest.TestCase):
    """`_seed_known_globals` runs on pages the harness maps itself.  It must
    fill them with what we know, and must never be pointed at a page of the
    genuinely mapped image (H11)."""

    def setUp(self):
        try:
            import unicorn  # noqa: F401
        except ImportError:
            self.skipTest("unicorn not installed")
        self.raw, self.secs = _image()

    def _uc(self):
        import unicorn
        return unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_32)

    def test_a_synthetic_page_at_an_rdata_address_gets_real_bytes(self):
        """This is the failure mode the migration exists to remove: the page
        used to be zeros, so a constant read from it was 0.0 on the oracle and
        a NULL-guard early-out on the candidate."""
        import unicorn
        uc = self._uc()
        page = 0x253080 & ~0xFFFF
        uc.mem_map(page, 0x10000)
        uc.mem_write(page, b"\x00" * 0x10000)
        ud._seed_known_globals(uc, page, 0x10000)
        self.assertEqual(bytes(uc.mem_read(0x253080, 4)),
                         xbe_image.read_va_raw(self.raw, self.secs,
                                               0x253080, 4))

    def test_seeding_stays_inside_the_requested_window(self):
        """It is called with one 64 KB page; writing outside it would land in
        an unmapped region (raising) or another page (corrupting it)."""
        uc = self._uc()
        page = 0x253080 & ~0xFFFF
        uc.mem_map(page, 0x10000)
        uc.mem_write(page, b"\x00" * 0x10000)
        ud._seed_xbe_initialized(uc, page, 0x10000)   # must not raise

    def test_seeding_outside_the_image_writes_nothing(self):
        uc = self._uc()
        from memmap import GLOBALS_BASE
        uc.mem_map(GLOBALS_BASE, 0x10000)
        uc.mem_write(GLOBALS_BASE, b"\xa5" * 0x10000)
        ud._seed_xbe_initialized(uc, GLOBALS_BASE, 0x10000)
        self.assertEqual(bytes(uc.mem_read(GLOBALS_BASE, 16)), b"\xa5" * 16)

    def test_the_image_wins_over_the_capture_in_the_seeding_order(self):
        src = (_HERE / "unicorn_diff.py").read_text(encoding="utf-8")
        body = src[src.index("def _seed_known_globals("):
                   src.index("def _seed_xbe_initialized(")]
        self.assertLess(body.index("_KNOWN_GLOBAL_BYTES.items()"),
                        body.index("_seed_xbe_initialized(uc, base, size)"),
                        "the capture must be stamped first and the image over "
                        "it, so a wide image read wins over a narrow capture")

    def test_h11_the_capture_is_never_stamped_over_a_mapped_image_page(self):
        src = (_HERE / "unicorn_diff.py").read_text(encoding="utf-8")
        self.assertIn("if page in _image_pages:", src,
                      "the snapshot-override loop no longer skips pages of "
                      "the mapped image, so known_globals.json can be written "
                      "over real .data (H11)")
        loop = src[src.index("_override_mapped = set()"):]
        loop = loop[:loop.index("_override_mapped.add(page)\n            uc.mem_write")]
        self.assertLess(loop.index("if page in _image_pages:"),
                        loop.index("_seed_known_globals(uc, page, 0x10000)"),
                        "the image-page skip must come BEFORE the zero-fill "
                        "and seed, not after")


if __name__ == "__main__":
    unittest.main(verbosity=2)
