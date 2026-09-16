#!/usr/bin/env python3
"""Pins on Hazard H10: vacuous early exit detection under `--oracle=xbe`.

WHY
---
Under --oracle=xbe, zero-filled globals or uninitialized engine state in the
synthetic harness can cause functions to take an entry-point NULL guard
directly to ret (<10% coverage floor, or an unexercised branch with no output
variation across all seeds).

Reporting these as 'pass' is false confidence; reporting them as 'inconclusive'
treats them as un-allowlisted errors in batch sweeps. The H10 detector identifies
these runs on the oracle side (all seeds taking the identical visited-PC path
without branch diversity) and emits `not_applicable` (`oracle_vacuous_early_exit`).

Functions with 100% coverage (like `game_state_save_to_persistent_storage`) are
NOT early exits and remain `inconclusive` (`vacuous_output`).

Run:  .venv/bin/python tools/equivalence/test_vacuous_early_exit.py
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
_UD = _HERE / "unicorn_diff.py"
_PY = sys.executable


def _run(target: str, oracle: str = "xbe", seeds: int = 20,
         snapshot: str = None) -> tuple[int, dict]:
    with tempfile.TemporaryDirectory() as td:
        out_json = Path(td) / "out.json"
        cmd = [
            _PY, str(_UD), target,
            f"--oracle={oracle}",
            f"--seeds={seeds}",
            "--allow-stubs",
            "--quiet",
            f"--output-json={out_json}",
        ]
        if snapshot is not None:
            cmd.extend(["--state-snapshot", snapshot])
        proc = subprocess.run(cmd, cwd=str(_ROOT), capture_output=True, text=True)
        data = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() else {}
        return proc.returncode, data


class TestVacuousEarlyExit(unittest.TestCase):
    def test_ai_handle_spatial_effect_is_not_applicable_early_exit(self):
        """ai_handle_spatial_effect (0x425c0) checks ai_globals->active; exits at 2.4% cov."""
        code, data = _run("ai_handle_spatial_effect", "xbe", seeds=20)
        self.assertEqual(code, 2)
        self.assertEqual(data.get("status"), "not_applicable")
        self.assertFalse(data.get("applicable"))
        reason = data.get("reason", "")
        self.assertTrue(reason.startswith("oracle_vacuous_early_exit"), reason)

    def test_game_engine_test_flag_is_not_applicable_early_exit(self):
        """game_engine_test_flag (0xa9fd0) checks game_engine_globals != NULL; exits monotonically."""
        code, data = _run("game_engine_test_flag", "xbe", seeds=20)
        self.assertEqual(code, 2)
        self.assertEqual(data.get("status"), "not_applicable")
        self.assertFalse(data.get("applicable"))
        reason = data.get("reason", "")
        self.assertTrue(reason.startswith("oracle_vacuous_early_exit"), reason)

    def test_full_coverage_remains_inconclusive_not_early_exit(self):
        """game_state_save_to_persistent_storage has 100% coverage; not an early exit."""
        code, data = _run("game_state_save_to_persistent_storage", "xbe", seeds=20)
        self.assertEqual(code, 3)
        self.assertEqual(data.get("status"), "inconclusive")
        self.assertTrue(data.get("applicable"))
        reason = data.get("reason", "")
        self.assertTrue(reason.startswith("vacuous_output"), reason)

    def test_state_snapshot_is_not_mislabeled_as_early_exit(self):
        """A supplied snapshot can make a deterministic path meaningful."""
        snapshot = _ROOT / "tools" / "equivalence" / "regression_snapshots" / \
            "sound_update_channel_attenuation_linear_mid.json"
        code, data = _run("sound_update_channel_attenuation", "xbe", seeds=20,
                          snapshot=str(snapshot))
        self.assertEqual(code, 3)
        self.assertEqual(data.get("status"), "inconclusive")
        self.assertTrue(data.get("applicable"))
        reason = data.get("reason", "")
        self.assertTrue(reason.startswith("vacuous_output"), reason)
        self.assertNotIn("oracle_vacuous_early_exit", reason)


if __name__ == "__main__":
    unittest.main()
