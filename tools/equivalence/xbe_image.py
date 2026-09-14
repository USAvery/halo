"""One XBE section-table parser, and one way to map the image into Unicorn.

Seven modules used to hand-roll the XBE header walk (`check_delinked_bounds`,
`check_fpu_association`, `check_callee_reg_args`, `extract_globals`,
`test_inflate_roundtrip`, `analysis/ntsc_callgraph`, `analysis/classify_common`).
They disagreed in a way that matters: `check_delinked_bounds.load_xbe` returns
`(vaddr, vsize, raw_off)` with **no raw_size**, and `.data` has vsize 0x36a918
against a raw_size of only 0x69a3c -- the rest is BSS.  Mapping `.data` from a
3-tuple therefore splatters ~3.1 MB of unrelated file bytes over what should be
zero-filled memory.  `Section` here always carries `raw_size`.

The equivalence lane's oracle uses `map_image` to place the pristine image at
its REAL virtual addresses, which is what makes every absolute reference in
Xbox debug-build code correct with no relocation at all.  See
docs/raw-xbe-oracle-migration.md.

The image mapped MUST be the pristine `halo-patched/cachebeta.xbe`, never our
patched `halo-patched/default.xbe`: loading the patched build as the oracle
would silently make oracle == candidate in every region we have already ported
and pass everything.  `assert_pristine()` is the guard.
"""
import bisect
import hashlib
import struct
from pathlib import Path
from typing import NamedTuple, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The one true oracle image.
PRISTINE_XBE = _REPO_ROOT / "halo-patched" / "cachebeta.xbe"

#: MD5 of debug build 2276 (version 01.10.12.2276, Oct 12 2001) -- see CLAUDE.md
#: and tools/verify/function_bounds.json `_meta.xbe_md5`.
PRISTINE_MD5 = "c7869590a1c64ad034e49a5ee0c02465"

#: Unicorn's minimum mapping granularity.
PAGE = 0x1000

#: Granularity the rest of the harness auto-maps at (`addr & ~0xFFFF`).
BIG_PAGE = 0x10000

_SECTION_HEADER_SIZE = 0x38


class Section(NamedTuple):
    """One XBE section.  `raw_size` may be smaller than `vsize` (BSS tail)."""
    name: str
    va: int
    vsize: int
    raw_off: int
    raw_size: int

    def contains_va(self, va: int) -> bool:
        """True if `va` is in this section's virtual extent (BSS included)."""
        return self.va <= va < self.va + self.vsize

    def raw_contains_va(self, va: int) -> bool:
        """True if `va` is backed by bytes in the file (BSS excluded)."""
        return self.va <= va < self.va + self.raw_size


_CACHE: dict = {}


def load_xbe(path=None):
    """Return `(raw_bytes, [Section, ...])` for an XBE, parsed once per path.

    Defaults to the pristine image.  The whole file is held in memory (3.4 MB)
    so repeated `read_va` calls do not re-open it.
    """
    p = Path(path) if path is not None else PRISTINE_XBE
    key = str(p.resolve())
    hit = _CACHE.get(key)
    if hit is not None:
        return hit

    raw = p.read_bytes()
    base = struct.unpack_from("<I", raw, 0x104)[0]
    nsec = struct.unpack_from("<I", raw, 0x11C)[0]
    shdr = struct.unpack_from("<I", raw, 0x120)[0] - base

    secs = []
    for i in range(nsec):
        off = shdr + i * _SECTION_HEADER_SIZE
        va, vsize, raw_off, raw_size = struct.unpack_from("<IIII", raw, off + 4)
        name_va = struct.unpack_from("<I", raw, off + 0x14)[0] - base
        name = ""
        if 0 <= name_va < len(raw):
            end = raw.find(b"\x00", name_va)
            if end != -1:
                name = raw[name_va:end].decode("ascii", "replace")
        secs.append(Section(name, va, vsize, raw_off, raw_size))

    _CACHE[key] = (raw, secs)
    return raw, secs


def xbe_md5(path=None) -> str:
    """MD5 of the XBE file (hex)."""
    raw, _ = load_xbe(path)
    return hashlib.md5(raw).hexdigest()


