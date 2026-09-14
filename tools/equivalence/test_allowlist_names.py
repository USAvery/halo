#!/usr/bin/env python3
"""Pins that every allowlist row names a function that exists.

WHY
---
`batch_verify_allowlist.json` excuses a target by NAME, and `batch_verify`
discovers targets by the name kb.json gives them.  When a function is renamed
in kb.json, the allowlist row keeps the old spelling and stops matching
anything.  Nothing fails: the row goes inert, the function is tested with no
excuse, and the allowlist grows a dead entry that still reads as an excuse to
anyone auditing the file.

The raw-XBE oracle retry sweep made the damage visible.  Ten rows came back
`missing_kb_entry` -- not "the reference crashed" as the row claimed, but "this
name does not exist".  A wider audit found 16 in total across four categories,
every one of them `ported: true` under a different name:

    FUN_000425c0  ->  ai_handle_spatial_effect
    FUN_00114630  ->  inflate_blocks_free
    FUN_0019a490  ->  file_create
    ...

One went the other way.  `ai_profile_change_render_spray` is a real PDB symbol,
but it belongs to the real ai_profile.c near 0x536xx, so
`src/halo/ai/ai_profile.c:608-616` deliberately reverted 0x54a80 to
`FUN_00054a80` rather than keep a plausibly-wrong name.  The allowlist kept the
withdrawn name.

Both directions are the same defect: the allowlist and kb.json drifted apart
with no gate between them.  This is that gate.

Run:  python3 tools/equivalence/test_allowlist_names.py
"""
import json
import re
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent

ALLOWLIST = _HERE / "batch_verify_allowlist.json"
KB_JSON = _ROOT / "kb.json"

_DECL_NAME = re.compile(r"([A-Za-z_]\w*)\s*\(")
_FUN_NAME = re.compile(r"^FUN_([0-9a-fA-F]{8})$")


def _kb_index():
    """(name -> addr, addr -> name) over every kb.json entry with a decl.

    Keyed off `decl` rather than `name` because a good many entries carry no
    `name` field at all -- the decl is the authority on what the function is
    called.
    """
    kb = json.loads(KB_JSON.read_text(encoding="utf-8"))
    by_name, by_addr = {}, {}

    def walk(node):
        if isinstance(node, dict):
            addr, decl = node.get("addr"), node.get("decl")
            if isinstance(addr, str) and isinstance(decl, str):
                m = _DECL_NAME.search(decl)
                if m:
                    try:
                        a = hex(int(addr, 16))
                    except ValueError:
                        a = None
                    if a is not None:
                        by_name[m.group(1)] = a
                        by_addr[a] = m.group(1)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(kb)
    return by_name, by_addr


class TestAllowlistNamesResolve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = json.loads(
            ALLOWLIST.read_text(encoding="utf-8"))["targets"]
        cls.by_name, cls.by_addr = _kb_index()

    def test_every_row_names_a_function_kb_json_knows(self):
        """An unresolvable row excuses nothing.  It is worse than no row: it
        reads as an excuse while the function is tested without one."""
        missing = sorted(n for n in self.targets if n not in self.by_name)
        self.assertEqual(missing, [], (
            "these allowlist rows name no kb.json function -- they are inert "
            "and must be renamed to the current name or deleted"))

    def test_no_row_uses_a_withdrawn_fun_name(self):
        """The exact defect the retry sweep found: the row says FUN_000425c0
        and kb.json says that address is `ai_handle_spatial_effect`."""
        stale = []
        for name in sorted(self.targets):
            m = _FUN_NAME.match(name)
            if not m:
                continue
            addr = hex(int(m.group(1), 16))
            current = self.by_addr.get(addr)
            if current is not None and current != name:
                stale.append("%s -> %s" % (name, current))
        self.assertEqual(stale, [], (
            "these rows use a FUN_ name whose address carries a real name in "
            "kb.json; rename the row in the same commit as the kb.json rename"))

    def test_no_row_names_a_function_at_a_different_address(self):
        """A row whose name resolves, but to an address whose kb.json name is
        something else, means two entries claim one name."""
        conflicts = []
        for name in sorted(self.targets):
            addr = self.by_name.get(name)
            if addr is None:
                continue
            back = self.by_addr.get(addr)
            if back is not None and back != name:
                conflicts.append("%s @ %s reads back as %s" % (name, addr, back))
        self.assertEqual(conflicts, [])


class TestAllowlistRowShape(unittest.TestCase):
    """An excuse with no written reason cannot be reviewed, and an
    un-reviewable excuse is how a real failure gets muted for good."""

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
        cls.targets = cls.doc["targets"]

    def test_every_row_carries_statuses_and_reasons(self):
        for name, row in sorted(self.targets.items()):
            with self.subTest(target=name):
                self.assertIsInstance(row, dict)
                self.assertIsInstance(row.get("statuses"), list)
                self.assertTrue(row["statuses"])
                self.assertIsInstance(row.get("reasons"), list)
                self.assertTrue(row["reasons"])

    def test_every_reason_says_something(self):
        for name, row in sorted(self.targets.items()):
            for reason in row.get("reasons", []):
                with self.subTest(target=name):
                    self.assertIsInstance(reason, str)
                    self.assertGreater(
                        len(reason.strip()), 20,
                        "a reason must name the failure mode, not just assert "
                        "that one exists")

    def test_an_oracle_scope_names_a_real_oracle(self):
        """Step 9 scopes a delinked-only excuse with an `oracle` key.  A typo
        there silently widens the excuse back to every lane."""
        for name, row in sorted(self.targets.items()):
            scope = row.get("oracle")
            if scope is None:
                continue
            with self.subTest(target=name):
                self.assertIn(scope, ("xbe", "delinked"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
