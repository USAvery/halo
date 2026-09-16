#!/usr/bin/env python3
"""Regression runner test for per-target child-process environment overrides."""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import regression_test


class TestRegressionTargetEnvironment(unittest.TestCase):
    def test_target_env_merges_without_dropping_parent_variables(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            if command[0] == "git":
                return SimpleNamespace(stdout="", stderr="", returncode=0)
            return SimpleNamespace(
                stdout="RESULTS: 1 seeds, 0 failed, 0 errors\n",
                stderr="",
                returncode=0,
            )

        target = {
            "name": "synthetic_env_target",
            "seeds": 1,
            "env": {
                "REGRESSION_TARGET_ONLY": "target-value",
            },
        }
        with mock.patch.dict(
                regression_test.os.environ,
                {"REGRESSION_PARENT_SENTINEL": "parent-value"},
                clear=True):
            with mock.patch.object(
                    regression_test.subprocess, "run", side_effect=fake_run):
                status, _detail = regression_test.run_target(target)

        self.assertEqual(status, "pass")
        child_env = calls[-1][1]["env"]
        self.assertEqual(child_env["REGRESSION_PARENT_SENTINEL"], "parent-value")
        self.assertEqual(child_env["REGRESSION_TARGET_ONLY"], "target-value")


if __name__ == "__main__":
    unittest.main(verbosity=2)
