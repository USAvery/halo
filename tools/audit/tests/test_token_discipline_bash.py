#!/usr/bin/env python3
"""Unit tests for the token-discipline hook's Bash command parser.

unittest, not pytest: pytest is installed neither on the dev box nor in CI,
and the CI step runs `unittest discover`, which collects TestCase classes
only.  Run with:

    python3 -m unittest discover -s tools/audit/tests -p 'test_*.py' -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import token_discipline_hook as tdh

HOOK = Path(tdh.__file__)


def _parse(cmd):
    return tdh.parse_bash_command(cmd)


class RtkReadTest(unittest.TestCase):
    def test_plain_rtk_read(self):
        self.assertEqual(_parse("rtk read src/objects.c")["reads"],
                         [("src/objects.c", 0, 0)])

    def test_rtk_read_with_range(self):
        self.assertEqual(
            _parse("rtk read src/units.c -o 120 -l 40")["reads"],
            [("src/units.c", 120, 40)])

    def test_bare_read_without_rtk(self):
        self.assertEqual(_parse("read src/units.c -o 5 -l 5")["reads"],
                         [("src/units.c", 5, 5)])


class SedTest(unittest.TestCase):
    def test_sed_line_range(self):
        self.assertEqual(_parse("sed -n '10,29p' src/types.h")["reads"],
                         [("src/types.h", 10, 20)])

    def test_sed_single_line(self):
        self.assertEqual(_parse("sed -n '42p' src/types.h")["reads"],
                         [("src/types.h", 42, 1)])

    def test_sed_non_print_script_is_whole_file(self):
        self.assertEqual(_parse("sed -n 's/a/b/' src/types.h")["reads"],
                         [("src/types.h", 0, 0)])


class CatHeadTailTest(unittest.TestCase):
    def test_cat_single(self):
        self.assertEqual(_parse("cat kb.json")["reads"], [("kb.json", 0, 0)])

    def test_cat_multiple_paths(self):
        self.assertEqual(_parse("cat a.c b.c")["reads"],
                         [("a.c", 0, 0), ("b.c", 0, 0)])

    def test_head_with_count(self):
        self.assertEqual(_parse("head -n 30 src/foo.c")["reads"],
                         [("src/foo.c", 0, 30)])

    def test_tail_dash_number_form(self):
        self.assertEqual(_parse("tail -20 src/foo.c")["reads"],
                         [("src/foo.c", 0, 20)])

    def test_head_in_pipe_without_path_is_not_a_read(self):
        out = _parse("rg foo src/ | head -30")
        self.assertEqual(out["reads"], [])
        self.assertEqual(out["searches"], 1)


class SearchTest(unittest.TestCase):
    def test_rtk_grep_counts_as_search_not_read(self):
        out = _parse("rtk grep object_damage src/")
        self.assertEqual(out["reads"], [])
        self.assertEqual(out["searches"], 1)

    def test_rg_and_grep(self):
        out = _parse("rg foo src/ && grep -rn bar src/")
        self.assertEqual(out["searches"], 2)
        self.assertEqual(out["reads"], [])


class IgnoredCommandTest(unittest.TestCase):
    def test_build_is_ignored(self):
        self.assertEqual(_parse("rtk python3 tools/build/build.py -q"),
                         {"reads": [], "searches": 0})

    def test_git_is_ignored(self):
        self.assertEqual(_parse("rtk git diff -- src/ kb.json"),
                         {"reads": [], "searches": 0})

    def test_git_grep_is_not_a_search(self):
        self.assertEqual(_parse("git grep foo")["searches"], 0)

    def test_tests_are_ignored(self):
        self.assertEqual(_parse("rtk pytest tools/audit/tests"),
                         {"reads": [], "searches": 0})

    def test_empty(self):
        self.assertEqual(_parse(""), {"reads": [], "searches": 0})


class HeredocTest(unittest.TestCase):
    def test_cat_heredoc_body_is_not_parsed_as_paths(self):
        out = _parse(
            "cat <<'EOF' > docs/notes.md\n"
            "NEW section on the raw XBE oracle and how to break early\n"
            "rtk proxy is used to bypass the filter\n"
            "EOF"
        )
        self.assertEqual(out["reads"], [])
        self.assertEqual(out["searches"], 0)

    def test_command_after_heredoc_still_parses(self):
        out = _parse(
            "cat <<'EOF' > docs/notes.md\n"
            "body text here\n"
            "EOF\n"
            "sed -n '1,10p' src/foo.c"
        )
        self.assertEqual(out["reads"], [("src/foo.c", 1, 10)])

    def test_unquoted_delimiter_heredoc(self):
        out = _parse("cat <<EOF\nsome words\nEOF")
        self.assertEqual(out["reads"], [])

    def test_dash_heredoc_allows_indented_delimiter(self):
        out = _parse("cat <<-EOF\n  indented body\n  EOF")
        self.assertEqual(out["reads"], [])

    def test_heredoc_output_redirect_target_is_not_a_read(self):
        # `> file` after the heredoc introducer is the file being WRITTEN.
        out = _parse("cat <<'EOF' > docs/notes.md\nbody text\nEOF")
        self.assertEqual(out["reads"], [])


class CompoundTest(unittest.TestCase):
    def test_mixed_segments(self):
        out = _parse("rtk grep foo src/ && sed -n '1,10p' src/foo.c "
                     "&& rtk git status")
        self.assertEqual(out["searches"], 1)
        self.assertEqual(out["reads"], [("src/foo.c", 1, 10)])

    def test_semicolon_inside_quotes_does_not_split(self):
        out = _parse("sed -n '5,6p' 'src/a;b.c'")
        self.assertEqual(out["reads"], [("src/a;b.c", 5, 2)])


class StateIntegrationTest(unittest.TestCase):
    """End-to-end through the hook process, incl. the JSON state schema."""

    def _run(self, payload, state_dir):
        env = dict(os.environ)
        return subprocess.run(
            [sys.executable, str(HOOK)], input=json.dumps(payload),
            capture_output=True, text=True, env=env,
        )

    def test_bash_read_credits_counters_and_repeat_warns(self):
        with tempfile.TemporaryDirectory() as td:
            orig = tdh.STATE_DIR
            try:
                tdh.STATE_DIR = Path(td)
                state = tdh._load_state("s1")
                msgs = tdh._record_bash(state, "sed -n '1,20p' src/foo.c")
                self.assertEqual(msgs, [])
                self.assertEqual(state["reads"], 1)
                self.assertEqual(state["bash_reads"], 1)
                msgs = tdh._record_bash(state, "sed -n '1,20p' src/foo.c")
                self.assertEqual(len(msgs), 1)
                self.assertIn("re-reading", msgs[0])
                self.assertEqual(state["repeat_reads"], 1)
                tdh._record_bash(state, "rtk grep foo src/")
                self.assertEqual(state["searches"], 1)
                self.assertEqual(state["reads"], 2)  # grep is not a read
            finally:
                tdh.STATE_DIR = orig

    def test_cat_of_kb_json_is_noisy(self):
        state = tdh._load_state("s2")
        msgs = tdh._record_bash(state, "cat kb.json")
        self.assertEqual(len(msgs), 1)
        self.assertIn("kb.json", msgs[0])
        self.assertEqual(state["noisy_reads"], 1)

    def test_cat_of_build_dir_is_noisy(self):
        state = tdh._load_state("s3")
        msgs = tdh._record_bash(state, "cat build/generated/decl.h")
        self.assertEqual(state["noisy_reads"], 0)  # relative path, no /build/
        state = tdh._load_state("s3")
        msgs = tdh._record_bash(state, "cat /mnt/g/dev/halo/build/x.log")
        self.assertEqual(state["noisy_reads"], 1)
        self.assertTrue(msgs)

    def test_old_state_file_without_new_keys_loads(self):
        with tempfile.TemporaryDirectory() as td:
            orig = tdh.STATE_DIR
            try:
                tdh.STATE_DIR = Path(td)
                legacy = {
                    "session_id": "old", "started": "2026-01-01T00:00:00",
                    "reads": 3, "noisy_reads": 0, "edits": 1, "writes": 0,
                    "repeat_reads": 0, "ranges": {}, "files_read": {},
                    "last_event": "", "last_ratio_warn_at": 0,
                }
                (Path(td) / "old.json").write_text(json.dumps(legacy))
                state = tdh._load_state("old")
                self.assertEqual(state["reads"], 3)
                self.assertEqual(state["bash_reads"], 0)
                self.assertEqual(state["searches"], 0)
                # summary must not KeyError on a legacy state
                self.assertIn("via bash", tdh._summary(state))
            finally:
                tdh.STATE_DIR = orig


class HookProcessTest(unittest.TestCase):
    def test_posttooluse_bash_event_is_accepted(self):
        payload = {
            "session_id": "unittest-bash-probe",
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "sed -n '1,20p' src/objects.c"},
        }
        r = subprocess.run([sys.executable, str(HOOK)],
                           input=json.dumps(payload),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
