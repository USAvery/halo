#!/usr/bin/env python3
"""
apply_cea_renames.py — single apply path for CEA-derived function-name
proposals and same-build symbol dumps, applied to kb.json and matching src/
call sites, with honest per-row provenance.

Background: two distinct evidence pipelines produce FUN_<addr8> -> name
proposals for this binary, and neither is ground truth:

  - artifacts/ghidra_groom/rename_mapping.json (method: line_containment)
    comes from the real HCEX PDB (see artifacts/ghidra_groom/cea_corpus/
    cea_procs.json) via a line-containment match against our own build.
    name_source = "cea-pdb".
  - artifacts/cea_propagation/{proposals,window_proposals,string_proposals}.json
    (methods: callgraph_propagation, anchored_window, exact_string) are
    derived from the decompiled 0563 halocea corpus, not our PDB.
    name_source = "halocea".

Per the naming-confidence skill's "Cross-build corpora" section, CEA cross-build
evidence — a different build's PDB, or a different build's decompiled
corpus — never self-justifies a T1 name; both pipelines here are capped at
T2 regardless of the row's own confidence tier (confirmed / high_confidence
/ probable, which measures match quality within its own pipeline, not
evidentiary tier). A row's name_source must never be mislabeled as PDB
evidence when it is actually corpus-derived, or vice versa.

rename_mapping.json also carries "punpckhdq_only" rows — a THIRD, unrelated
cross-build PDB corpus (a different retail/PC debug build; see
artifacts/ghidra_groom/CHARTER.md). Those already have their own apply path,
tools/analysis/apply_punpckhdq_renames.py, and are excluded here rather than
mislabeled as cea-pdb or halocea.

Two kb.json storage shapes hold function records:
  - kb["objects"][*]["functions"][*]   (the vast majority)
  - kb["0x<addr>"] top-level entries    (a legacy minority, ~87 total)
Both are handled uniformly.

Same-build symbol dumps use --symbol-dump. Their text/RVA rows are direct
evidence for this binary and are tagged name_source=2276-symbol-dump, T1.
Only leading-underscore function symbols are imported; sub_ placeholders are
ignored.

Subcommands:
  plan                         Compute and print the batch plan (no writes).
  apply --batch N [--dry-run]  Apply one batch. Without --dry-run, writes
                                kb.json and rewrites src/**/*.c, src/**/*.h.

Shared options (plan and apply):
  --mapping PATH   Repeatable. Default: artifacts/ghidra_groom/
                   rename_mapping.json only. Also accepts
                   artifacts/cea_propagation/proposals.json,
                   window_proposals.json, string_proposals.json — same row
                   schema, "method" says which pipeline produced the row.
  --min-tier T     confirmed / high_confidence / probable (default
                   high_confidence). Rows below this are held back, never
                   batched.
  --exclude ADDR   Repeatable. Drops one address (8-hex or 0x-prefixed)
                   from consideration across every mapping file.

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
  - An evidence plate is appended to "comment" (created if absent — always
    append-only, never edits or removes a prior plate):
      [NAME: <new_name> — name_source=<src> method=<method> tier=<tier>
       (T2), <batch tag> <date>]
  - FUN_<addr8> -> new_name, whole-word, across every src/**/*.c and
    src/**/*.h file that contains it.

Rows are dropped (never applied, reported separately) when:
  - Their method has no known name_source ("unsupported_method") — e.g.
    punpckhdq_only rows; route those through apply_punpckhdq_renames.py
    instead of guessing at their provenance here.
  - Two rows (usually from two different mapping files) propose different
    new_name values for the same address ("cross_file_disagreement") — all
    sides are dropped and reported. When they instead agree on the same
    new_name, they are deduplicated to one candidate — preferring
    cea-pdb/line_containment as the cited provenance when a halocea row
    merely corroborates the same name (recorded as "_corroborated_by").
  - Below --min-tier ("held back").
  - The mapping's new_name already exists as a real (non-FUN_) function name
    anywhere in kb.json ("exists_in_kb").
  - Two or more candidate rows in this run propose the same new_name for
    different addresses ("duplicate_target_in_mapping") — applying either
    would create a name collision the other introduces.
  - Explicitly excluded via --exclude.

kb.json is rewritten with json.dump(kb, f, indent=1, ensure_ascii=False) and
no trailing newline, matching the file's existing exact byte format (verified
by round-tripping the file unmodified and diffing: zero bytes differ).
"""

