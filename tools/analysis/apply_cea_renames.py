#!/usr/bin/env python3
"""
apply_cea_renames.py — apply deferred Halo CE Anniversary (CEA) PDB
line-containment renames to kb.json and the matching src/ call sites.

Background: artifacts/ghidra_groom/rename_mapping.json holds NTSC-address ->
CEA-PDB-name proposals (tier: confirmed / high_confidence / probable) from an
earlier line-containment pass. A prior campaign (2026-07-10) already applied
the easy majority; this script picks up the rows that are still literally
named FUN_<addr8> in kb.json today.

Two kb.json storage shapes hold function records:
  - kb["objects"][*]["functions"][*]   (the vast majority)
  - kb["0x<addr>"] top-level entries    (a legacy minority, ~87 total)
Both are handled uniformly.

Subcommands:
  plan                         Compute and print the batch plan (no writes).
  apply --batch N [--dry-run]  Apply one batch. Without --dry-run, writes
                                kb.json and rewrites src/**/*.c, src/**/*.h.

Batching:
  - Batch 0 = every candidate row whose FUN_<addr8> token appears nowhere in
    src/ (i.e. the function has no C implementation yet — renaming it is a
    pure kb.json edit, zero build risk).
  - Batches 1..N group the remaining rows so that any two rows whose FUN_
    token appears in a shared src file always land in the same batch (a
    union-find over shared-file edges), packed greedily to ~50 rows/batch.

Per applied row:
  - FUN_<addr8> -> new_name, whole-word, in kb.json's "decl" and "name"
    (when present).
  - An evidence plate is appended to "comment" (created if absent):
      [NAME: <new_name> — CEA PDB line-containment (<tier>), <batch tag>]
  - FUN_<addr8> -> new_name, whole-word, across every src/**/*.c and
    src/**/*.h file that contains it.

Rows are dropped (never applied, reported separately) when:
  - The mapping's new_name already exists as a real (non-FUN_) function name
    anywhere in kb.json ("exists_in_kb").
  - Two or more candidate rows in this run propose the same new_name
    ("duplicate_target_in_mapping") — applying either would create a name
    collision the other introduces.

kb.json is rewritten with json.dump(kb, f, indent=1, ensure_ascii=False) and
no trailing newline, matching the file's existing exact byte format (verified
by round-tripping the file unmodified and diffing: zero bytes differ).
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KB_PATH = os.path.join(REPO_ROOT, "kb.json")
MAPPING_PATH = os.path.join(REPO_ROOT, "artifacts", "ghidra_groom", "rename_mapping.json")
SRC_ROOT = os.path.join(REPO_ROOT, "src")

BATCH_TAG = "cea-deferred 2026-09-03"
BATCH_SIZE_TARGET = 50

FUN_TOKEN_RE = re.compile(r'\bFUN_[0-9a-fA-F]{8}\b')
FUN_NAME_RE = re.compile(r'^FUN_[0-9a-fA-F]{8}$')
DECL_NAME_RE = re.compile(r'^(.*?)\b(\w+)\s*\((.*)\)\s*;?\s*$', re.DOTALL)


# =============================================================================
# kb.json / mapping I/O
# =============================================================================

def load_kb():
    with open(KB_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_kb(kb):
    with open(KB_PATH, "w", encoding="utf-8") as f:
        json.dump(kb, f, indent=1, ensure_ascii=False)


def load_mapping():
    with open(MAPPING_PATH, encoding="utf-8") as f:
        return json.load(f)


def norm_addr(addr8):
    """8-hex-digit address string (no 0x) -> kb.json's '0x...' key format."""
    return "0x" + format(int(addr8, 16), "x")


def parse_decl_name(decl):
    m = DECL_NAME_RE.match(decl)
    return m.group(2) if m else None


def iter_kb_records(kb):
    """Yield (kb_addr, record_dict) for every function record, both shapes."""
    for obj in kb.get("objects", []):
        for fn in obj.get("functions", []):
            addr = fn.get("addr", "")
            if addr:
                yield addr, fn
    for key, val in kb.items():
        if key.startswith("0x") and isinstance(val, dict):
            yield val.get("addr", key), val


def build_addr_index(kb):
    """addr -> every kb record at that address, both shapes.

    A minority of addresses (~87) exist as BOTH an objects[].functions entry
    AND a legacy top-level "0x..." entry. Only the objects[] copy feeds the
    real build (KnowledgeBase.deserialize() in tools/analysis/knowledge.py
    reads serialized_kb['objects'] only; the top-level shape is inert dead
    data there). A rename must therefore touch every record sharing an
    address, not just one of them, or the objects[] copy is left stale at
    FUN_<addr8> while source files are renamed out from under it, causing
    "implicit declaration" build failures.
    """
    idx = defaultdict(list)
    for addr, rec in iter_kb_records(kb):
        idx[addr].append(rec)
    return idx


def build_name_index(kb):
    """Set of every non-FUN_ function name currently in kb.json."""
    names = set()
    for _addr, rec in iter_kb_records(kb):
        nm = rec.get("name")
        if not nm:
            decl = rec.get("decl", "")
            nm = parse_decl_name(decl) if decl else None
        if nm and not FUN_NAME_RE.match(nm):
            names.add(nm)
    return names


