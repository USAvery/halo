#!/usr/bin/env python3
"""Print the CEA-360 source for an NTSC (2276) function, for side reading.

This is a lookup, not an oracle: it prints the Halo: Combat Evolved
Anniversary Xbox 360 decompile of the function that best corresponds to an
NTSC target, so a porter can read it next to the Ghidra decompile of the
2276 binary. It never edits kb.json or src/, and it never asserts that the
CEA body IS what 2276 does -- disassembly verification against cachebeta.xbe
remains the only authority (see skill halo-lift, step 4). Per naming-confidence,
a CEA correspondence is structural cross-build evidence, T2 at most -- never
T1 -- even when the match is a confirmed row from rename_mapping.json.

Resolution order for the given `<name_or_addr>`:
  1. The token, taken literally as a name, matches a CEA function name in
     artifacts/cea_corpus/index.json exactly. (Typical case: an already-
     ported NTSC function that shares its semantic name with the CEA source,
     e.g. "game_tick" or "apply_dead_zones".)
  2. The token resolves to an NTSC address (either given directly as hex, or
     looked up as an exact kb.json function name), and that address has a
     row in artifacts/ghidra_groom/rename_mapping.json whose `new_name`
     matches a CEA function name.
  3. Same as (2), against artifacts/cea_propagation/proposals.json (same row
     schema), if that file exists yet.
  4. No match: print the closest CEA names by prefix and exit non-zero.

Usage:
  rtk python3 tools/analysis/cea_body.py game_tick
  rtk python3 tools/analysis/cea_body.py apply_dead_zones --callees
  rtk python3 tools/analysis/cea_body.py 0001d6d0
  rtk python3 tools/analysis/cea_body.py game_tick --header game_globals_t
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent

# cea_corpus_index.py owns corpus-root resolution (--corpus / HALO_CEA_CORPUS /
# tools/analysis/cea_corpus.path) and the corpus staleness fingerprint, since
# cea_propagate_names.py needs the exact same two pieces of logic (finding 8
# requires both this tool and that one to warn on a stale corpus) and three
# copies of the same resolver would drift. This is the one import this tool
# takes from a sibling script; function_name() below stays duplicated rather
# than imported, per this tool's usual import-free preference, because it is
# small enough to not be worth the coupling.
import cea_corpus_index

DEFAULT_INDEX = ROOT / "artifacts" / "cea_corpus" / "index.json"
DEFAULT_RENAME_MAPPING = ROOT / "artifacts" / "ghidra_groom" / "rename_mapping.json"
DEFAULT_PROPOSALS = ROOT / "artifacts" / "cea_propagation" / "proposals.json"
DEFAULT_KB = ROOT / "kb.json"


class MissingInputError(Exception):
  """An input this tool needs (corpus root, index.json, or a CEA source file
  the index points at) is missing or unresolvable, as opposed to a genuine
  "no CEA-360 match" result. main() maps this to exit code 2; a real
  no-match result is exit code 1."""

CEA_DISCLAIMER = (
  "CEA-360 REFERENCE ONLY. This is Halo: Combat Evolved Anniversary's Xbox 360\n"
  "decompile, not the 2276 binary. Its names, control flow, and struct layouts\n"
  "are useful reading, not proof -- treat every detail as unconfirmed until you\n"
  "check it against 2276 disassembly. Per naming-confidence, a CEA correspondence\n"
  "is T2 evidence at most, never T1, and this tool never gates or asserts a match."
)


def function_name(decl: str) -> str:
  """Extract the C identifier from a kb.json declaration (same rule as
  crossbuild_context.py, duplicated here to keep this tool import-free of it
  since the two evolve independently)."""
  return re.split(r"[\s*]+", decl.split("(", 1)[0].strip())[-1]


def normalize_addr(token: str) -> int | None:
  """Parse a hex address in any of the forms this repo's tables use: kb.json's
  "0x1d6d0", rename_mapping's zero-padded "0001d6d0", or a bare token. Returns
  None if the token doesn't look like an address, so name lookups are tried
  first and short hex-looking names aren't misread as addresses."""
  s = token.strip().lower()
  if s.startswith("0x"):
    s = s[2:]
    if not s or any(c not in "0123456789abcdef" for c in s):
      return None
    return int(s, 16)
  # Without an explicit 0x prefix, require a length typical of this repo's
  # addresses (4-8 hex digits) so ordinary short names aren't swept in.
  if 4 <= len(s) <= 8 and all(c in "0123456789abcdef" for c in s):
    return int(s, 16)
  return None


