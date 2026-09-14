#!/usr/bin/env python3
"""Re-run every allowlisted target under a given oracle. Read-only.

Why
---
`batch_verify_allowlist.json` holds 383 targets. 334 of them (87%) are
reference-availability or relocation artifacts rather than lift bugs:

    243  oracle-unmappable    reference crashes in the zero-fill Unicorn
                              harness -- an unmapped callee code page, or a
                              relocation that resolved to nothing
     71  oracle_extract_failed  no delinked object to build an oracle from
     20  lifted_extract_failed  candidate side; oracle-independent
     39  timeout / instruction budget
      7  deflate_state* seed-domain crashes

The first two categories are DEFINITIONALLY delinked-lane problems: with the
pristine image mapped at real VAs there is no unmapped callee page and nothing
to relocate. So most of this file should be retirable -- but "should be" is
not evidence, and a bulk clear would turn 314 unreviewed targets loose into the
batch gate at once, where a genuine lift bug hiding behind an infra excuse
would land as a fresh red row with no one to read it.

This script produces the evidence instead of acting on it. It runs each
allowlisted target exactly as `batch_verify.py` would, classifies the outcome
against the reason the entry claims, and writes a CSV plus a JSON summary. It
NEVER edits the allowlist: retirement is a reviewed commit that cites this
artifact.

Usage
-----
    python3 tools/equivalence/retry_allowlisted.py                  # all
    python3 tools/equivalence/retry_allowlisted.py --category oracle-unmappable
    python3 tools/equivalence/retry_allowlisted.py --limit 25 --jobs 3
    python3 tools/equivalence/retry_allowlisted.py --oracle delinked  # A/B

Writes artifacts/equivalence/allowlist_retry_<oracle>[_<category>].{csv,json}.
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent.parent
sys.path.insert(0, str(_HERE))

import batch_verify

ALLOWLIST = _HERE / "batch_verify_allowlist.json"
OUT_DIR = ROOT / "artifacts" / "equivalence"
RUN_DIR = ROOT / "artifacts" / "equivalence" / "allowlist_retry_runs"


def artifact_paths(oracle: str, category: str = ""):
    """Per-(oracle, category) artifact names.

    A single fixed pair of filenames would make each category run silently
    overwrite the last, so a five-category sweep would leave one file claiming
    to be the whole picture.
    """
    stem = f"allowlist_retry_{oracle}"
    if category:
        stem += "_" + category.replace("-", "_")
    return OUT_DIR / (stem + ".csv"), OUT_DIR / (stem + ".json")

# Which claim each entry makes about itself, in the order a reason is tested.
# Order matters: a `deflate_state` entry also says "oracle crash", and the
# seed-domain category is the more specific claim.
_CATEGORIES = (
    ("deflate_state", ("deflate_state",)),
    ("timeout", ("timeout", "instruction budget", "non-terminating",
                 "instruction-budget")),
    # `lifted_extract_failed` is tested BEFORE the oracle one: those 20
    # entries read "no-delinked-ref: lifted_extract_failed ...", so the
    # oracle needles match them too, and the side that actually failed is
    # the candidate.  Getting this backwards would file 20 oracle-independent
    # rows under a category the migration is expected to fix, and then read
    # their still-failing status as the migration falling short.
    ("lifted_extract_failed", ("lifted_extract_failed",)),
    ("oracle_extract_failed", ("oracle_extract_failed",
                               "no-delinked-ref", "missing delinked",
                               "no delinked")),
    ("oracle-unmappable", ("oracle-unmappable", "unmapped callee code page",
                           "missing reloc", "oracle crash", "oracle-crash")),
)

# Categories the migration cannot explain away, so a still-failing row there is
# expected and says nothing about the oracle.
_ORACLE_INDEPENDENT = {"timeout", "deflate_state", "lifted_extract_failed"}


def categorize(entry: dict) -> str:
    reasons = " ".join(entry.get("reasons", []) or []).lower()
    for name, needles in _CATEGORIES:
        if any(n.lower() in reasons for n in needles):
            return name
    return "other"


def load_entries(category: str = "") -> list:
    raw = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    out = []
    for name, entry in (raw.get("targets") or {}).items():
        if not isinstance(entry, dict):
            entry = {"reasons": [str(entry)]}
        cat = categorize(entry)
        if category and cat != category:
            continue
        out.append({
            "name": name,
            "category": cat,
            "scoped_oracle": entry.get("oracle"),
            "reasons": entry.get("reasons", []),
        })
    out.sort(key=lambda r: (r["category"], r["name"]))
    return out


def verdict_of(result: dict) -> str:
    """One of now_passes / now_fails / now_runs_no_verdict / still_errors.

    Four buckets rather than two, because "the excuse is gone" and "the target
    is now useful" are different facts and retirement needs both:

      now_passes           the excuse is gone and the target is green.
      now_fails            the harness RAN and the two sides differed. Kept
                           apart from still_errors on purpose: a recovered
                           target that found a real divergence must not read
                           as the same infra problem it was allowlisted for.
                           This is the bucket that has to be read by a human
                           before anything is retired.
      now_runs_no_verdict  `inconclusive` / `not_applicable`. The original
                           reason IS gone -- the reference no longer crashes --
                           but the run yields no evidence either way (usually
                           vacuous_output: the seeds produce no observable
                           variation). Retiring one of these trades an honest
                           infra excuse for a permanently grey row.
      still_errors         no verdict and no run: the excuse still stands.
    """
    status = (result or {}).get("status")
    if status == "pass":
        return "now_passes"
    if status == "fail":
        return "now_fails"
    if status in ("inconclusive", "not_applicable"):
        return "now_runs_no_verdict"
    return "still_errors"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--oracle", choices=("xbe", "delinked"), default="xbe",
                    help="Oracle to retry under (default: xbe)")
    ap.add_argument("--category", default="",
                    help="Only retry one category (see the table above)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Retry at most N targets (0 = all)")
    ap.add_argument("--names", default="",
                    help="Only retry these comma-separated names, or @<file> "
                         "for one name per line. Cuts across --category, "
                         "which is what a targeted re-check needs: a rename "
                         "batch or a fix spans several categories at once.")
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true",
                    help="List what would be retried and exit")
    args = ap.parse_args()

    entries = load_entries(args.category)
    if args.names:
        spec = args.names
        if spec.startswith("@"):
            spec = Path(spec[1:]).read_text(encoding="utf-8")
        wanted = {n.strip() for n in spec.replace(",", "\n").splitlines()
                  if n.strip()}
        entries = [e for e in entries if e["name"] in wanted]
        # A name that matches nothing is a typo or a row that is already gone.
        # Silently retrying 15 of 16 would read as a clean result.
        absent = sorted(wanted - {e["name"] for e in entries})
        if absent:
            print("ERROR: not in the allowlist: " + ", ".join(absent),
                  file=sys.stderr)
            return 2
    if args.limit > 0:
        entries = entries[:args.limit]
    if not entries:
        print("No allowlist entries match.")
        return 0

    if args.dry_run:
        print(f"{'Category':<22} {'Name':<34} Reason")
        print("-" * 100)
        for e in entries:
            reason = (e["reasons"] or [""])[0]
            print(f"{e['category']:<22} {e['name']:<34} {reason[:60]}")
        print(f"\n{len(entries)} target(s)")
        return 0

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Retrying {len(entries)} allowlisted target(s) under "
          f"--oracle={args.oracle}, {args.seeds} seeds, {args.jobs} job(s)\n")

    t0 = time.time()
    done = [0]

    def run_one(e):
        result = batch_verify.run_verify(
            e["name"], RUN_DIR, seeds=args.seeds, timeout=args.timeout,
            oracle=args.oracle)
        rec = dict(e)
        rec["status"] = (result or {}).get("status")
        rec["reason"] = (result or {}).get("reason")
        rec["coverage_pct"] = (result or {}).get("coverage_pct")
        rec["confidence"] = (result or {}).get("confidence")
        rec["verdict"] = verdict_of(result)
        done[0] += 1
        print(f"  [{done[0]:3d}/{len(entries)}] {rec['verdict']:<12} "
              f"{e['name']:<34} {e['category']:<22} "
              f"{rec['status']}/{rec['reason'] or '-'}")
        return rec

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        records = list(pool.map(run_one, entries))

    counts = {}
    by_cat = {}
    for r in records:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        cat = by_cat.setdefault(r["category"], {})
        cat[r["verdict"]] = cat.get(r["verdict"], 0) + 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # A --names run must not overwrite the full sweep's artifact: the two
    # answer different questions and the sweep's is the reviewable one.
    stem_cat = args.category or ("names" if args.names else "")
    csv_path, json_path = artifact_paths(args.oracle, stem_cat)
    cols = ("name", "category", "verdict", "status", "reason",
            "coverage_pct", "confidence", "scoped_oracle")
    lines = [",".join(cols)]
    for r in sorted(records, key=lambda x: (x["category"], x["name"])):
        row = []
        for c in cols:
            v = r.get(c)
            v = "" if v is None else str(v)
            row.append('"' + v.replace('"', '""') + '"' if "," in v or '"' in v
                       else v)
        lines.append(",".join(row))
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    json_path.write_text(json.dumps({
        "oracle": args.oracle,
        "seeds": args.seeds,
        "generated_at": time.time(),
        "elapsed_s": round(time.time() - t0, 1),
        "total": len(records),
        "counts": counts,
        "by_category": by_cat,
        "note": ("Evidence only. This tool never edits the allowlist; "
                 "retirement is a reviewed commit citing this artifact. "
                 "A still_errors row in an oracle-independent category "
                 "(timeout, deflate_state, lifted_extract_failed) is expected "
                 "and says nothing about the oracle."),
        "records": records,
    }, indent=2) + "\n", encoding="utf-8")

    print(f"\nDone in {time.time() - t0:.1f}s")
    for verdict in ("now_passes", "now_fails", "now_runs_no_verdict",
                    "still_errors"):
        print(f"  {verdict:<14}: {counts.get(verdict, 0)}")
    print("\n  by category:")
    for cat in sorted(by_cat):
        parts = ", ".join(f"{k}={v}" for k, v in sorted(by_cat[cat].items()))
        tag = "  (oracle-independent)" if cat in _ORACLE_INDEPENDENT else ""
        print(f"    {cat:<22} {parts}{tag}")
    print(f"\n  {csv_path.relative_to(ROOT)}")
    print(f"  {json_path.relative_to(ROOT)}")
    print("\nThis tool does not edit the allowlist. Retire entries in "
          "reviewed batches citing the artifact above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