# =============================================================================
# src/ scanning
# =============================================================================

def build_token_to_files():
    """One pass over src/**/*.c and src/**/*.h: {FUN_token: {file_path, ...}}."""
    result = defaultdict(set)
    for root, _dirs, fnames in os.walk(SRC_ROOT):
        for fn in fnames:
            if not (fn.endswith(".c") or fn.endswith(".h")):
                continue
            path = os.path.join(root, fn)
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
            for tok in set(FUN_TOKEN_RE.findall(content)):
                result[tok].add(path)
    return result


# =============================================================================
# Plan computation (shared by `plan` and `apply`)
# =============================================================================

class DSU:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def compute_candidates(kb, mapping):
    """Return (candidates, not_found) — rows whose kb record still literally
    contains FUN_<addr8>, and rows whose address isn't in kb at all."""
    addr_index = build_addr_index(kb)
    candidates = []
    not_found = []
    for row in mapping:
        addr8 = row["addr"]
        # Derive the token from the address itself, not row["old_name"]: the
        # mapping also carries rows from an earlier pass whose "old_name" is
        # already a real synced name (not a FUN_ placeholder), which must
        # never be treated as still-FUN_ just because that string happens to
        # appear in the (already correct) current decl.
        old_name = f"FUN_{addr8}"
        kb_addr = norm_addr(addr8)
        recs = addr_index.get(kb_addr)
        if not recs:
            not_found.append(row)
            continue
        # A candidate if ANY record at this address (either shape) still
        # carries the FUN_ placeholder; apply_batch_rows renames every
        # matching record, not just one, so dual-shape addresses stay in sync.
        if any(old_name in r.get("decl", "") or old_name in r.get("name", "") for r in recs):
            candidates.append({"row": row, "kb_addr": kb_addr, "recs": recs, "old_name": old_name})
    return candidates, not_found


def split_collisions(candidates, name_index):
    """Drop rows whose new_name already exists in kb, or is duplicated by
    another candidate row in this same run. Returns (kept, collisions)."""
    name_counts = Counter(c["row"]["new_name"] for c in candidates)
    kept = []
    collisions = []
    for c in candidates:
        new_name = c["row"]["new_name"]
        if new_name in name_index:
            collisions.append(dict(c, reason="exists_in_kb"))
        elif name_counts[new_name] > 1:
            collisions.append(dict(c, reason="duplicate_target_in_mapping"))
        else:
            kept.append(c)
    return kept, collisions


def compute_batches(kept, token_to_files):
    """batch 0 = zero-src-reference rows; batches 1..N = connected components
    (by shared src file) packed greedily to ~BATCH_SIZE_TARGET rows."""
    zero_ref = []
    with_ref = []
    for c in kept:
        files = token_to_files.get(c["old_name"], set())
        c["_files"] = files
        if files:
            with_ref.append(c)
        else:
            zero_ref.append(c)

    dsu = DSU(len(with_ref))
    file_to_indices = defaultdict(list)
    for i, c in enumerate(with_ref):
        for f in c["_files"]:
            file_to_indices[f].append(i)
    for indices in file_to_indices.values():
        for i in indices[1:]:
            dsu.union(indices[0], i)

    components = defaultdict(list)
    for i in range(len(with_ref)):
        components[dsu.find(i)].append(with_ref[i])

    comp_list = sorted(components.values(), key=len, reverse=True)

    packed = []
    current = []
    for comp in comp_list:
        if current and len(current) + len(comp) > BATCH_SIZE_TARGET and len(current) >= BATCH_SIZE_TARGET * 0.6:
            packed.append(current)
            current = []
        current.extend(comp)
    if current:
        packed.append(current)

    batches = [zero_ref] + packed
    return batches


def compute_plan(kb, mapping):
    candidates, not_found = compute_candidates(kb, mapping)
    name_index = build_name_index(kb)
    kept, collisions = split_collisions(candidates, name_index)
    token_to_files = build_token_to_files()
    batches = compute_batches(kept, token_to_files)
    return {
        "candidates": candidates,
        "not_found": not_found,
        "kept": kept,
        "collisions": collisions,
        "batches": batches,
        "token_to_files": token_to_files,
    }


# =============================================================================
# plan subcommand
# =============================================================================

def cmd_plan(args):
    kb = load_kb()
    mapping = load_mapping()
    plan = compute_plan(kb, mapping)

    print(f"Mapping rows loaded: {len(mapping)}")
    print(f"Still FUN_ in kb.json (candidates): {len(plan['candidates'])}")
    if plan["not_found"]:
        print(f"Address not present in kb.json: {len(plan['not_found'])}")
        for row in plan["not_found"][:10]:
            print(f"  0x{row['addr']} -> {row['new_name']}")
    print(f"Collisions (dropped): {len(plan['collisions'])}")
    for c in plan["collisions"]:
        print(f"  {c['kb_addr']} {c['old_name']} -> {c['row']['new_name']} "
              f"({c['row']['tier']}) [{c['reason']}]")
    print(f"Kept for application: {len(plan['kept'])}")
    print()
    print(f"Batch plan ({len(plan['batches'])} batches, target ~{BATCH_SIZE_TARGET} rows/batch "
          f"except batch 0):")
    for i, batch in enumerate(plan["batches"]):
        files = set()
        for c in batch:
            files |= c["_files"] if "_files" in c else set()
        print(f"  batch {i}: {len(batch)} rows, {len(files)} distinct src file(s)")
    return 0