def load_kb(kb_path: Path) -> tuple[dict[int, dict[str, str]], dict[str, dict[str, str]]]:
  """by_name is keyed primarily by the decl-derived identifier (the same
  rule ntsc_callgraph.py and knowledge.py use), since that is what most
  lookups and the --callees table match against. A kb.json row can also
  carry an explicit "name" field that differs from its decl -- usually a
  human-assigned semantic name written before the function's C body was
  filled in, so decl still parses to a placeholder like FUN_xxx or to
  nothing at all. When that explicit name differs from the decl-derived
  one, it is added as a second key pointing at the same entry, so a lookup
  by either spelling resolves -- but only when it does not collide with an
  existing decl-derived name for a *different* function."""
  by_addr: dict[int, dict[str, str]] = {}
  by_name: dict[str, dict[str, str]] = {}
  if not kb_path.exists():
    return by_addr, by_name
  kb = json.loads(kb_path.read_text(encoding="utf-8"))
  for obj in kb.get("objects", []):
    for fn in obj.get("functions", []):
      addr_str = str(fn.get("addr", ""))
      addr_int = normalize_addr(addr_str)
      name = function_name(str(fn.get("decl", "")))
      if addr_int is None or not name:
        continue
      entry = {
        "addr": addr_str,
        "name": name,
        "object": str(obj.get("name", "")),
        "ported": fn.get("ported"),
      }
      by_addr[addr_int] = entry
      by_name[name] = entry
      explicit_name = fn.get("name")
      if explicit_name and explicit_name != name and explicit_name not in by_name:
        by_name[explicit_name] = entry
  return by_addr, by_name


def load_cea_index(index_path: Path) -> dict[str, Any]:
  if not index_path.exists():
    raise MissingInputError(
      f"CEA corpus index not found: {index_path}\n"
      "Run tools/analysis/cea_corpus_index.py first."
    )
  return json.loads(index_path.read_text(encoding="utf-8"))


def check_corpus_staleness(cea_index: dict[str, Any], corpus_root: Path) -> dict[str, Any] | None:
  """Compare index.json's recorded corpus_signature (finding 8) against the
  live corpus on disk.

  The comparison uses the full path-plus-size hash recorded by the indexer,
  so it catches files added, removed, renamed, or edited in place. This is
  deliberately a complete check: a stale source body must never be silently
  presented as if it came from the indexed corpus. See
  cea_corpus_index.compute_corpus_signature().

  Returns one of:
    None                          -- corpus matches, nothing to warn about.
    {"unverifiable": True, ...}   -- index.json predates corpus_signature
                                      (or lacks hash), so there is
                                      nothing recorded to compare against.
                                      Distinct from a clean match so callers
                                      (e.g. --json / lift_pipeline.py) don't
                                      read "no warning" as "verified clean".
    {..mismatch fields..}         -- the live corpus disagrees with what
                                      was recorded.
  """
  recorded = cea_index.get("corpus_signature")
  if not recorded or not recorded.get("hash"):
    return {"unverifiable": True, "reason": "index.json has no recorded corpus_signature.hash"}
  try:
    live = cea_corpus_index.compute_corpus_signature(str(corpus_root))
  except OSError as exc:
    return {"unverifiable": True, "reason": f"could not walk live corpus: {exc}"}
  if (live["file_count"] == recorded.get("file_count")
      and live["hash"] == recorded.get("hash")):
    return None
  return {
    "recorded_hash": recorded.get("hash"),
    "recorded_file_count": recorded.get("file_count"),
    "live_hash": live["hash"],
    "live_file_count": live["file_count"],
  }


