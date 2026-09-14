#!/usr/bin/env python3
"""Pins the raw-XBE oracle's replacements for the three reloc-derived gates.

WHY
---
Under `--oracle=xbe` the oracle side is a VA range of the pristine
`cachebeta.xbe`, so its `FunctionSlice.relocs` is empty BY CONSTRUCTION.  Three
gates in `unicorn_diff` read leaf-ness, stubbability and reference coverage off
that list, and an empty list reads to all three as "self-contained, complete"
(hazard H1/H2 in docs/raw-xbe-oracle-migration.md).  That would flip the leaf
gate, suppress the oracle's stub sentinels, and let the Z3 proof run over code
containing CALL -- silently, and in the direction that passes.

So each gate gets an explicit replacement, and this suite pins them:

  `_check_relocations(..., expect_relocs=False)`  refuses to answer
  `_classify_raw_oracle(code, va)`               counts by disassembly
  `_oracle_bound_unreliable(addr)`               coverage, from the bounds table
  `_z3_gate_reason(...)`                         the re-gated proof

The central agreement test compares `_classify_raw_oracle` against the
delinked-derived `classify_relocations` over every function in
`delinked/game_state.obj`, which is the only whole-object delink left on this
host.  `delinked/` is gitignored, so those cases SKIP where it is absent; the
rest of the suite does not need it.

Run:  .venv/bin/python tools/equivalence/test_raw_oracle_classify.py
"""
import re
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for _p in (str(_HERE), str(_ROOT / "tools"), str(_ROOT / "tools" / "verify"),
           str(_ROOT / "tools" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import unicorn_diff as ud                                  # noqa: E402
from coff_loader import (FunctionSlice, extract_function,   # noqa: E402
                         list_functions, slice_looks_truncated)

_OBJ = _ROOT / "delinked" / "game_state.obj"
# Every symbol in the object lands in one contiguous exported VA range, which is
# what lets a delinked call be resolved internally with no relocation at all.
# A little slack past the last symbol covers that symbol's own body.
_SPAN_SLACK = 0x400


def _kb_name_to_addr():
    kb = ud._load_kb()
    out = {}
    for obj in kb.get("objects", []):
        for fn in obj.get("functions", []):
            m = re.search(r"\b(\w+)\s*\(", fn.get("decl", ""))
            if m and fn.get("addr"):
                try:
                    out[m.group(1)] = int(fn["addr"], 16)
                except ValueError:
                    pass
    return out


def _addr_for(sym, name_to_addr):
    canon = sym.lstrip("_")
    a = name_to_addr.get(canon) or name_to_addr.get(sym)
    if a is not None:
        return canon, a
    m = re.match(r"FUN_0*([0-9a-fA-F]+)$", canon)
    return canon, (int(m.group(1), 16) if m else None)


def _direct_branch_targets(code, va):
    """Targets of direct CALL/JMP/Jcc leaving [va, va+len(code))."""
    import capstone
    from capstone import x86 as cs_x86
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    end = va + len(code)
    out = []
    for insn in md.disasm(code, va):
        if not (insn.mnemonic == "call" or insn.mnemonic.startswith("j")):
            continue
        for op in insn.operands:
            if op.type == cs_x86.X86_OP_IMM:
                t = op.imm & 0xFFFFFFFF
                if not (va <= t < end):
                    out.append(t)
    return out


class TestAgreementWithDelinked(unittest.TestCase):
    """`_classify_raw_oracle` vs `classify_relocations` over game_state.obj."""

    @classmethod
    def setUpClass(cls):
        if not _OBJ.exists():
            raise unittest.SkipTest(f"{_OBJ} absent (delinked/ is gitignored)")
        from stubs import classify_relocations
        name_to_addr = _kb_name_to_addr()
        cls.cases = []
        addrs = []
        for sym in list_functions(str(_OBJ)):
            canon, addr = _addr_for(sym, name_to_addr)
            if addr is None:
                continue
            addrs.append(addr)
            dl = extract_function(str(_OBJ), sym)
            xbe, err = ud._xbe_oracle_slice(addr, canon)
            cls.cases.append({
                "name": canon, "addr": addr, "delinked": dl,
                "delinked_truncated": slice_looks_truncated(dl),
                "xbe": xbe, "xbe_err": err,
                "dl_class": classify_relocations(dl.relocs, dl.defined_symbols),
                "raw_class": (ud._classify_raw_oracle(xbe.code, addr)
                              if xbe is not None else None),
            })
        cls.export_span = (min(addrs), max(addrs) + _SPAN_SLACK) if addrs else (0, 0)
        # Cases where a comparison is meaningful at all: both references exist
        # and the delinked one actually covers its function.
        cls.comparable = [c for c in cls.cases
                          if c["xbe"] is not None and not c["delinked_truncated"]]

    def test_the_object_still_holds_the_twenty_or_so_functions(self):
        self.assertGreaterEqual(len(self.cases), 20,
                                "game_state.obj shrank; the agreement test "
                                "below is only as good as its corpus")

    def test_every_function_has_xbe_bytes(self):
        for c in self.cases:
            with self.subTest(fn=c["name"]):
                self.assertIsNotNone(c["xbe"], c["xbe_err"])

    def test_dir32_counts_agree_exactly(self):
        """The data-reference count is the one that must match on the nose: the
        raw scan finds the same SITES the delinked relocation table lists."""
        for c in self.comparable:
            with self.subTest(fn=c["name"]):
                self.assertEqual(
                    c["raw_class"].dir32_count, c["dl_class"].dir32_count,
                    f"{c['name']} ({c['addr']:#x}): raw found "
                    f"{c['raw_class'].dir32_count} absolute data reference(s), "
                    f"delinked relocations list {c['dl_class'].dir32_count}")

    def test_call_counts_agree_up_to_internally_resolved_siblings(self):
        """`classify_relocations` cannot see a call the delinked object resolved
        ITSELF -- a plain E8 with a correct in-object displacement and no
        relocation, which is why `_has_raw_calls`/`_redirect_raw_calls` exist.
        So the raw count is >= the delinked count, and every surplus call must
        land inside the object's own exported VA range."""
        lo, hi = self.export_span
        for c in self.comparable:
            with self.subTest(fn=c["name"]):
                raw, dl = c["raw_class"].call_count, c["dl_class"].call_count
                self.assertGreaterEqual(
                    raw, dl,
                    f"{c['name']}: raw classifier found FEWER calls ({raw}) "
                    f"than the relocation table ({dl}) -- it is missing a call "
                    f"site, which is the direction that silently passes")
                surplus = raw - dl
                if not surplus:
                    continue
                internal = [t for t in _direct_branch_targets(c["xbe"].code,
                                                              c["addr"])
                            if lo <= t < hi]
                self.assertLessEqual(
                    surplus, len(internal),
                    f"{c['name']}: {surplus} surplus call(s) but only "
                    f"{len(internal)} target(s) inside the exported range "
                    f"[{lo:#x},{hi:#x}) -- the surplus is not explained by "
                    f"internally-resolved sibling calls")

    def test_the_two_classifiers_reach_the_same_category(self):
        """A category disagreement would route the run down a different path
        (leaf vs stubbable), which is the decision H1 is about."""
        for c in self.comparable:
            with self.subTest(fn=c["name"]):
                self.assertEqual(c["raw_class"].category,
                                 c["dl_class"].category,
                                 f"{c['name']}: raw says "
                                 f"{c['raw_class'].category!r} "
                                 f"({c['raw_class'].reason}), delinked says "
                                 f"{c['dl_class'].category!r} "
                                 f"({c['dl_class'].reason})")

    def test_raw_oracle_never_reports_intra_object_calls(self):
        """There is no object in a raw image, so the field is deliberately 0
        and `call_count` carries every out-of-body call instead."""
        for c in self.cases:
            if c["raw_class"] is None:
                continue
            with self.subTest(fn=c["name"]):
                self.assertEqual(c["raw_class"].intra_obj_calls, 0)


class TestOracleSliceShape(unittest.TestCase):
    ADDR = 0x1BF7C0          # FUN_001bf7c0, a bounded stubbable body

    def test_the_slice_is_marked_as_raw(self):
        sl, err = ud._xbe_oracle_slice(self.ADDR, "FUN_001bf7c0")
        self.assertIsNone(err)
        self.assertIsInstance(sl, FunctionSlice)
        self.assertEqual(sl.real_va, self.ADDR)
        self.assertEqual(sl.relocs, [],
                         "a raw slice must carry NO relocations; a non-empty "
                         "list here means something synthesized them again")
        self.assertEqual(sl.bound_provenance, "table")
        self.assertEqual(sl.bound_kind, "auto")

    def test_a_coff_slice_leaves_the_raw_fields_unset(self):
        """`real_va` is the discriminator every consumer keys off, so a COFF
        slice must never accidentally set it."""
        sl = FunctionSlice(name="x", raw_name="x", code=b"\xc3", relocs=[])
        self.assertIsNone(sl.real_va)
        self.assertIsNone(sl.bound_kind)
        self.assertIsNone(sl.bound_provenance)

    def test_the_bytes_match_the_bounds_table_span(self):
        import xbe_reference
        ext = xbe_reference.function_extent(self.ADDR)
        self.assertIsNotNone(ext)
        sl, _ = ud._xbe_oracle_slice(self.ADDR, "FUN_001bf7c0")
        self.assertEqual(len(sl.code), ext[0] - self.ADDR)

    def test_an_unmapped_address_is_reported_not_raised(self):
        sl, err = ud._xbe_oracle_slice(0x7FFF0000, "nope")
        self.assertIsNone(sl)
        self.assertTrue(err)


class TestBoundReliability(unittest.TestCase):
    """`_oracle_bound_unreliable` is H2's replacement for
    `slice_looks_truncated`: it answers "does the reference cover the function"
    from the committed bounds table instead of from `reached_section_end`,
    which a raw slice never sets."""

    def test_a_bounded_function_is_accepted(self):
        self.assertIsNone(ud._oracle_bound_unreliable(0x1BF7C0))

    def test_a_one_byte_ret_is_accepted(self):
        """0x1bf760 really is a lone `ret`; the delinked export gives it 38
        bytes by swallowing alignment padding and two following import thunks.
        The raw bound is the more accurate of the two, so it must not be
        rejected for being short."""
        sl, err = ud._xbe_oracle_slice(0x1BF760, "FUN_001bf760")
        self.assertIsNone(err)
        self.assertEqual(sl.code, b"\xc3")
        self.assertIsNone(ud._oracle_bound_unreliable(0x1BF760))

    def test_an_address_absent_from_the_table_is_rejected(self):
        """A run-time-computed bound is a per-run heuristic, not something a
        reviewer can argue with in a diff.  0x1bfbd0 is such an address today;
        if it gains a committed entry, pick another or drop this pin."""
        import xbe_reference
        addr = 0x1BFBD0
        if xbe_reference.bounds_entry(addr) is not None:
            self.skipTest(f"{addr:#x} now has a committed bound")
        reason = ud._oracle_bound_unreliable(addr)
        self.assertIsNotNone(reason)
        self.assertIn("computed at run time", reason)

    def test_an_unmapped_address_is_rejected(self):
        reason = ud._oracle_bound_unreliable(0x7FFF0000)
        self.assertIsNotNone(reason)

    def test_a_non_terminating_range_is_rejected(self):
        """The one check carried over verbatim from `slice_looks_truncated`:
        bytes that do not end on RET/JMP/INT3 are not a whole body."""
        import xbe_reference
        real = xbe_reference.function_bytes

        def fake(addr):
            return b"\x55\x8b\xec", None      # push ebp; mov ebp, esp

        xbe_reference.function_bytes = fake
        try:
            reason = ud._oracle_bound_unreliable(0x1BF7C0)
        finally:
            xbe_reference.function_bytes = real
        self.assertIsNotNone(reason)
        self.assertIn("not a terminating instruction", reason)

    def test_table_data_and_no_terminator_kinds_are_rejected(self):
        """Both kinds exist in the committed table (3 and 7 entries today) and
        both mean the end address is not a real function tail."""
        import xbe_reference
        real = xbe_reference.function_extent
        for kind in ("table_data", "no_terminator"):
            xbe_reference.function_extent = (
                lambda a, _k=kind: (a + 0x10, _k, "table"))
            try:
                reason = ud._oracle_bound_unreliable(0x1BF7C0)
            finally:
                xbe_reference.function_extent = real
            with self.subTest(kind=kind):
                self.assertIsNotNone(reason)
                self.assertIn(kind, reason)


class TestCheckRelocationsRefusesRawSlices(unittest.TestCase):
    def test_an_empty_coff_reloc_table_still_means_leaf(self):
        sl = FunctionSlice(name="x", raw_name="x", code=b"\xc3", relocs=[])
        self.assertTrue(_quiet_check(sl, expect_relocs=True))

    def test_an_empty_raw_reloc_table_does_not(self):
        """This is hazard H1 in one assertion: the same empty list must not
        answer the same question when it was never populated."""
        sl = FunctionSlice(name="x", raw_name="x", code=b"\xc3", relocs=[])
        self.assertFalse(_quiet_check(sl, expect_relocs=False))

    def test_the_call_sites_derive_the_flag_from_real_va(self):
        """Nothing threads an oracle-mode flag down to the gate; `real_va` being
        set IS the signal, so a delinked run is untouched."""
        src = (_HERE / "unicorn_diff.py").read_text(encoding="utf-8")
        self.assertIn('_oracle_va = getattr(oracle_slice, "real_va", None)', src)
        self.assertRegex(
            src, r"if _oracle_va is None:\s*\n\s*oracle_ok = _check_relocations\("
                 r"oracle_slice, \"oracle\", quiet=quiet,\s*\n\s*"
                 r"expect_relocs=True\)")
        self.assertIn("oracle_raw_class = _classify_raw_oracle(", src)


def _quiet_check(sl, expect_relocs):
    return ud._check_relocations(sl, "test", quiet=True,
                                 expect_relocs=expect_relocs)


class TestZ3Regate(unittest.TestCase):
    def test_a_delinked_run_is_not_re_gated(self):
        lft = FunctionSlice(name="x", raw_name="x", code=b"\xc3", relocs=[])
        self.assertIsNone(ud._z3_gate_reason(None, lft, None))

    def test_a_non_leaf_raw_oracle_blocks_the_proof(self):
        from stubs import RelocClassification
        lft = FunctionSlice(name="x", raw_name="x", code=b"\xc3", relocs=[])
        cls = RelocClassification("stubbable", 0, 2, 0, [], "2 external call(s)")
        reason = ud._z3_gate_reason(None, lft, cls)
        self.assertIsNotNone(reason)
        self.assertIn("not leaf", reason)

    def test_candidate_dir32_blocks_the_proof(self):
        from stubs import RelocClassification, IMAGE_REL_I386_DIR32
        from coff_loader import CoffReloc
        cls = RelocClassification("leaf", 0, 0, 0, [], "leaf")
        lft = FunctionSlice(
            name="x", raw_name="x", code=b"\xc3",
            relocs=[CoffReloc(virtual_address=1, symbol_name="DAT_0",
                              reloc_type=IMAGE_REL_I386_DIR32)])
        reason = ud._z3_gate_reason(None, lft, cls)
        self.assertIsNotNone(reason)
        self.assertIn("DIR32", reason)

    def test_a_clean_raw_leaf_is_allowed(self):
        from stubs import RelocClassification
        cls = RelocClassification("leaf", 0, 0, 0, [], "leaf")
        lft = FunctionSlice(name="x", raw_name="x", code=b"\xc3", relocs=[])
        self.assertIsNone(ud._z3_gate_reason(None, lft, cls))


class TestClassifierOperandRules(unittest.TestCase):
    """Pins the two places the raw classifier has to make a judgement call."""

    VA = 0x1BF7C0        # inside .text, so the assembled snippets are plausible

    def test_a_base_relative_displacement_is_a_struct_field(self):
        # mov eax, [esi + 0x2c8] ; ret
        code = b"\x8b\x86\xc8\x02\x00\x00\xc3"
        self.assertEqual(ud._classify_raw_oracle(code, self.VA).dir32_count, 0)

    def test_an_absolute_displacement_is_a_global(self):
        # mov eax, [0x4ea9ac] ; ret
        code = b"\xa1\xac\xa9\x4e\x00\xc3"
        self.assertEqual(ud._classify_raw_oracle(code, self.VA).dir32_count, 1)

    def test_a_size_immediate_inside_text_is_not_an_address(self):
        """`cmp eax, 0x40000` is 256 KB, and .text starts at 0x12000, so the
        constant lands in the image span by arithmetic accident.  Two
        game_state functions were mis-classified on exactly this."""
        # cmp eax, 0x40000 ; ret
        code = b"\x3d\x00\x00\x04\x00\xc3"
        self.assertEqual(ud._classify_raw_oracle(code, self.VA).dir32_count, 0)

    def test_a_function_pointer_immediate_is_an_address(self):
        """The same form IS an address when it names a kb.json entry."""
        target = 0x1BF7C0
        self.assertIn(target, ud._kb_func_addr_set())
        code = b"\x68" + target.to_bytes(4, "little") + b"\xc3"   # push; ret
        self.assertEqual(ud._classify_raw_oracle(code, 0x100000).dir32_count, 1)

    def test_a_local_branch_is_not_a_call(self):
        # jmp +0 ; ret  (target is the next instruction, inside the body)
        code = b"\xeb\x00\xc3"
        cls = ud._classify_raw_oracle(code, self.VA)
        self.assertEqual(cls.call_count, 0)
        self.assertEqual(cls.category, "leaf")

    def test_an_undecodable_tail_is_reported(self):
        code = b"\xc3\xff\xff\xff\xff\xff\xff"
        cls = ud._classify_raw_oracle(code, self.VA)
        self.assertTrue(any("undecoded tail" in e for e in cls.external_symbols),
                        f"externals were {cls.external_symbols}")


class TestRunFunctionImagePlumbing(unittest.TestCase):
    """Source-level pins on the four `_run_function` hazards.  Exercising them
    end to end needs the `--oracle` flag, which lands in step 4; what must not
    regress silently in the meantime is that the guards are there at all."""

    @classmethod
    def setUpClass(cls):
        cls.src = (_HERE / "unicorn_diff.py").read_text(encoding="utf-8")

    def test_image_and_entry_va_are_accepted(self):
        import inspect
        sig = inspect.signature(ud._run_function)
        for p in ("image", "entry_va", "native_callee_ranges"):
            self.assertIn(p, sig.parameters)
            self.assertIsNone(sig.parameters[p].default)

    def test_entry_va_is_required_with_image(self):
        self.assertIn('raise ValueError("_run_function(image=...) requires '
                      'entry_va")', self.src)

    def test_code_base_is_not_mapped_under_the_image(self):
        """H4's premise: the FXSAVE stub had to move off CODE_BASE because the
        raw oracle stops mapping it."""
        self.assertRegex(
            self.src,
            r"_image_lo, _image_hi = xbe_image\.map_image\(uc, _img_raw, "
            r"_img_secs\)(?:.|\n)*?else:\s*\n\s*uc\.mem_map\(CODE_BASE, "
            r"CODE_SIZE\)")
        self.assertIn("fxsave_stub_addr = TRAMP_BASE", self.src)

    def test_both_pre_map_passes_skip_the_image(self):
        """H3.  The second pass scans 0x000100..0x01FFFFFF, which CONTAINS the
        image span, so without the guard it stamps `31 C0 C3` over real code."""
        self.assertEqual(
            self.src.count("if _image_lo is not None and _image_lo <= "), 2)
        self.assertIn("0x000100 <= _ptr <= 0x01FFFFFF", self.src)

    def test_the_eip_domain_guard_exists_and_is_image_only(self):
        """H5."""
        self.assertIn("_eip_guard = [image is not None]", self.src)
        self.assertIn("oracle_escaped eip=", self.src)
        self.assertRegex(self.src,
                         r"if _eip_guard\[0\] and not \(_eip_lo <= address < "
                         r"_eip_hi\):")

    def test_the_eip_guard_is_disarmed_for_the_fxsave_capture(self):
        self.assertRegex(
            self.src,
            r"_eip_guard\[0\] = False[^\n]*\n\s*fxsave_stub_addr = TRAMP_BASE")

    def test_global_reads_are_recorded_against_image_pages_too(self):
        """H6.  Image pages are pre-mapped, so the auto-mapped-only filter
        would leave concolic Phase 2 with no recorded inputs."""
        self.assertIn("if page in _mapped_regions or page in _image_pages:",
                      self.src)
        self.assertIn("xbe_image.image_pages(_img_secs)", self.src)


class TestOracleModeIsWired(unittest.TestCase):
    """Step 4 made the step-3 plumbing reachable.  What this class pins is that
    it is reachable ONLY through `oracle="xbe"`, so a delinked run cannot
    accidentally take the new path."""

    @classmethod
    def setUpClass(cls):
        src = (_HERE / "unicorn_diff.py").read_text(encoding="utf-8")
        cls.body = src[src.index("def run_diff("):]

    def test_run_diff_takes_an_oracle_mode_defaulting_to_delinked(self):
        import inspect
        sig = inspect.signature(ud.run_diff)
        self.assertIn("oracle", sig.parameters)
        self.assertEqual(sig.parameters["oracle"].default, "delinked")

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            ud.run_diff("whatever", oracle="ghidra")

    def test_every_new_path_is_behind_the_flag(self):
        for needle in ("_xbe_oracle_slice(addr_int, func_name)",
                       "_oracle_bound_unreliable(addr_int)",
                       "image=oracle_image"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.body)
        # `oracle_image` is the only thing handed to _run_function as `image`,
        # and it stays None unless the flag is set.
        self.assertIn("oracle_image = None", self.body)
        self.assertRegex(self.body,
                         r"if _oracle_xbe:\s*\n\s*import xbe_image\s*\n\s*"
                         r"xbe_image\.assert_pristine\(\)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