import argparse
import copy
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KB_PATH = os.path.join(REPO_ROOT, "kb.json")
DEFAULT_MAPPING_PATH = os.path.join(REPO_ROOT, "artifacts", "ghidra_groom", "rename_mapping.json")
SRC_ROOT = os.path.join(REPO_ROOT, "src")

BATCH_TAG_LABEL = "cea-apply-unified"
BATCH_SIZE_TARGET = 50

FUN_TOKEN_RE = re.compile(r'\bFUN_[0-9a-fA-F]{8}\b')
FUN_NAME_RE = re.compile(r'^FUN_[0-9a-fA-F]{8}$')
DECL_NAME_RE = re.compile(r'^(.*?)\b(\w+)\s*\((.*)\)\s*;?\s*$', re.DOTALL)

# method -> evidence-plate name_source. A method missing from this table is
# reported and dropped as "unsupported_method" rather than guessed at.
METHOD_TO_SOURCE = {
    "line_containment": "cea-pdb",
    "callgraph_propagation": "halocea",
    "anchored_window": "halocea",
    "exact_string": "halocea",
    "exact_address": "2276-symbol-dump",
}

METHOD_EVIDENCE_TIER = {
    "line_containment": "T2",
    "callgraph_propagation": "T2",
    "anchored_window": "T2",
    "exact_string": "T2",
    "exact_address": "T1",
}

# When two+ mapping files agree on the same address and new_name, this order
# picks whose (method, mapping_file) is cited as primary in the plate —
# prefer the real PDB match over corpus inference.
METHOD_PRIORITY = {
    "line_containment": 0,
    "callgraph_propagation": 1,
    "anchored_window": 2,
    "exact_string": 3,
}

TIER_RANK = {"confirmed": 3, "high_confidence": 2, "probable": 1}
TIER_CHOICES = tuple(TIER_RANK)


def batch_tag():
    return f"{BATCH_TAG_LABEL} {date.today().isoformat()}"


# =============================================================================
# kb.json / mapping I/O
# =============================================================================