def load_rows(path: Path) -> list[dict[str, Any]]:
  """Load a rename_mapping.json / proposals.json row list. Missing file is
  not an error -- proposals.json doesn't exist yet in most trees.

  rename_mapping.json is a bare JSON list. proposals.json (written by
  cea_propagate_names.py propose) is a {"metadata": {...}, "proposals":
  [...]} object as of the seed-conflict/precision-gate fix, so both shapes
  are accepted here; a bare list is used as-is."""
  if not path.exists():
    return []
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except json.JSONDecodeError:
    return []
  if isinstance(data, list):
    return data
  if isinstance(data, dict) and isinstance(data.get("proposals"), list):
    return data["proposals"]
  return []


def find_row_for_addr(rows: list[dict[str, Any]], addr_int: int) -> dict[str, Any] | None:
  for row in rows:
    row_addr = normalize_addr(str(row.get("addr", "")))
    if row_addr == addr_int:
      return row
  return None


def closest_names(token: str, cea_names: list[str], limit: int = 12) -> list[str]:
  """Prefix-based "closest name" search: shrink the prefix until something
  matches, so a typo'd suffix still surfaces the right neighborhood."""
  lowered = token.lower()
  for prefix_len in range(len(lowered), 0, -1):
    prefix = lowered[:prefix_len]
    matches = sorted(n for n in cea_names if n.lower().startswith(prefix))
    if matches:
      return matches[:limit]
  return sorted(cea_names)[:limit]


class Resolution:
  def __init__(self, cea_entry: dict[str, Any], method: str, tier: str,
               detail: str, kb_entry: dict[str, str] | None,
               conflict: dict[str, Any] | None = None):
    self.cea_entry = cea_entry
    self.method = method
    self.tier = tier
    self.detail = detail
    self.kb_entry = kb_entry
    # Set when the exact-name path (clause 1) and the mapping-table path
    # (rename_mapping.json / proposals.json, clauses 2-3) disagree about
    # what this NTSC address's CEA name should be. Both hypotheses are
    # surfaced; the exact-name path still wins (it is checked first), but
    # the tier is labeled CONFLICT so a reader knows there is a live
    # disagreement, mirroring the seed-conflict handling in
    # cea_propagate_names.py's build_seeds().
    self.conflict = conflict


