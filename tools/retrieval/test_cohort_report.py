#!/usr/bin/env python3
"""Tests for cohort outcome aggregation and cost-normalized reporting."""

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from cohort_report import aggregate, price_weight  # noqa: E402


class CohortReportTests(unittest.TestCase):
    def test_price_weights_are_provider_neutral_family_matches(self):
        self.assertEqual(price_weight("opus-5[1m]"), 2.5)
        self.assertEqual(price_weight("sonnet-high"), 1.0)
        self.assertEqual(price_weight("haiku"), 0.5)
        self.assertIsNone(price_weight("provider/model"))

    def test_aggregate_exposes_priced_and_unpriced_cost_views(self):
        summary = aggregate([
            {"cohort": "control", "outcome": "committed", "tokens": 100,
             "model_id": "opus-high"},
            {"cohort": "control", "outcome": "parked", "tokens": 50,
             "model_id": "sonnet-high"},
            {"cohort": "control", "outcome": "committed", "tokens": 25,
             "model_id": "unknown-provider"},
        ])
        row = summary["control"]
        self.assertEqual(row["priced_records"], 2)
        self.assertEqual(row["unpriced_records"], 1)
        self.assertEqual(row["sonnet_equivalent_tokens"], 300.0)
        self.assertEqual(row["sonnet_equivalent_per_commit"], 300.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
