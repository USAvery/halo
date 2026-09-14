#!/usr/bin/env python3
"""Pins for `xbe_image`: the oracle image is pristine, complete, and bounded.

Three failure modes this exists to catch, in order of how badly they would
mislead:

  1. **Wrong XBE.** Mapping `halo-patched/default.xbe` (our patched build)
     instead of `cachebeta.xbe` makes oracle == candidate in every region we
     have already ported, so every equivalence check passes for free.  Checked
     two ways: against the literal md5 from CLAUDE.md, and against
     `function_bounds.json`'s `_meta.xbe_md5`, so the bounds table and the
     image can never silently describe different binaries.
  2. **BSS splattered with file bytes.** `.data` has vsize 0x36a918 and
     raw_size 0x69a3c.  A 3-tuple section walk with no `raw_size` (as in
     `check_delinked_bounds.load_xbe`) writes ~3.1 MB of unrelated file
     content over what the loader leaves zeroed.
  3. **Span rounded down past the image.** `.text` starts at 0x12000, which is
     4 KB- but not 64 KB-aligned.  Aligning the map to 64 KB would map 0x10000
     and make near-NULL dereferences succeed instead of faulting.
"""
import json
import random
import struct
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "verify"))
sys.path.insert(0, str(REPO / "tools" / "audit"))

import xbe_image

BOUNDS_JSON = REPO / "tools" / "verify" / "function_bounds.json"

try:
    import unicorn
    from unicorn import UC_ARCH_X86, UC_MODE_32, Uc
except ImportError:  # pragma: no cover
    unicorn = None


class TestPristineIdentity(unittest.TestCase):
    def test_md5_matches_claude_md(self):
        self.assertEqual(xbe_image.xbe_md5(), xbe_image.PRISTINE_MD5)

    def test_md5_matches_bounds_table(self):
        """The bounds table and the oracle image must describe one binary."""
        meta = json.loads(BOUNDS_JSON.read_text()).get("_meta", {})
        self.assertEqual(meta.get("xbe_md5"), xbe_image.PRISTINE_MD5,
                         "function_bounds.json was generated from a different XBE")

    def test_assert_pristine_accepts_the_real_image(self):
        xbe_image.assert_pristine()  # must not raise

    def test_assert_pristine_rejects_a_different_binary(self):
        """Any XBE that is not debug-2276 must be refused as an oracle.

        Exercised against a byte-mutated copy rather than `default.xbe`, so the
        guard is pinned on every host and not only where a patched build
        happens to exist.
        """
        import tempfile

        raw, _ = xbe_image.load_xbe()
        mutated = bytearray(raw)
        mutated[0x2000] ^= 0xFF  # first byte of .text
        with tempfile.NamedTemporaryFile(suffix=".xbe", delete=False) as fh:
            fh.write(bytes(mutated))
            path = Path(fh.name)
        try:
            with self.assertRaises(ValueError):
                xbe_image.assert_pristine(path)
        finally:
            path.unlink()


class TestSectionTable(unittest.TestCase):
    def setUp(self):
        self.raw, self.secs = xbe_image.load_xbe()

    def test_section_count(self):
        self.assertEqual(len(self.secs), 24)

    def test_text_section_bounds(self):
        text = xbe_image.section_at(self.secs, 0x12000)
        self.assertIsNotNone(text)
        self.assertEqual(text.name, ".text")
        self.assertEqual(text.va, 0x12000)
        self.assertEqual(text.raw_off, 0x2000)

    def test_data_has_bss_tail(self):
        data = xbe_image.section_at(self.secs, 0x2c84c0)
        self.assertIsNotNone(data)
        self.assertEqual(data.name, ".data")
        self.assertLess(data.raw_size, data.vsize,
                        "a .data with no BSS tail means raw_size was misparsed")

    def test_image_span_is_4k_aligned_not_64k(self):
        lo, hi = xbe_image.image_span(self.secs)
        self.assertEqual(lo, 0x12000)
        self.assertEqual(hi, 0x642000)

    def test_read_va_zero_fills_bss(self):
        data = xbe_image.section_at(self.secs, 0x2c84c0)
        bss = data.va + data.raw_size + 0x100
        self.assertEqual(xbe_image.read_va(self.raw, self.secs, bss, 64),
                         b"\x00" * 64)

    def test_read_va_straddling_raw_boundary(self):
        """A read starting in raw data and running into BSS gets both halves."""
        data = xbe_image.section_at(self.secs, 0x2c84c0)
        edge = data.va + data.raw_size - 8
        got = xbe_image.read_va(self.raw, self.secs, edge, 16)
        self.assertEqual(len(got), 16)
        self.assertEqual(got[:8],
                         self.raw[data.raw_off + data.raw_size - 8:
                                  data.raw_off + data.raw_size])
        self.assertEqual(got[8:], b"\x00" * 8)

    def test_read_va_outside_any_section(self):
        self.assertEqual(xbe_image.read_va(self.raw, self.secs, 0xF0000000, 4), b"")
        self.assertIsNone(xbe_image.va_to_off(self.secs, 0x11FFF))

    def test_load_xbe_is_cached(self):
        again = xbe_image.load_xbe()
        self.assertIs(again[0], self.raw)

    def test_legacy_shim_shape(self):
        _raw, legacy = xbe_image.load_xbe_legacy()
        self.assertEqual(len(legacy), 24)
        self.assertEqual(len(legacy[0]), 3)
        self.assertEqual(xbe_image.va_to_off(legacy, 0x12000), 0x2000)


