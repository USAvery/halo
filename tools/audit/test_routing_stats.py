#!/usr/bin/env python3
"""Tests for routing_stats.py ledger discovery and aggregation."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import routing_stats  # noqa: E402


class RoutingStatsTests(unittest.TestCase):
    def test_shared_artifacts_root_resolves_relative_common_dir(self):
        result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=".git\n", stderr=""
        )
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(routing_stats.Path, "cwd", return_value=Path(temp)):
                with mock.patch.object(routing_stats.subprocess, "run", return_value=result):
                    self.assertEqual(
                        routing_stats.shared_artifacts_root(),
                        Path(temp) / "artifacts",
                    )

    def test_failures_dir_uses_shared_root_and_environment_override(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HALO_FAILURES_DIR", None)
            with mock.patch.object(
                routing_stats, "shared_artifacts_root", return_value=Path("/shared/artifacts")
            ):
                self.assertEqual(
                    routing_stats.failures_dir(),
                    Path("/shared/artifacts/auto_lift/failures"),
                )

        with mock.patch.dict(
            os.environ, {"HALO_FAILURES_DIR": "/tmp/custom-failures"}, clear=False
        ):
            self.assertEqual(
                routing_stats.failures_dir(), Path("/tmp/custom-failures")
            )

    def test_aggregate_preserves_failure_stage_counts(self):
        result = routing_stats.aggregate(
            [],
            [
                (
                    Path("failure.json"),
                    {
                        "verdict": "build_failed",
                        "attempts": [
                            {"model": "sonnet", "effort": "high", "failure_stage": "build"}
                        ],
                    },
                )
            ],
        )
        self.assertEqual(result["sources"]["failure_records"], 1)
        self.assertEqual(result["by_failure_stage"][0]["failure_stage"], "build")
        self.assertEqual(result["by_failure_stage"][0]["by_model"], {"sonnet": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