def load_kb():
    with open(KB_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_kb(kb):
    fd, temp_path = tempfile.mkstemp(prefix=".kb.json.", dir=os.path.dirname(KB_PATH))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(kb, f, indent=1, ensure_ascii=False)
        os.replace(temp_path, KB_PATH)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def load_mapping_rows(paths):
    """Load and concatenate rows from every --mapping path, tagging each row
    with the (repo-relative) file it came from, for reporting only."""
    rows = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            file_rows = json.load(f)
        rel = os.path.relpath(path, REPO_ROOT)
        for row in file_rows:
            rows.append(dict(row, _mapping_file=rel))
    return rows


def load_symbol_dump_rows(paths):
    """Read text/RVA symbol dumps for the target binary into mapping rows."""
    rows = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line_number, line in enumerate(f, 1):
                columns = line.rstrip("\n").split("\t")
                if len(columns) < 3 or not columns[0].startswith("_"):
                    continue
                raw_name = columns[0]
                new_name = raw_name[1:]
                # Map exports occasionally render argument text in the symbol.
                # They cannot safely become C identifiers without separate evidence.
                if not re.fullmatch(r"[A-Za-z_]\w*", new_name):
                    continue
                rows.append({
                    "addr": format(int(columns[2], 16), "08x"),
                    "new_name": new_name,
                    "method": "exact_address",
                    "tier": "confirmed",
                    "_mapping_file": path,
                })
    return rows


def norm_addr(addr8):
    """8-hex-digit address string (no 0x) -> kb.json's '0x...' key format."""
    return "0x" + format(int(addr8, 16), "x")


def norm_exclude_arg(arg):
    """--exclude value (8-hex or 0x-prefixed, any case) -> row['addr']
    format: 8 lowercase hex digits, no 0x."""
    return format(int(arg, 16), "08x")


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


def build_live_addr_index(kb):
    """Index objects[].functions, the records KnowledgeBase.deserialize uses."""
    idx = defaultdict(list)
    for obj in kb.get("objects", []):
        for rec in obj.get("functions", []):
            addr = rec.get("addr", "")
            if addr:
                idx[addr].append(rec)
    return idx


def build_live_addr_owner_index(kb):
    """Map each build-visible function address to its owning object."""
    idx = {}
    for obj in kb.get("objects", []):
        for rec in obj.get("functions", []):
            addr = rec.get("addr", "")
            if addr:
                idx[addr] = obj["name"]
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


def split_unsupported(rows):
    """Partition rows by whether their method has a known name_source.
    Returns (supported, unsupported)."""
    supported, unsupported = [], []
    for row in rows:
        if row.get("method") in METHOD_TO_SOURCE:
            supported.append(row)
        else:
            unsupported.append(row)
    return supported, unsupported


def compute_candidates(kb, rows):
    """Return (candidates, not_found) for method-supported rows — rows whose
    kb record still literally contains FUN_<addr8>, and rows whose address
    isn't in kb at all."""
    addr_index = build_addr_index(kb)
    live_addr_index = build_live_addr_index(kb)
    live_addr_owner_index = build_live_addr_owner_index(kb)
    candidates = []
    not_found = []
    for row in rows:
        addr8 = row["addr"]
        # Derive the token from the address itself, not row["old_name"]: a
        # mapping file can also carry rows from an earlier pass whose
        # "old_name" is already a real synced name (not a FUN_ placeholder),
        # which must never be treated as still-FUN_ just because that string
        # happens to appear in the (already correct) current decl.
        old_name = f"FUN_{addr8}"
        kb_addr = norm_addr(addr8)
        recs = addr_index.get(kb_addr)
        if not recs:
            not_found.append(row)
            continue
        # Direct 2276 symbols are applied only when a build-visible record
        # still uses the placeholder. Legacy-only records must not rename
        # source out from under the declarations consumed by the build.
        if row["method"] == "exact_address":
            live_recs = live_addr_index.get(kb_addr, ())
            if not any(old_name in r.get("decl", "") or old_name in r.get("name", "")
                       for r in live_recs):
                continue
        # A candidate if ANY record at this address (either shape) still
        # carries the FUN_ placeholder; apply_batch_rows renames every
        # matching record, not just one, so dual-shape addresses stay in sync.
        if any(old_name in r.get("decl", "") or old_name in r.get("name", "") for r in recs):
            method = row["method"]
            candidates.append({
                "row": row, "kb_addr": kb_addr, "recs": recs, "old_name": old_name,
                "method": method, "source": METHOD_TO_SOURCE[method],
                "mapping_file": row["_mapping_file"], "tier": row["tier"],
                "new_name": row["new_name"],
                "object": live_addr_owner_index.get(kb_addr),
            })
    return candidates, not_found


def resolve_cross_file(candidates):
    """Group candidates by address. Addresses where every candidate agrees
    on new_name are deduplicated to one representative (lowest
    METHOD_PRIORITY wins — cea-pdb over halocea — with the rest recorded in
    "_corroborated_by"); addresses that disagree are dropped entirely and
    reported. Returns (resolved, disagreements)."""
    by_addr = defaultdict(list)
    for c in candidates:
        by_addr[c["kb_addr"]].append(c)

    resolved = []
    disagreements = []
    for kb_addr, group in by_addr.items():
        names = {c["new_name"] for c in group}
        if len(names) > 1:
            disagreements.append({"kb_addr": kb_addr, "candidates": group})
            continue
        group_sorted = sorted(group, key=lambda c: METHOD_PRIORITY.get(c["method"], 99))
        primary = group_sorted[0]
        if len(group_sorted) > 1:
            primary = dict(primary)
            primary["_corroborated_by"] = [
                {"mapping_file": c["mapping_file"], "method": c["method"]}
                for c in group_sorted[1:]
            ]
        resolved.append(primary)
    return resolved, disagreements


def apply_excludes(candidates, exclude_set):
    """Split out explicitly --excluded rows. Runs on raw per-mapping-file
    candidates, before resolve_cross_file, so an excluded address is reported
    as excluded rather than surfacing as a cross-file disagreement or
    agreement it never got a chance to join."""
    if not exclude_set:
        return candidates, []
    kept, excluded = [], []
    for c in candidates:
        if c["row"]["addr"].lower() in exclude_set:
            excluded.append(c)
        else:
            kept.append(c)
    return kept, excluded


def split_by_tier(candidates, min_tier):
    min_rank = TIER_RANK[min_tier]
    tier_ok, held_back = [], []
    for c in candidates:
        if TIER_RANK[c["tier"]] >= min_rank:
            tier_ok.append(c)
        else:
            held_back.append(c)
    return tier_ok, held_back


def split_collisions(candidates, name_index):
    """Drop rows whose new_name already exists in kb, or is duplicated by
    another candidate row in this same run. Returns (kept, collisions)."""
    name_counts = Counter(c["new_name"] for c in candidates)
    kept = []
    collisions = []
    for c in candidates:
        new_name = c["new_name"]
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

    exact_by_object = defaultdict(list)
    ungrouped = []
    for c in with_ref:
        if c["method"] == "exact_address" and c["object"]:
            exact_by_object[c["object"]].append(c)
        else:
            ungrouped.append(c)

    dsu = DSU(len(ungrouped))
    file_to_indices = defaultdict(list)
    for i, c in enumerate(ungrouped):
        for f in c["_files"]:
            file_to_indices[f].append(i)
    for indices in file_to_indices.values():
        for i in indices[1:]:
            dsu.union(indices[0], i)

    components = defaultdict(list)
    for i in range(len(ungrouped)):
        components[dsu.find(i)].append(ungrouped[i])

    exact_batches = sorted(exact_by_object.values(), key=lambda group: group[0]["object"])
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

    batches = [zero_ref] + exact_batches + packed
    return batches


def compute_plan(kb, mapping_paths, min_tier="high_confidence", exclude_addrs=(),
                 symbol_dump_paths=()):
    rows = load_mapping_rows(mapping_paths)
    rows.extend(load_symbol_dump_rows(symbol_dump_paths))
    rows_by_file = Counter(r["_mapping_file"] for r in rows)
    supported_rows, unsupported_rows = split_unsupported(rows)
    candidates, not_found = compute_candidates(kb, supported_rows)
    exclude_set = {norm_exclude_arg(a) for a in exclude_addrs}
    excl_kept, excluded = apply_excludes(candidates, exclude_set)
    resolved, disagreements = resolve_cross_file(excl_kept)
    tier_ok, held_back = split_by_tier(resolved, min_tier)
    name_index = build_name_index(kb)
    kept, collisions = split_collisions(tier_ok, name_index)
    token_to_files = build_token_to_files()
    batches = compute_batches(kept, token_to_files)
    return {
        "rows_by_file": rows_by_file,
        "unsupported_rows": unsupported_rows,
        "not_found": not_found,
        "candidates_count": len(candidates),
        "disagreements": disagreements,
        "excluded": excluded,
        "held_back": held_back,
        "collisions": collisions,
        "kept": kept,
        "batches": batches,
        "token_to_files": token_to_files,
    }


# =============================================================================
# plan subcommand
# =============================================================================

def cmd_plan(args):
    kb = load_kb()
    plan = compute_plan(kb, args.mapping, args.min_tier, args.exclude,
                        args.symbol_dump)

    print("Mapping rows loaded:")
    for path, count in plan["rows_by_file"].items():
        print(f"  {path}: {count}")
    print(f"  total: {sum(plan['rows_by_file'].values())}")

    if plan["unsupported_rows"]:
        by_method = Counter(r["method"] for r in plan["unsupported_rows"])
        print(f"\nUnsupported method rows (not this script's apply path): {len(plan['unsupported_rows'])}")
        for method, count in by_method.items():
            hint = " -> use tools/analysis/apply_punpckhdq_renames.py" if method == "punpckhdq_only" else ""
            print(f"  {method}: {count}{hint}")

    print(f"\nStill FUN_ in kb.json (candidates, pre-dedup): {plan['candidates_count']}")

    if plan["not_found"]:
        print(f"Address not present in kb.json: {len(plan['not_found'])}")
        for row in plan["not_found"][:10]:
            print(f"  0x{row['addr']} -> {row['new_name']} ({row['_mapping_file']})")

    if plan["disagreements"]:
        print(f"\nCross-file disagreements (dropped, all sides): {len(plan['disagreements'])}")
        for d in plan["disagreements"]:
            proposals = ", ".join(
                f"{c['new_name']} [{c['mapping_file']}/{c['method']}]" for c in d["candidates"]
            )
            print(f"  {d['kb_addr']}: {proposals}")

    if plan["excluded"]:
        print(f"\nExplicitly excluded (--exclude): {len(plan['excluded'])}")
        for c in plan["excluded"]:
            print(f"  {c['kb_addr']} {c['old_name']} -> {c['new_name']}")

    if plan["held_back"]:
        by_tier = Counter(c["tier"] for c in plan["held_back"])
        print(f"\nHeld back (below --min-tier {args.min_tier}): {len(plan['held_back'])}")
        for tier in sorted(by_tier, key=lambda t: -TIER_RANK[t]):
            print(f"  {tier}: {by_tier[tier]}")

    print(f"\nCollisions (dropped): {len(plan['collisions'])}")
    for c in plan["collisions"]:
        print(f"  {c['kb_addr']} {c['old_name']} -> {c['new_name']} "
              f"({c['tier']}) [{c['reason']}]")

    print(f"\nKept for application: {len(plan['kept'])}")
    print()
    print(f"Batch plan ({len(plan['batches'])} batches, target ~{BATCH_SIZE_TARGET} rows/batch "
          f"except batch 0):")
    for i, batch in enumerate(plan["batches"]):
        files = set()
        for c in batch:
            files |= c.get("_files", set())
        by_source = Counter(c["source"] for c in batch)
        by_method = Counter(c["method"] for c in batch)
        by_mapping = Counter(c["mapping_file"] for c in batch)
        print(f"  batch {i}: {len(batch)} rows, {len(files)} distinct src file(s)")
        print(f"    by name_source: {dict(by_source)}")
        print(f"    by method: {dict(by_method)}")
        print(f"    by mapping file: {dict(by_mapping)}")
        if files:
            file_counts = Counter()
            for c in batch:
                for f in c.get("_files", set()):
                    file_counts[f] += 1
            for f, count in sorted(file_counts.items(), key=lambda kv: (-kv[1], kv[0])):
                print(f"    {os.path.relpath(f, REPO_ROOT)}: {count} row(s)")
        else:
            print("    (kb.json only — no src/ references)")
    return 0


# =============================================================================
# apply subcommand
# =============================================================================

def apply_batch_rows(kb, batch_rows, token_to_files, dry_run):
    """Prepare kb/source updates without writing files.

    cmd_apply commits the prepared changes only after all rows pass guards.
    Keeping this function write-free prevents a failed metadata save from
    leaving source tokens renamed under stale FUN_ declarations.
    """
    file_edits = defaultdict(list)  # path -> [(old_name, new_name), ...]
    row_results = []
    tag = batch_tag()

    for c in batch_rows:
        recs = c["recs"]
        old_name = c["old_name"]
        new_name = c["new_name"]
        tier = c["tier"]
        method = c["method"]
        source = c["source"]
        token_re = re.compile(r'\b' + re.escape(old_name) + r'\b')
        plate = (f"[NAME: {new_name} — name_source={source} method={method} "
                 f"tier={tier} ({METHOD_EVIDENCE_TIER[method]}), {tag}]")

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
                    if rec.get("comment"):
                        rec["comment"] = rec["comment"].rstrip() + " " + plate
                    else:
                        rec["comment"] = plate

        touched = sorted(token_to_files.get(old_name, ()))
        for path in touched:
            file_edits[path].append((old_name, new_name))

        row_results.append({
            "addr": c["kb_addr"], "old_name": old_name, "new_name": new_name,
            "tier": tier, "method": method, "source": source, "plate": plate,
            "decl_changed": any_decl_changed, "name_changed": any_name_changed,
            "files": touched, "records_touched": records_touched,
        })

    file_updates = {}
    for path, pairs in file_edits.items():
        with open(path, encoding="utf-8") as f:
            content = f.read()
        new_content = content
        for old_name, new_name in pairs:
            new_content = re.sub(r'\b' + re.escape(old_name) + r'\b', new_name, new_content)
        if new_content != content:
            file_updates[path] = (content, new_content)

    return row_results, file_updates


def cmd_apply(args):
    kb = load_kb()
    original_kb = copy.deepcopy(kb)
    plan = compute_plan(kb, args.mapping, args.min_tier, args.exclude,
                        args.symbol_dump)
    batches = plan["batches"]

    if args.batch < 0 or args.batch >= len(batches):
        print(f"ERROR: batch {args.batch} out of range (0..{len(batches) - 1})", file=sys.stderr)
        return 1

    batch_rows = batches[args.batch]
    print(f"Batch {args.batch}: {len(batch_rows)} rows"
          f"{' (DRY RUN)' if args.dry_run else ''}")

    row_results, file_updates = apply_batch_rows(kb, batch_rows, plan["token_to_files"], args.dry_run)

    for r in sorted(row_results, key=lambda x: x["addr"]):
        print(f"  {r['addr']}: {r['old_name']} -> {r['new_name']} "
              f"({r['tier']}, {r['source']}/{r['method']}) "
              f"decl={'Y' if r['decl_changed'] else 'n'} name={'Y' if r['name_changed'] else 'n'} "
              f"files={len(r['files'])} recs={r['records_touched']}")
        print(f"    {r['plate']}")

    print(f"\nRows applied: {len(row_results)}")
    print(f"Distinct src files touched: {len(file_updates)}")
    for f in sorted(file_updates):
        print(f"  {os.path.relpath(f, REPO_ROOT)}")

    if not args.dry_run:
        try:
            save_kb(kb)
            for path, (_old_content, new_content) in file_updates.items():
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_content)
        except OSError:
            for path, (old_content, _new_content) in file_updates.items():
                with open(path, "w", encoding="utf-8") as f:
                    f.write(old_content)
            save_kb(original_kb)
            raise
        print("\nkb.json and source written.")
    else:
        print("\nDry run — no files written.")

    return 0


