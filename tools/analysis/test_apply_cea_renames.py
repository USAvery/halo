"""Focused tests for same-build symbol-dump imports."""

import copy
from pathlib import Path

from tools.analysis import apply_cea_renames as subject


def _exact_row(addr: int, name: str) -> dict:
  return {
    "addr": f"{addr:08x}",
    "new_name": name,
    "method": "exact_address",
    "tier": "confirmed",
    "_mapping_file": "fixture.txt",
  }


def test_symbol_dump_parser_keeps_only_c_identifiers(tmp_path: Path) -> None:
  dump = tmp_path / "functions.txt"
  dump.write_text(
    "_actor_update\t.text\t00012000\n"
    "__private_helper\t.text\t00012010\n"
    "_initterm(x,x)\t.text\t00012020\n"
    "sub_12030\t.text\t00012030\n"
  )

  rows = subject.load_symbol_dump_rows([dump])

  assert [(row["addr"], row["new_name"], row["method"], row["tier"]) for row in rows] == [
    ("00012000", "actor_update", "exact_address", "confirmed"),
    ("00012010", "_private_helper", "exact_address", "confirmed"),
  ]
  assert subject.METHOD_TO_SOURCE["exact_address"] == "2276-symbol-dump"
  assert subject.METHOD_EVIDENCE_TIER["exact_address"] == "T1"


def test_exact_address_candidates_require_live_fun_record() -> None:
  kb = {
    "objects": [{
      "name": "fixture.obj",
      "functions": [
        {"addr": "0x12000", "decl": "void FUN_00012000(void);"},
        {"addr": "0x12010", "decl": "void already_named(void);"},
      ],
    }],
    "0x12020": {"addr": "0x12020", "decl": "void FUN_00012020(void);"},
  }
  rows = [
    _exact_row(0x12000, "actor_update"),
    _exact_row(0x12010, "should_not_replace_named"),
    _exact_row(0x12020, "legacy_only"),
    _exact_row(0x12030, "not_present"),
  ]

  candidates, not_found = subject.compute_candidates(kb, rows)

  assert [(c["kb_addr"], c["new_name"]) for c in candidates] == [
    ("0x12000", "actor_update"),
  ]
  assert [row["new_name"] for row in not_found] == ["not_present"]


def test_exact_address_dry_run_leaves_kb_unchanged() -> None:
  kb = {
    "objects": [{
      "name": "fixture.obj",
      "functions": [{"addr": "0x12000", "decl": "void FUN_00012000(void);"}],
    }],
  }
  candidates, not_found = subject.compute_candidates(
    kb, [_exact_row(0x12000, "actor_update")]
  )
  before = copy.deepcopy(kb)

  results, file_updates = subject.apply_batch_rows(kb, candidates, {}, dry_run=True)

  assert not not_found
  assert kb == before
  assert file_updates == {}
  assert results[0]["decl_changed"]


def test_exact_address_source_batches_stay_within_owner_object() -> None:
  kb = {
    "objects": [
      {"name": "alpha.obj", "functions": [
        {"addr": "0x12000", "decl": "void FUN_00012000(void);"},
      ]},
      {"name": "beta.obj", "functions": [
        {"addr": "0x12010", "decl": "void FUN_00012010(void);"},
      ]},
    ],
  }
  candidates, not_found = subject.compute_candidates(kb, [
    _exact_row(0x12000, "alpha_update"),
    _exact_row(0x12010, "beta_update"),
  ])
  token_to_files = {
    "FUN_00012000": {"src/shared.c"},
    "FUN_00012010": {"src/shared.c"},
  }

  batches = subject.compute_batches(candidates, token_to_files)

  assert not not_found
  assert batches[0] == []
  assert [[c["object"] for c in batch] for batch in batches[1:]] == [
    ["alpha.obj"],
    ["beta.obj"],
  ]
