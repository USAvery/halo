#!/usr/bin/env python3
"""Unit tests for fingerprinted research evidence reuse."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from research_bundle import (
    COHORT_ENV,
    ResearchCache,
    ResolvedTarget,
    context_fingerprint,
    retrieval_cohort,
    score_fingerprint,
)


def _target(*, decl: str = "void FUN_00000011(void);") -> ResolvedTarget:
    return ResolvedTarget(
        addr="0x11", name="FUN_00000011", object_name="test.obj",
        source_path="src/halo/test.c", decl=decl,
        kb_entry={"addr": "0x11", "decl": decl}, has_reg_args=False,
    )


def _kb(target: ResolvedTarget) -> dict:
    return {"objects": [{
        "name": target.object_name, "source": "test.c",
        "functions": [target.kb_entry],
    }]}


class FingerprintTests(unittest.TestCase):
    def test_retrieval_cohort_is_deterministic_by_address(self):
        for addr in ("0x10", "0x11", "0x124e90"):
            self.assertEqual(retrieval_cohort(addr), retrieval_cohort(addr))
        # Assignment keys off the normalised address, so zero-padding and case
        # must not move a target between arms.
        self.assertEqual(retrieval_cohort("0x10"), retrieval_cohort("0x00000010"))
        self.assertEqual(retrieval_cohort("0x124e90"), retrieval_cohort("0x124E90"))

    def test_retrieval_cohort_splits_aligned_addresses(self):
        """Both arms must be populated by REAL (aligned, even) addresses.

        The original ``int(addr, 16) % 2`` test assigned on address parity.
        Function entry points are aligned, so every kb.json address is even and
        100% of targets landed in "retrieval" -- 727 outcomes with no control.
        """
        addrs = [f"0x{a:x}" for a in range(0x100000, 0x100000 + 0x1000, 0x10)]
        cohorts = [retrieval_cohort(a) for a in addrs]
        self.assertEqual(set(cohorts), {"retrieval", "control"})
        share = cohorts.count("retrieval") / len(cohorts)
        self.assertTrue(0.35 < share < 0.65, f"lopsided split: {share:.2f}")

    def test_retrieval_cohort_env_override(self):
        import os
        original = os.environ.get(COHORT_ENV)
        try:
            for forced in ("retrieval", "control", "off"):
                os.environ[COHORT_ENV] = forced
                self.assertEqual(retrieval_cohort("0x10"), forced)
                self.assertEqual(retrieval_cohort("0x11"), forced)
            # An unrecognised value must NOT pin the experiment to one arm.
            os.environ[COHORT_ENV] = "typo"
            self.assertEqual(retrieval_cohort("0x10"),
                             retrieval_cohort("0x00000010"))
            self.assertIn(retrieval_cohort("0x10"), ("retrieval", "control"))
        finally:
            if original is None:
                os.environ.pop(COHORT_ENV, None)
            else:
                os.environ[COHORT_ENV] = original

    def test_context_stability_and_all_required_invalidations(self):
        base = {
            "xbe_md5": "xbe-a",
            "target": {"addr": "0x11", "bounds": {"end": "0x20"},
                       "function_bytes_sha256": "bytes-a"},
            "kb_entry": {"addr": "0x11", "decl": "void f(void);"},
            "callee_declarations_sha256": "callees-a",
            "extractor_version": "extractor-a",
            "ghidra_analysis_version": "ghidra-a",
        }
        baseline = context_fingerprint(base)
        self.assertEqual(baseline, context_fingerprint(dict(base)))
        mutations = [
            ("xbe_md5", "xbe-b"),
            ("target", {"addr": "0x11", "bounds": {"end": "0x21"},
                        "function_bytes_sha256": "bytes-b"}),
            ("kb_entry", {"addr": "0x11", "decl": "int f(void);"}),
            ("callee_declarations_sha256", "callees-b"),
            ("extractor_version", "extractor-b"),
            ("ghidra_analysis_version", "ghidra-b"),
        ]
        for key, value in mutations:
            changed = dict(base)
            changed[key] = value
            self.assertNotEqual(baseline, context_fingerprint(changed), key)

    def test_score_stability_and_attempt_invalidations(self):
        base = {
            "target_addr": "0x11", "candidate_source_sha256": "source-a",
            "kb_declaration_sha256": "decl-a", "compiler_options": "opts-a",
            "verifier_version_sha256": "verify-a", "reference_sha256": "ref-a",
        }
        baseline = score_fingerprint(base)
        self.assertEqual(baseline, score_fingerprint(dict(base)))
        for key in ("candidate_source_sha256", "kb_declaration_sha256",
                    "compiler_options", "verifier_version_sha256",
                    "reference_sha256"):
            changed = dict(base)
            changed[key] += "-changed"
            self.assertNotEqual(baseline, score_fingerprint(changed), key)


class StoreTests(unittest.TestCase):
    def test_outcome_stats_keep_provider_model_identifiers_opaque(self):
        with tempfile.TemporaryDirectory() as td:
            cache = ResearchCache(Path(td) / "shared")
            cache.record_outcome(
                target="0x11", cohort="control", outcome="committed", tokens=100,
                model_id="gpt-5.6-luna-medium", effort="medium", route="atlas_fix")
            cache.record_outcome(
                target="0x12", cohort="control", outcome="parked", tokens=50,
                model_id="provider-b/model-x", effort="high", route="semantic_relift")
            stats = cache.stats()
            self.assertEqual(stats["models"]["gpt-5.6-luna-medium"], {
                "attempts": 1, "tokens": 100, "accepted": 1,
                "accepted_per_100k_tokens": 1000.0,
            })
            self.assertEqual(stats["models"]["provider-b/model-x"]["attempts"], 1)

    def test_control_bundle_carries_no_neighbors(self):
        """The control arm must never receive neighbors, even if the loader
        would return some — that emptiness IS the experiment's independent
        variable, so it cannot depend on the index having no match."""
        import os
        neighbor = {"addr": "0x999", "name": "helper", "decl": "void helper(void);",
                    "c_source": "void helper(void) {}"}
        results = {}
        original = os.environ.get(COHORT_ENV)
        try:
            for forced in ("retrieval", "control", "off"):
                os.environ[COHORT_ENV] = forced
                with tempfile.TemporaryDirectory() as td:
                    cache = ResearchCache(Path(td) / "shared", Path(td) / "repo")
                    target = _target()
                    results[forced] = cache.prepare_target(
                        target, _kb(target),
                        build_ghidra=lambda _t: {
                            "decompile_c": "void f(void) {}", "disassembly": "ret",
                            "callers": [], "callees": []},
                        source_checker=lambda _addr, _name: None,
                        retrieval_loader=lambda _body, _obj: [neighbor],
                    )["retrieval"]
        finally:
            if original is None:
                os.environ.pop(COHORT_ENV, None)
            else:
                os.environ[COHORT_ENV] = original

        self.assertEqual(results["retrieval"]["neighbor_ids"], ["0x999"])
        self.assertIn("neighbor", results["retrieval"])
        for arm in ("control", "off"):
            self.assertEqual(results[arm]["cohort"], arm)
            self.assertEqual(results[arm]["neighbor_ids"], [], arm)
            self.assertNotIn("neighbor", results[arm])

    def test_retrieval_gate_stays_closed_without_two_fifty_target_cohorts(self):
        with tempfile.TemporaryDirectory() as td:
            stats = ResearchCache(Path(td) / "shared").stats()
            self.assertFalse(stats["retrieval_decision_eligible"])
            self.assertFalse(stats["recommend_unconditional_retrieval"])

    def test_hit_uses_zero_ghidra_builds_and_stale_kb_rebuilds(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            cache = ResearchCache(Path(td) / "shared", repo)
            target = _target()
            calls = []

            def build(_target_value):
                calls.append(_target_value.addr)
                return {"decompile_c": "void f(void) {}", "disassembly": "ret",
                        "callers": [], "callees": []}

            first = cache.prepare_target(
                target, _kb(target), build_ghidra=build,
                source_checker=lambda _addr, _name: None,
                retrieval_loader=lambda _body, _obj: [],
            )
            second = cache.prepare_target(
                target, _kb(target), build_ghidra=build,
                source_checker=lambda _addr, _name: None,
                retrieval_loader=lambda _body, _obj: [],
            )
            self.assertEqual(first["cache"], {"context": "miss", "ghidra_builds": 1})
            self.assertEqual(second["cache"], {"context": "hit", "ghidra_builds": 0})
            self.assertEqual(len(calls), 1)

            changed = _target(decl="int FUN_00000011(void);")
            stale = cache.prepare_target(
                changed, _kb(changed), build_ghidra=build,
                source_checker=lambda _addr, _name: None,
                retrieval_loader=lambda _body, _obj: [],
            )
            self.assertEqual(stale["cache"], {"context": "miss", "ghidra_builds": 1})
            self.assertEqual(len(calls), 2)
            self.assertEqual(cache.stats()["cache"], {
                "hits": 1, "misses": 2, "ghidra_builds": 2,
            })

    def test_scores_cross_worktrees_only_on_full_attempt_match(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            shared = base / "shared"
            target = _target()
            source = b"void FUN_00000011(void) {}\n"
            source_sha = hashlib.sha256(source).hexdigest()

            def make_repo(name: str, source_bytes: bytes) -> Path:
                repo = base / name
                (repo / "src" / "halo").mkdir(parents=True)
                (repo / "src" / "halo" / "test.c").write_bytes(source_bytes)
                (repo / "tools" / "verify").mkdir(parents=True)
                (repo / "tools" / "verify" / "vc71_verify.py").write_text("v1")
                (repo / "tools" / "verify" / "xbe_reference.py").write_text("r1")
                (repo / "tools" / "verify" / "function_bounds.json").write_text("{}")
                return repo

            repo_a = make_repo("worktree-a", source)
            score_dir = repo_a / "artifacts" / "score_context"
            score_dir.mkdir(parents=True)
            (score_dir / f"{target.name}.json").write_text(json.dumps({
                "schema": 2, "name": target.name, "addr": target.addr,
                "candidate_source_sha256": source_sha, "scores": {"official_pct": 90},
            }))
            cache_a = ResearchCache(shared, repo_a)
            attempt_a, published = cache_a.publish_local_score(target)
            self.assertIsNotNone(published)

            repo_b = make_repo("worktree-b", source)
            cache_b = ResearchCache(shared, repo_b)
            attempt_b, reused = cache_b._matching_score(target)
            self.assertEqual(attempt_a, attempt_b)
            self.assertIsNotNone(reused)

            repo_c = make_repo("worktree-c", source + b"/* changed */\n")
            cache_c = ResearchCache(shared, repo_c)
            attempt_c, reused_changed = cache_c._matching_score(target)
            self.assertNotEqual(attempt_a, attempt_c)
            self.assertIsNone(reused_changed)

    def test_legacy_score_pack_is_a_miss(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            source_path = repo / "src" / "halo" / "test.c"
            source_path.parent.mkdir(parents=True)
            source_path.write_text("void FUN_00000011(void) {}\n")
            score_dir = repo / "artifacts" / "score_context"
            score_dir.mkdir(parents=True)
            (score_dir / "FUN_00000011.json").write_text(json.dumps({
                "schema": 2, "name": "FUN_00000011", "scores": {"official_pct": 99},
            }))
            cache = ResearchCache(Path(td) / "shared", repo)
            _attempt, published = cache.publish_local_score(_target())
            self.assertIsNone(published)


if __name__ == "__main__":
    unittest.main()