def add_common_args(p):
    p.add_argument("--mapping", action="append", dest="mapping", metavar="PATH",
                    help="Mapping JSON path (repeatable). Default: "
                         "artifacts/ghidra_groom/rename_mapping.json")
    p.add_argument("--min-tier", choices=TIER_CHOICES, default="high_confidence",
                    help="Minimum row tier to apply (default: high_confidence)")
    p.add_argument("--exclude", action="append", default=[], dest="exclude", metavar="ADDR",
                    help="Address to drop from consideration (repeatable)")
    p.add_argument("--symbol-dump", action="append", default=[], metavar="PATH",
                    help="Same-build text/RVA symbol dump (repeatable)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    p_plan = sub.add_parser("plan", help="Compute and print the batch plan")
    add_common_args(p_plan)

    p_apply = sub.add_parser("apply", help="Apply one batch")
    add_common_args(p_apply)
    p_apply.add_argument("--batch", type=int, required=True, help="Batch index to apply")
    p_apply.add_argument("--dry-run", action="store_true", help="Print what would change; write nothing")

    args = parser.parse_args()
    if args.command in ("plan", "apply"):
        args.mapping = args.mapping or ([] if args.symbol_dump else [DEFAULT_MAPPING_PATH])
    if args.command == "plan":
        return cmd_plan(args)
    elif args.command == "apply":
        return cmd_apply(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
