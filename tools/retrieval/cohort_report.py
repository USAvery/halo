#!/usr/bin/env python3
"""Aggregate lift outcomes by retrieval A/B cohort.

Reads the immutable outcome ledger written by
``tools/lift/research_bundle.py record-outcome``
(``artifacts/research_cache/outcomes/*.json``) and reports, per cohort:

    n              outcomes recorded
    committed      share of outcomes with outcome == "committed"
    parked         share of outcomes with outcome == "parked"
    mean tokens    mean tokens per outcome
    tokens/commit  total tokens divided by committed outcomes

Cohorts are assigned in ``research_bundle.retrieval_cohort``; ``control``
bundles carry no retrieval neighbors, ``retrieval`` bundles do.  A report
showing a single cohort means the experiment has no control arm and the
numbers compare nothing -- that was the state through 2026-09-02, when
address-parity assignment put 100% of 727 targets in ``retrieval``.

Usage:
    python3 tools/retrieval/cohort_report.py
    python3 tools/retrieval/cohort_report.py --json
    python3 tools/retrieval/cohort_report.py --since 2026-09-01
    python3 tools/retrieval/cohort_report.py --by route
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUTCOMES_DIR = REPO_ROOT / "artifacts" / "research_cache" / "outcomes"

COMMITTED = "committed"
PARKED = "parked"

# Relative to Sonnet 5 pricing. Outcome records contain one aggregate token
# count, so this is a token-equivalent estimate, not an absolute dollar cost.
MODEL_PRICE_WEIGHTS = {
    "opus": 2.5,
    "sonnet": 1.0,
    "haiku": 0.5,
}


def price_weight(model_id: str) -> float | None:
    model = (model_id or "").strip().lower()
    for family, weight in MODEL_PRICE_WEIGHTS.items():
        if model == family or model.startswith(family + "-"):
            return weight
    return None


def load_outcomes(outcomes_dir: Path, since: str = "") -> list[dict]:
    """Load every outcome record, oldest first, optionally filtered by date."""
    if not outcomes_dir.exists():
        return []
    records = []
    for path in outcomes_dir.glob("*.json"):
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or "cohort" not in rec:
            continue
        if since and str(rec.get("ts", "")) < since:
            continue
        records.append(rec)
    records.sort(key=lambda r: str(r.get("ts", "")))
    return records


def aggregate(records: list[dict], key: str = "cohort") -> dict:
    """Group records by *key* and compute the per-group summary."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        groups[str(rec.get(key) or "unknown")].append(rec)

    out = {}
    for name, rows in groups.items():
        n = len(rows)
        committed = sum(1 for r in rows if r.get("outcome") == COMMITTED)
        parked = sum(1 for r in rows if r.get("outcome") == PARKED)
        tokens = sum(int(r.get("tokens") or 0) for r in rows)
        weighted_tokens = 0.0
        priced_records = 0
        priced_committed = 0
        for record in rows:
            weight = price_weight(str(record.get("model_id") or ""))
            if weight is None:
                continue
            priced_records += 1
            weighted_tokens += int(record.get("tokens") or 0) * weight
            priced_committed += int(record.get("outcome") == COMMITTED)
        outcomes: dict[str, int] = defaultdict(int)
        for r in rows:
            outcomes[str(r.get("outcome") or "unknown")] += 1
        out[name] = {
            "n": n,
            "committed": committed,
            "parked": parked,
            "committed_rate": committed / n if n else 0.0,
            "parked_rate": parked / n if n else 0.0,
            "tokens_total": tokens,
            "mean_tokens": tokens / n if n else 0.0,
            # Cost of one landed function: the number the experiment exists
            # to move.  None when the arm has landed nothing yet.
            "tokens_per_commit": (tokens / committed) if committed else None,
            "priced_records": priced_records,
            "unpriced_records": n - priced_records,
            "sonnet_equivalent_tokens": weighted_tokens if priced_records else None,
            "sonnet_equivalent_per_commit": (
                weighted_tokens / priced_committed if priced_committed else None
            ),
            "outcomes": dict(sorted(outcomes.items())),
        }
    return dict(sorted(out.items()))


def render(summary: dict, key: str, total: int) -> str:
    lines = []
    lines.append(f"lift outcomes by {key}")
    lines.append(f"  ledger: {OUTCOMES_DIR}")
    lines.append(f"  records: {total}")
    lines.append("")
    hdr = (f"  {'cohort':<12} {'n':>6} {'commit%':>8} {'park%':>7} "
           f"{'mean tok':>10} {'tok/commit':>11} {'STE/commit':>11}")
    lines.append(hdr)
    lines.append("  " + "-" * (len(hdr) - 2))
    for name, s in summary.items():
        tpc = ("-" if s["tokens_per_commit"] is None
               else f"{s['tokens_per_commit']:,.0f}")
        ste = ("-" if s["sonnet_equivalent_per_commit"] is None
               else f"{s['sonnet_equivalent_per_commit']:,.0f}")
        lines.append(
            f"  {name:<12} {s['n']:>6} {s['committed_rate']*100:>7.1f}% "
            f"{s['parked_rate']*100:>6.1f}% {s['mean_tokens']:>10,.0f} {tpc:>11} {ste:>11}"
        )
    lines.append("")
    for name, s in summary.items():
        breakdown = "  ".join(f"{k}={v}" for k, v in s["outcomes"].items())
        lines.append(f"  {name}: {breakdown}")

    if key == "cohort":
        lines.append("")
        arms = [a for a in ("retrieval", "control") if a in summary]
        if not total:
            lines.append("  no outcomes in range — nothing to compare.")
        elif len(arms) < 2:
            missing = "control" if "retrieval" in arms else "retrieval"
            lines.append(f"  WARNING: no '{missing}' arm — this report compares "
                         f"nothing.")
            lines.append("  Check research_bundle.retrieval_cohort and any "
                         "RETRIEVAL_COHORT override.")
        else:
            r, c = summary["retrieval"], summary["control"]
            d_commit = (r["committed_rate"] - c["committed_rate"]) * 100
            lines.append(f"  retrieval - control: committed {d_commit:+.1f}pp, "
                         f"mean tokens {r['mean_tokens'] - c['mean_tokens']:+,.0f}")
            if min(r["n"], c["n"]) < 50:
                lines.append(f"  (underpowered: smallest arm n="
                             f"{min(r['n'], c['n'])}; treat as directional only)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--outcomes-dir", default=str(OUTCOMES_DIR))
    ap.add_argument("--by", default="cohort",
                    help="record field to group by (default: cohort)")
    ap.add_argument("--since", default="",
                    help="only outcomes with ts >= this ISO prefix")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    records = load_outcomes(Path(args.outcomes_dir), since=args.since)
    summary = aggregate(records, key=args.by)

    if args.json:
        json.dump({"key": args.by, "records": len(records),
                   "groups": summary}, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(render(summary, args.by, len(records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