def assert_pristine(path=None) -> None:
    """Raise unless `path` is the pristine debug-2276 XBE.

    Call this before using an image as an equivalence oracle.
    """
    p = Path(path) if path is not None else PRISTINE_XBE
    got = xbe_md5(p)
    if got != PRISTINE_MD5:
        raise ValueError(
            "refusing to use %s as an oracle image: md5 %s != pristine %s. "
            "The oracle must be halo-patched/cachebeta.xbe, not our patched "
            "default.xbe." % (p, got, PRISTINE_MD5))


def as_section(s) -> Section:
    """Normalize any accepted section shape to a `Section`.

    Callers still pass the legacy tuple forms -- 3-tuple `(va, vsize, raw_off)`
    from `check_delinked_bounds`, 4-tuple `(va, vsize, raw_off, raw_size)` from
    `check_fpu_association` -- so every lookup below goes through here.  A
    3-tuple has no `raw_size`; it is taken as `vsize`, which is what those
    callers already assumed and is correct for the code sections they read.
    """
    if isinstance(s, Section):
        return s
    if len(s) >= 4:
        return Section("", s[0], s[1], s[2], s[3])
    return Section("", s[0], s[1], s[2], s[1])


def va_to_off(secs, va: int) -> Optional[int]:
    """File offset for a virtual address, or None if it is in no section."""
    s = section_at(secs, va)
    return None if s is None else s.raw_off + (va - s.va)


def read_va(raw: bytes, secs, va: int, size: int) -> bytes:
    """Bytes at a virtual address, zero-padded where the section is BSS.

    A read that starts inside a section's raw data but runs past `raw_size`
    gets real bytes followed by zeros -- the same thing the Xbox loader
    produces, and the reason a caller must never write `vsize` bytes from a
    section's raw offset.  Returns b"" for an address in no section.
    """
    s = section_at(secs, va)
    if s is None:
        return b""
    delta = va - s.va
    avail = max(0, min(size, s.raw_size - delta))
    out = raw[s.raw_off + delta:s.raw_off + delta + avail] if avail else b""
    want = min(size, s.vsize - delta)
    return out + b"\x00" * (want - len(out))


def read_va_raw(raw: bytes, secs, va: int, size: int) -> bytes:
    """Bytes at a virtual address, clamped to what the FILE actually holds.

    The counterpart to `read_va`: where that one zero-fills a section's BSS
    tail (loader semantics, which is what an emulated oracle needs), this one
    returns a short buffer -- or b"" -- once the raw data runs out.  Audit
    scripts that read strings or disassemble depend on that: a zero-filled
    buffer would decode as a valid empty string or as `add [eax],al`, turning
    "no data here" into a plausible-looking result.
    """
    s = section_at(secs, va)
    if s is None:
        return b""
    delta = va - s.va
    avail = s.raw_size - delta
    if avail <= 0:
        return b""
    return raw[s.raw_off + delta:s.raw_off + delta + min(size, avail)]


#: `{id(secs): (secs, sorted_sections, starts)}`.  The section list is kept in
#: the value so the id can never be recycled onto a different list while the
#: entry is live.  Bounded by the number of distinct section lists a process
#: builds (one, in practice).
_SECTION_INDEX = {}


def _section_index(secs):
    """`(sorted_sections, starts)` for `secs`, or `None` when they overlap.

    `section_at` used to normalize and linear-scan all 24 sections on every
    call, and the harness calls it hundreds of thousands of times per run
    (`_seed_capture_over_bss` alone is 7k addresses per emulator instance).
    Normalizing once and bisecting is the same answer for a non-overlapping
    section table; a table WITH overlaps would make bisect pick the last
    section starting at or before `va` where the scan picks the first one in
    list order, so that case falls back to the scan rather than guessing.
    """
    key = id(secs)
    hit = _SECTION_INDEX.get(key)
    if hit is not None and hit[0] is secs:
        return hit[1]
    norm = sorted((as_section(s) for s in secs), key=lambda s: s.va)
    disjoint = all(a.va + a.vsize <= b.va for a, b in zip(norm, norm[1:]))
    entry = (norm, [s.va for s in norm]) if disjoint else None
    _SECTION_INDEX[key] = (secs, entry)
    return entry