def _find_mapping_conflict(addr_int: int, exact_name: str, cea_by_name: dict[str, Any],
                            rename_rows: list[dict[str, Any]],
                            proposal_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
  for source, rows in (("rename_mapping", rename_rows), ("proposals", proposal_rows)):
    row = find_row_for_addr(rows, addr_int)
    if row is None:
      continue
    new_name = str(row.get("new_name", ""))
    if new_name and new_name != exact_name and new_name in cea_by_name:
      return {"source": source, "row": row, "new_name": new_name}
  return None


def resolve(token: str, cea_index: dict[str, Any], kb_by_addr: dict[int, dict[str, str]],
            kb_by_name: dict[str, dict[str, str]], rename_rows: list[dict[str, Any]],
            proposal_rows: list[dict[str, Any]]) -> Resolution | None:
  cea_by_name = {f["name"]: f for f in cea_index.get("functions", [])}

  # 1. Literal name equals a CEA function name.
  cea_entry = cea_by_name.get(token)
  if cea_entry is not None:
    kb_entry = kb_by_name.get(token)
    detail = "the given name is an exact CEA function-name match"
    if kb_entry is not None:
      detail += f"; kb.json also names {kb_entry['addr']} \"{token}\""
    tier = "name-equality (T2 max)"
    conflict = None
    if kb_entry is not None:
      addr_for_conflict_check = normalize_addr(kb_entry["addr"])
      if addr_for_conflict_check is not None:
        conflict = _find_mapping_conflict(
          addr_for_conflict_check, token, cea_by_name, rename_rows, proposal_rows)
    if conflict is not None:
      row = conflict["row"]
      detail += (f"; CONFLICT: {conflict['source']} row for the same address proposes "
                 f"\"{conflict['new_name']}\" instead (tier={row.get('tier', 'unknown')})")
      tier = "CONFLICT (" + tier + ")"
    return Resolution(cea_entry, "exact-name", tier, detail, kb_entry, conflict)

  # Resolve an NTSC address to try the mapping tables: either the token is
  # hex itself, or it's an exact kb.json name whose address we can look up.
  addr_int = normalize_addr(token)
  kb_entry = None
  if addr_int is not None:
    kb_entry = kb_by_addr.get(addr_int)
  elif token in kb_by_name:
    kb_entry = kb_by_name[token]
    addr_int = normalize_addr(kb_entry["addr"])

  if addr_int is not None:
    row = find_row_for_addr(rename_rows, addr_int)
    if row is not None:
      new_name = str(row.get("new_name", ""))
      cea_entry = cea_by_name.get(new_name)
      if cea_entry is not None:
        tier = str(row.get("tier", "unknown"))
        evidence = row.get("evidence", {}) or {}
        detail_bits = [f"rename_mapping.json row addr={row.get('addr')}",
                        f"old_name={row.get('old_name')}", f"new_name={new_name}"]
        if evidence.get("cea_src"):
          detail_bits.append(f"cea_src={evidence['cea_src']}")
        if evidence.get("cea_lines"):
          detail_bits.append(f"cea_lines={evidence['cea_lines']}")
        detail = "; ".join(detail_bits)
        return Resolution(cea_entry, "rename_mapping", f"{tier} (T2 max)", detail, kb_entry)

    row = find_row_for_addr(proposal_rows, addr_int)
    if row is not None:
      new_name = str(row.get("new_name", ""))
      cea_entry = cea_by_name.get(new_name)
      if cea_entry is not None:
        tier = str(row.get("tier", "unknown"))
        detail = (f"cea_propagation/proposals.json row addr={row.get('addr')} "
                  f"old_name={row.get('old_name')} new_name={new_name}")
        return Resolution(cea_entry, "proposals", f"{tier} (T2 max)", detail, kb_entry)

  return None


def print_banner(res: Resolution | None, token: str, cea_index: dict[str, Any], raw: bool) -> None:
  warn = sys.stderr if raw else sys.stdout
  total = sum(1 for f in cea_index.get("functions", []) if f.get("owner_divergence"))
  print(CEA_DISCLAIMER, file=warn)
  print(
    f"Of the functions in the current CEA corpus index, {total} carry an "
    "OWNER-DIRECTED/OWNER DECISION marker: those files are the port author's own\n"
    "back-ports of a 2276 decision onto the CEA body, not independent Xbox 360\n"
    "evidence, even though they live in the CEA tree.",
    file=warn,
  )
  print(file=warn)
  if res is not None and res.cea_entry.get("owner_divergence"):
    print("!" * 78, file=warn)
    print("OWNER-DIVERGENCE FILE.", file=warn)
    print(
      "This CEA-360 source carries an OWNER-DIRECTED/OWNER DECISION marker: it is\n"
      "the port author's own back-port of a deliberate 2276 decision onto the CEA\n"
      "body, not independent Xbox 360 evidence. See the file's own header comment\n"
      "for the specific decision and its citations.",
      file=warn,
    )
    print("!" * 78, file=warn)
    print(file=warn)


def print_match(res: Resolution) -> None:
  print(f"Match: {res.method} (tier: {res.tier})")
  print(f"  {res.detail}")
  if res.conflict is not None:
    row = res.conflict["row"]
    print("CONFLICT: the exact-name match and a mapping table disagree about this address.")
    print(f"  exact-name hypothesis : {res.cea_entry['name']}")
    print(f"  {res.conflict['source']} hypothesis : {res.conflict['new_name']} "
          f"(row addr={row.get('addr')}, tier={row.get('tier', 'unknown')})")
    print("  This tool follows the exact-name match; treat both as unconfirmed T2 evidence "
          "until checked against 2276 disassembly.")
  if res.kb_entry is not None:
    ported = res.kb_entry.get("ported")
    ported_str = "ported" if ported else ("not ported" if ported is False else "ported flag unset")
    print(f"  NTSC side: {res.kb_entry['name']} @ {res.kb_entry['addr']} ({ported_str}, "
          f"object {res.kb_entry['object']})")
  addr360 = res.cea_entry.get("addr360")
  addr360_str = f"0x{addr360:08x}" if isinstance(addr360, int) else "unknown"
  print(f"CEA function : {res.cea_entry['name']}")
  print(f"CEA file     : {res.cea_entry['file']}")
  print(f"CEA 360 addr : {addr360_str}")
  if res.cea_entry.get("deviation") and not res.cea_entry.get("owner_divergence"):
    print("  (note: this file carries a DEVIATION/CORRECTED-TO-XBOX marker; read the "
          "header comment before trusting a specific detail.)")


def print_callees(res: Resolution, kb_by_name: dict[str, dict[str, str]], out) -> None:
  callees = res.cea_entry.get("callees", [])
  if not callees:
    print("Callees: none recorded.", file=out)
    return
  print(f"Callees ({len(callees)}):", file=out)
  for name in callees:
    kb_entry = kb_by_name.get(name)
    if kb_entry is None:
      print(f"  {name:<48} no kb.json match by name", file=out)
      continue
    ported = kb_entry.get("ported")
    ported_str = "ported" if ported else ("not ported" if ported is False else "ported flag unset")
    print(f"  {name:<48} {kb_entry['addr']}  ({ported_str})", file=out)


def print_header(corpus: Path, struct_name: str) -> None:
  headers_dir = corpus / "headers"
  matches = sorted(headers_dir.rglob(f"{struct_name}.h"))
  if not matches:
    # Case-insensitive fallback.
    matches = sorted(p for p in headers_dir.rglob("*.h") if p.stem.lower() == struct_name.lower())
  if not matches:
    print(f"\nNo header file '{struct_name}.h' found under {headers_dir}", file=sys.stderr)
    return
  for path in matches:
    print(f"\n--- header: {path.relative_to(corpus)} ---")
    print(path.read_text(encoding="utf-8", errors="replace"))


def _json_result(ok: bool, target: str, res: Resolution | None = None,
                  error: str | None = None, missing_input: bool = False,
                  corpus_check: dict[str, Any] | None = None,
                  suggestions: list[str] | None = None) -> dict[str, Any]:
  out: dict[str, Any] = {"ok": ok, "target": target}
  if error is not None:
    out["error"] = error
    out["missing_input"] = missing_input
  if res is not None:
    out["method"] = res.method
    out["tier"] = res.tier
    out["detail"] = res.detail
    out["conflict"] = res.conflict
    out["cea_function"] = res.cea_entry["name"]
    out["cea_file"] = res.cea_entry["file"]
    addr360 = res.cea_entry.get("addr360")
    out["cea_addr360"] = f"0x{addr360:08x}" if isinstance(addr360, int) else None
    out["kb_entry"] = res.kb_entry
  if suggestions is not None:
    out["closest_names"] = suggestions
  # Three distinct states, so a consumer (lift_pipeline.py included) never
  # reads "corpus_stale: null" as "verified clean" when it actually means
  # "index.json has no recorded signature to check against":
  #   corpus_signature_recorded=False              -- nothing to compare (old
  #                                                    index, or the live
  #                                                    walk itself failed)
  #   corpus_signature_recorded=True, corpus_stale=None      -- verified clean
  #   corpus_signature_recorded=True, corpus_stale={...}     -- verified stale
  if corpus_check is None:
    out["corpus_signature_recorded"] = True
    out["corpus_stale"] = None
  elif corpus_check.get("unverifiable"):
    out["corpus_signature_recorded"] = False
    out["corpus_stale"] = None
  else:
    out["corpus_signature_recorded"] = True
    out["corpus_stale"] = corpus_check
  return out


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("target", help="NTSC kb.json function name, or an address (hex, with or "
                                      "without 0x / zero-padding)")
  parser.add_argument("--corpus", default=None,
                       help="CEA corpus root (src/engine of the CEA tree). If omitted, falls "
                            "back to the HALO_CEA_CORPUS environment variable, then to "
                            "tools/analysis/cea_corpus.path.")
  parser.add_argument("--index", default=DEFAULT_INDEX, type=Path,
                       help="artifacts/cea_corpus/index.json path")
  parser.add_argument("--rename-mapping", default=DEFAULT_RENAME_MAPPING, type=Path)
  parser.add_argument("--proposals", default=DEFAULT_PROPOSALS, type=Path)
  parser.add_argument("--kb", default=DEFAULT_KB, type=Path)
  parser.add_argument("--callees", action="store_true",
                       help="list CEA callees with their NTSC address if resolvable")
  parser.add_argument("--header", metavar="STRUCT",
                       help="also print headers/<STRUCT>.h from the CEA corpus")
  parser.add_argument("--raw", action="store_true",
                       help="print only the CEA source (and any --header file) to stdout; "
                            "warnings still go to stderr")
  parser.add_argument("--json", action="store_true",
                       help="print one JSON object describing the match (or the failure) to "
                            "stdout instead of human-readable text; does not print the CEA "
                            "source body. Meant for tools/lift_pipeline.py to parse instead of "
                            "scraping human-readable output.")
  args = parser.parse_args()

  try:
    corpus_root = Path(cea_corpus_index.resolve_corpus_root(args.corpus))
  except SystemExit as e:
    if args.json:
      print(json.dumps(_json_result(False, args.target, error=str(e), missing_input=True)))
    else:
      print(str(e), file=sys.stderr)
    return 2

  try:
    cea_index = load_cea_index(args.index)
  except MissingInputError as e:
    if args.json:
      print(json.dumps(_json_result(False, args.target, error=str(e), missing_input=True)))
    else:
      print(str(e), file=sys.stderr)
    return 2

  kb_by_addr, kb_by_name = load_kb(args.kb)
  rename_rows = load_rows(args.rename_mapping)
  proposal_rows = load_rows(args.proposals)

  corpus_check = check_corpus_staleness(cea_index, corpus_root)
  if corpus_check is not None and not corpus_check.get("unverifiable"):
    print(
      f"WARNING: the CEA corpus on disk does not match index.json's recorded "
      f"corpus_signature (recorded {corpus_check['recorded_file_count']} files, "
      f"hash {corpus_check['recorded_hash']}; live corpus has "
      f"{corpus_check['live_file_count']} files, hash {corpus_check['live_hash']}). "
      "Re-run tools/analysis/cea_corpus_index.py to refresh the index before trusting "
      "this match.",
      file=sys.stderr,
    )

  res = resolve(args.target, cea_index, kb_by_addr, kb_by_name, rename_rows, proposal_rows)

  if res is None:
    cea_names = [f["name"] for f in cea_index.get("functions", [])]
    suggestions = closest_names(args.target, cea_names)
    if args.json:
      print(json.dumps(_json_result(False, args.target,
                                     error=f"No CEA match for '{args.target}' via exact name, "
                                           "rename_mapping.json, or proposals.json.",
                                     missing_input=False, corpus_check=corpus_check,
                                     suggestions=suggestions)))
      return 1
    print_banner(None, args.target, cea_index, args.raw)
    out = sys.stderr if args.raw else sys.stdout
    print(f"No CEA match for '{args.target}' via exact name, rename_mapping.json, "
          "or proposals.json.", file=out)
    print("Closest CEA names by prefix:", file=out)
    for name in suggestions:
      print(f"  {name}", file=out)
    return 1

  cea_path = corpus_root / res.cea_entry["file"]
  if not cea_path.exists():
    error = f"CEA source file listed in the index is missing on disk: {cea_path}"
    if args.json:
      print(json.dumps(_json_result(False, args.target, res=res, error=error,
                                     missing_input=True, corpus_check=corpus_check)))
    else:
      print(error, file=sys.stderr)
    return 2

  if args.json:
    print(json.dumps(_json_result(True, args.target, res=res, corpus_check=corpus_check)))
    return 0

  print_banner(res, args.target, cea_index, args.raw)

  if not args.raw:
    print_match(res)
    print()

  if not args.raw:
    print(f"--- source: {res.cea_entry['file']} ---")
  print(cea_path.read_text(encoding="utf-8", errors="replace"))

  if args.callees:
    out = sys.stderr if args.raw else sys.stdout
    print(file=out)
    print_callees(res, kb_by_name, out)

  if args.header:
    print_header(corpus_root, args.header)

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