@unittest.skipIf(unicorn is None, "unicorn not installed")
class TestMapImage(unittest.TestCase):
    def setUp(self):
        self.raw, self.secs = xbe_image.load_xbe()
        self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        self.lo, self.hi = xbe_image.map_image(self.uc, self.raw, self.secs)

    def test_text_bytes_land_at_real_va(self):
        self.assertEqual(bytes(self.uc.mem_read(0x12000, 16)),
                         self.raw[0x2000:0x2010])

    def test_rdata_bytes_land_at_real_va(self):
        rdata = xbe_image.section_at(self.secs, 0x253080)
        self.assertEqual(rdata.name, ".rdata")
        self.assertEqual(bytes(self.uc.mem_read(rdata.va, 32)),
                         self.raw[rdata.raw_off:rdata.raw_off + 32])

    def test_bss_is_zero_in_the_map(self):
        data = xbe_image.section_at(self.secs, 0x2c84c0)
        bss = data.va + data.raw_size + 0x1000
        self.assertEqual(bytes(self.uc.mem_read(bss, 256)), b"\x00" * 256)

    def test_page_below_text_is_unmapped(self):
        """A near-NULL dereference must still fault, not read zeros."""
        with self.assertRaises(unicorn.UcError):
            self.uc.mem_read(0x11FFF, 1)

    def test_page_above_image_is_unmapped(self):
        with self.assertRaises(unicorn.UcError):
            self.uc.mem_read(self.hi, 1)

    def test_every_section_is_readable(self):
        for s in self.secs:
            self.assertEqual(len(self.uc.mem_read(s.va, 1)), 1,
                             "section %s not mapped at %#x" % (s.name, s.va))

    def test_image_pages_cover_the_span(self):
        pages = xbe_image.image_pages(self.secs)
        self.assertIn(0x12000 & ~0xFFFF, pages)
        self.assertIn((self.hi - 1) & ~0xFFFF, pages)


class TestAgreesWithVc71Reference(unittest.TestCase):
    """The oracle's bytes must equal what the VC71 lane already scores against.

    Both derive from the same XBE + the same bounds table, so any disagreement
    means one of the two is reading the image wrong.
    """

    def test_function_bytes_match_image_slice(self):
        import xbe_reference as xr

        raw, secs = xbe_image.load_xbe()
        bounds = json.loads(BOUNDS_JSON.read_text())
        addrs = sorted(k for k in bounds if k != "_meta")
        random.Random(1234).shuffle(addrs)

        checked = 0
        for key in addrs:
            if checked >= 200:
                break
            addr = int(key, 16)
            code, err = xr.function_bytes(addr)
            if code is None or not code:
                continue
            self.assertEqual(code, xbe_image.read_va(raw, secs, addr, len(code)),
                             "mismatch at %s (%d bytes)" % (key, len(code)))
            checked += 1
        self.assertGreaterEqual(checked, 200, "sampled too few functions")