def section_at(secs, va: int) -> Optional[Section]:
    """The section containing `va`, or None.  Accepts every section shape."""
    index = _section_index(secs)
    if index is None:
        for raw_s in secs:
            s = as_section(raw_s)
            if s.va <= va < s.va + s.vsize:
                return s
        return None
    norm, starts = index
    i = bisect.bisect_right(starts, va) - 1
    if i < 0:
        return None
    s = norm[i]
    return s if va < s.va + s.vsize else None


def image_span(secs) -> tuple:
    """Page-aligned `(lo, hi)` covering every section.

    Aligned to `PAGE` (4 KB), not `BIG_PAGE`: `.text` starts at 0x12000, which
    is 4 KB-aligned but not 64 KB-aligned, and rounding down to 0x10000 would
    map the page below it -- making a near-NULL dereference silently succeed
    instead of faulting.
    """
    norm = [as_section(s) for s in secs]
    lo = min(s.va for s in norm)
    hi = max(s.va + s.vsize for s in norm)
    return lo & ~(PAGE - 1), (hi + PAGE - 1) & ~(PAGE - 1)


def image_pages(secs) -> set:
    """The `BIG_PAGE`-granular pages the image span touches.

    Used to record memory reads against the image the same way the harness
    records reads against its own auto-mapped pages (`addr & ~0xFFFF`).
    """
    lo, hi = image_span(secs)
    first = lo & ~(BIG_PAGE - 1)
    last = (hi + BIG_PAGE - 1) & ~(BIG_PAGE - 1)
    return set(range(first, last, BIG_PAGE))


def map_image(uc, raw: bytes, secs) -> tuple:
    """Map the whole image into `uc` at its real VAs.  Returns `(lo, hi)`.

    One coalesced `mem_map` over the page-aligned span, then one `mem_write`
    per section.  Per-section maps are NOT possible: XBE sections are
    0x20-aligned, not page-aligned (`.text` ends 0x1e69cc, `D3D` starts
    0x1e69e0), so 24 separate maps collide.  BSS needs no write -- Unicorn
    zero-fills a fresh mapping.
    """
    import unicorn

    lo, hi = image_span(secs)
    uc.mem_map(lo, hi - lo, unicorn.UC_PROT_ALL)
    for raw_s in secs:
        s = as_section(raw_s)
        if s.raw_size:
            uc.mem_write(s.va, raw[s.raw_off:s.raw_off + s.raw_size])
    return lo, hi


# ---------------------------------------------------------------------------
# Legacy shim: the 3-tuple `(vaddr, vsize, raw_off)` form several callers use.
# ---------------------------------------------------------------------------
def load_xbe_legacy(path=None):
    """`load_xbe` with sections as bare 3-tuples `(va, vsize, raw_off)`."""
    raw, secs = load_xbe(path)
    return raw, [(s.va, s.vsize, s.raw_off) for s in secs]


def load_xbe_legacy4(path=None):
    """`load_xbe` with sections as bare 4-tuples `(va, vsize, raw_off, raw_size)`."""
    raw, secs = load_xbe(path)
    return raw, [(s.va, s.vsize, s.raw_off, s.raw_size) for s in secs]


def _self_test() -> int:
    raw, secs = load_xbe()
    assert_pristine()
    print("sections: %d" % len(secs))
    lo, hi = image_span(secs)
    print("image span: %#x..%#x" % (lo, hi))
    text = section_at(secs, 0x12000)
    assert text is not None and text.name == ".text", text
    assert read_va(raw, secs, 0x12000, 16) == raw[0x2000:0x2010]
    data = section_at(secs, 0x2c84c0)
    assert data is not None and data.name == ".data", data
    assert data.raw_size < data.vsize, "expected a BSS tail in .data"
    bss = data.va + data.raw_size + 0x100
    assert read_va(raw, secs, bss, 32) == b"\x00" * 32, "BSS read not zero"
    assert va_to_off(secs, lo - 1) is None
    assert read_va(raw, secs, 0xF0000000, 4) == b""
    print("md5: %s (pristine)" % xbe_md5())
    print("xbe_image self-test OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