# =============================================================================
# apply subcommand
# =============================================================================

def apply_batch_rows(kb, batch_rows, token_to_files, dry_run):
    """Mutate kb records in place (unless dry_run) and rewrite src files
    (unless dry_run). Returns (row_results, touched_files)."""
    file_edits = defaultdict(list)  # path -> [(old_name, new_name), ...]
    row_results = []

    for c in batch_rows:
        recs = c["recs"]
        row = c["row"]
        old_name = c["old_name"]
        new_name = row["new_name"]
        tier = row["tier"]
        token_re = re.compile(r'\b' + re.escape(old_name) + r'\b')

        # Rename every record sharing this address (objects[] copy AND any
        # legacy top-level duplicate), not just one, so a dual-shape address
        # never ends up with one copy renamed and the other stale.
        any_decl_changed = False
        any_name_changed = False
        records_touched = 0
        for rec in recs:
            decl_before = rec.get("decl", "")
            reg_annotations_before = decl_before.count("@<")
            decl_changed = old_name in decl_before
            name_changed = bool(rec.get("name")) and old_name in rec["name"]
            if decl_changed or name_changed:
                records_touched += 1
            any_decl_changed = any_decl_changed or decl_changed
            any_name_changed = any_name_changed or name_changed

            if not dry_run:
                if decl_changed:
                    rec["decl"] = token_re.sub(new_name, decl_before)
                    assert rec["decl"].count("@<") == reg_annotations_before, (
                        f"@<reg> annotation count changed for {c['kb_addr']}"
                    )
                if name_changed:
                    rec["name"] = token_re.sub(new_name, rec["name"])
                if decl_changed or name_changed:
                    plate = f"[NAME: {new_name} — CEA PDB line-containment ({tier}), {BATCH_TAG}]"
                    if rec.get("comment"):
                        rec["comment"] = rec["comment"].rstrip() + " " + plate
                    else:
                        rec["comment"] = plate

        touched = sorted(token_to_files.get(old_name, ()))
        for path in touched:
            file_edits[path].append((old_name, new_name))

        row_results.append({
            "addr": c["kb_addr"], "old_name": old_name, "new_name": new_name,
            "tier": tier, "decl_changed": any_decl_changed, "name_changed": any_name_changed,
            "files": touched, "records_touched": records_touched,
        })

    touched_files = []
    for path, pairs in file_edits.items():
        with open(path, encoding="utf-8") as f:
            content = f.read()
        new_content = content
        for old_name, new_name in pairs:
            new_content = re.sub(r'\b' + re.escape(old_name) + r'\b', new_name, new_content)
        if new_content != content:
            touched_files.append(path)
            if not dry_run:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_content)

    return row_results, sorted(touched_files)


def cmd_apply(args):
    kb = load_kb()
    mapping = load_mapping()
    plan = compute_plan(kb, mapping)
    batches = plan["batches"]

    if args.batch < 0 or args.batch >= len(batches):
        print(f"ERROR: batch {args.batch} out of range (0..{len(batches) - 1})", file=sys.stderr)
        return 1

    batch_rows = batches[args.batch]
    print(f"Batch {args.batch}: {len(batch_rows)} rows"
          f"{' (DRY RUN)' if args.dry_run else ''}")

    row_results, touched_files = apply_batch_rows(kb, batch_rows, plan["token_to_files"], args.dry_run)

    for r in sorted(row_results, key=lambda x: x["addr"]):
        print(f"  {r['addr']}: {r['old_name']} -> {r['new_name']} ({r['tier']}) "
              f"decl={'Y' if r['decl_changed'] else 'n'} name={'Y' if r['name_changed'] else 'n'} "
              f"files={len(r['files'])} recs={r['records_touched']}")

    print(f"\nRows applied: {len(row_results)}")
    print(f"Distinct src files touched: {len(touched_files)}")
    for f in touched_files:
        print(f"  {os.path.relpath(f, REPO_ROOT)}")

    if not args.dry_run:
        save_kb(kb)
        print("\nkb.json written.")
    else:
        print("\nDry run — no files written.")

    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("plan", help="Compute and print the batch plan")

    p_apply = sub.add_parser("apply", help="Apply one batch")
    p_apply.add_argument("--batch", type=int, required=True, help="Batch index to apply")
    p_apply.add_argument("--dry-run", action="store_true", help="Print what would change; write nothing")

    args = parser.parse_args()
    if args.command == "plan":
        return cmd_plan(args)
    elif args.command == "apply":
        return cmd_apply(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