class TestLegacyParserParity(unittest.TestCase):
    """`xbe_image` must reproduce the hand-rolled parsers it replaced.

    The two shapes that existed before consolidation are inlined here verbatim
    as the reference: the 3-tuple walk from `check_delinked_bounds.load_xbe`
    and the 4-tuple walk from `check_fpu_association.load_xbe`.  Keeping them
    as a fixture is the point -- it pins the replacement against the code it
    replaced, so a future edit to `xbe_image` cannot silently change what six
    audit scripts read out of the binary.
    """

    @staticmethod
    def _orig_sections_4tuple(raw):
        base = struct.unpack_from("<I", raw, 0x104)[0]
        nsec = struct.unpack_from("<I", raw, 0x11C)[0]
        hdr = struct.unpack_from("<I", raw, 0x120)[0] - base
        out = []
        for i in range(nsec):
            off = hdr + i * 0x38
            out.append(struct.unpack_from("<IIII", raw, off + 4))
        return out

    @staticmethod
    def _orig_read_va(raw, sections, va, size):
        for sva, svs, sra, srs in sections:
            if sva <= va < sva + svs:
                off = sra + (va - sva)
                return raw[off:off + min(size, srs - (va - sva))]
        return b""

    @staticmethod
    def _orig_va_to_off(secs, va):
        for vaddr, vsize, raw_off in secs:
            if vaddr <= va < vaddr + vsize:
                return raw_off + (va - vaddr)
        return None

    def setUp(self):
        self.raw, _ = xbe_image.load_xbe()
        _, self.secs3 = xbe_image.load_xbe_legacy()
        _, self.secs4 = xbe_image.load_xbe_legacy4()

    def _probes(self):
        """Every section edge and raw/BSS boundary, plus random addresses."""
        out = []
        for va, vs, _ra, rs in self.secs4:
            out += [va - 1, va, va + 1, va + vs - 1, va + vs,
                    va + rs - 1, va + rs]
        rnd = random.Random(99)
        out += [rnd.randrange(0, 0x700000) for _ in range(5000)]
        return out

    def test_section_tables_identical(self):
        self.assertEqual(self._orig_sections_4tuple(self.raw), self.secs4)
        self.assertEqual([t[:3] for t in self.secs4], self.secs3)

    def test_va_to_off_identical(self):
        for va in self._probes():
            self.assertEqual(self._orig_va_to_off(self.secs3, va),
                             xbe_image.va_to_off(self.secs3, va),
                             "va_to_off diverges at %#x" % va)

    def test_read_va_identical_where_raw_data_exists(self):
        """Identical on full reads; a prefix where the old form read short.

        The old walk clamped to `raw_size` and returned fewer bytes than asked
        for near a BSS boundary.  The new one zero-fills to the requested size,
        which is what the loader actually leaves in memory -- so the old result
        must be a prefix of the new one, never a conflict.
        """
        full = 0
        for va in self._probes():
            for size in (1, 4, 16, 64):
                old = self._orig_read_va(self.raw, self.secs4, va, size)
                new = xbe_image.read_va(self.raw, self.secs4, va, size)
                self.assertEqual(new[:len(old)], old,
                                 "read_va conflicts at %#x/%d" % (va, size))
                if len(old) == size:
                    self.assertEqual(old, new)
                    full += 1
        self.assertGreater(full, 1000, "sampled too few full reads")

    def test_whole_text_section_identical(self):
        text = xbe_image.section_at(self.secs4, 0x12000)
        self.assertEqual(
            self._orig_read_va(self.raw, self.secs4, text.va, text.vsize),
            xbe_image.read_va(self.raw, self.secs4, text.va, text.vsize))

    @unittest.skipIf(unicorn is None, "unicorn not installed")
    def test_map_image_matches_the_original_flat_map(self):
        """`map_image` must reproduce `test_inflate_roundtrip`'s flat mapping.

        That file already mapped a whole XBE at real VAs into Unicorn, so it is
        the working implementation this one generalizes; inlined verbatim here
        as the reference.  The only intended difference is that `map_image`
        lets Unicorn zero-fill the BSS tail instead of writing zeros over it.
        """
        def orig_sections(raw):
            base = struct.unpack_from("<I", raw, 0x104)[0]
            nsec = struct.unpack_from("<I", raw, 0x11C)[0]
            sec_hdr = struct.unpack_from("<I", raw, 0x120)[0] - base
            out = []
            for i in range(nsec):
                off = sec_hdr + i * 0x38
                vaddr, vsize, raw_off, raw_size = struct.unpack_from(
                    "<IIII", raw, off + 0x04)
                blob = raw[raw_off:raw_off + raw_size]
                if len(blob) < vsize:
                    blob = blob + b"\0" * (vsize - len(blob))
                out.append((vaddr, blob[:vsize]))
            return out

        raw, secs = xbe_image.load_xbe()
        ref = orig_sections(raw)

        # The `(vaddr, blob)` adapter the delegating callers now use.
        self.assertEqual(
            [(s.va, xbe_image.read_va(raw, secs, s.va, s.vsize)) for s in secs],
            ref)

        uc_old = Uc(UC_ARCH_X86, UC_MODE_32)
        lo = min(v for v, _ in ref) & ~0xFFF
        hi = (max(v + len(b) for v, b in ref) + 0xFFF) & ~0xFFF
        uc_old.mem_map(lo, hi - lo, unicorn.UC_PROT_ALL)
        for va, blob in ref:
            if blob:
                uc_old.mem_write(va, blob)

        uc_new = Uc(UC_ARCH_X86, UC_MODE_32)
        self.assertEqual(xbe_image.map_image(uc_new, raw, secs), (lo, hi))

        chunk = 0x10000
        for off in range(lo, hi, chunk):
            n = min(chunk, hi - off)
            self.assertEqual(bytes(uc_old.mem_read(off, n)),
                             bytes(uc_new.mem_read(off, n)),
                             "mapped memory differs at %#x" % off)

    def test_as_section_normalizes_every_shape(self):
        want = xbe_image.load_xbe()[1][0]
        self.assertEqual(xbe_image.as_section(want), want)
        four = xbe_image.as_section(self.secs4[0])
        self.assertEqual((four.va, four.vsize, four.raw_off, four.raw_size),
                         self.secs4[0])
        three = xbe_image.as_section(self.secs3[0])
        self.assertEqual(three.raw_size, three.vsize,
                         "a 3-tuple has no raw_size; vsize is the safe default")


if __name__ == "__main__":
    unittest.main(verbosity=2)
