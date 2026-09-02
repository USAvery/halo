---
name: halo-verify-debug
tier: agent
triggers: ["vc71", "vc71_verify", "low match", "low-match", "match percent", "objdiff", "delink", "delinked", "verify lane"]
description: "/verify, VC71, delink, objdiff, lift_pipeline, equivalence, golden tests, dual-oracle, low-match, behavior/runtime failure: verification ladder and regression debugging workflow."
---

# Halo Verify And Debug

Use this skill for lift verification, XDK/delink comparison, Option 3 fallback,
or regression investigation. Doctrine and evidence rules live in
`halo-lift`; this skill covers the operational verification and debugging
procedures.

Passing validation reduces risk, but it is not proof of behavioral equivalence
unless the target also has strong delink, golden, or runtime coverage.

## Faithfulness Standard & Expected Differences

Judge a match by **identical logic/operations**, not identical byte output. Expected and acceptable differences versus original executable disassembly:
- **Import Indirection:** Re-implemented globals are reached via `mov eax,[__imp__global]; mov ...,(eax)` instead of direct absolute `mov ...,[0xADDR]`. This is inherent to patch redirects and present in every accepted function.
- **Equivalent Codegen:** Compiler instruction choices (e.g. coalescing adjacent byte stores into a word store, `cmp $0, mem` vs `mov`/`test`, `add esp, 4` vs `pop`).

## Verification priority

Prefer real Xbox via XBDM/RDCP over xemu+ISO whenever a console is on the
network. The order is:

1. **XBDM deploy + probe** — build, deploy to Xbox via `/deploy`, then probe
   with `/xbdm <mode>` commands. Fastest iteration, real hardware, no ISO needed.
2. **xemu + ISO** — only if no Xbox is available or XBDM cannot reach it.

## Verification lanes

The user-facing command surface is consolidated under `/verify`:

- `/verify <target>` or `/verify normal <target>` for the normal lift pipeline.
- `/verify structural <target> <new_address>` for explicit patched-XBE address verification.
- `/verify hazards` for `check_lift_hazards.py`.
- `/verify delink <target>` for delink export and reference mapping.
- `/verify equivalence <target>` for Unicorn differential testing; use xemu
  virtual `memsave` (never physical `pmemsave`) or XBDM `getmem` live memory
  captures when zero-filled globals under-cover live paths.
- `/verify golden <target>` for runtime oracle comparison through
  `tools/verify/run_golden_tests.py`.
- `/verify dual-oracle <target>` for same-process original-vs-candidate
  runtime comparison once a target has a dual-oracle harness case.
- `/verify option3 <target>` for legacy runtime/xemu fallback.
- `/verify failure <artifact_dir>` for failed artifact triage.

### Normal post-lift validation

Run:

`rtk python3 tools/lift_pipeline.py --target <target> --no-metadata-update --verify-policy auto <extra_flags>`

Report:

- build stage result
- ABI audit result
- `vc71_verify` result and match percentage if available
- low-match policy result
- behavior/runtime check result if requested
- summary path under `artifacts/lift_runs/.../summary.json`

### Score-improvement gate

Use this after a verified lift when making deliberate source-level changes to
improve an existing VC71 score. First record the whole-TU baseline, then make
one binary-backed candidate edit and gate it:

```bash
rtk python3 tools/verify/score_improve.py baseline \
  --source <file.c> --output artifacts/score_improve/<func>-baseline.json
rtk python3 tools/verify/score_improve.py check \
  --baseline artifacts/score_improve/<func>-baseline.json \
  --source <file.c> --target <func> \
  --output artifacts/score_improve/<func>-check.json
```

`check` rejects a target that gains less than 0.01pp, any missing or regressed
function score in the source file, or an increased warning count. Restore a
failed candidate before pursuing another recipe. Use `lift-score-improve` to
choose the next binary-backed lever from the score-context pack.

### Explicit structural verification

Use this when the lifted function address in the patched XBE is known:

`rtk python3 tools/lift_pipeline.py --target <target> --verify-auto --verify-new-address <new_address> --no-metadata-update <extra_flags>`

Report the verify payload path, `verify_lift` stage result, and summary path.

### Hazard scan

Run after source edits or when reviewing auto-lift output:

`rtk python3 tools/audit/check_lift_hazards.py`

Treat intrinsic calls, undersized buffers, duplicate suspicious arguments, and
pointer-as-float warnings as blockers until investigated against disassembly.

### Equivalence with live memory replay

Use this when Unicorn/Z3 equivalence is applicable but zero-filled global memory
only reaches early exits or weak coverage:

`rtk python3 tools/equivalence/unicorn_diff.py <target> --allow-stubs --mem-trace --state-snapshot artifacts/snapshots/<name>.json`

Capture selected memory regions from a live xemu engine state with the
VIRTUAL-memsave tools: `tools/equivalence/memsave_snapshot.py` (plan →
capture) or `tools/equivalence/qmp_capture.py`. These captures are selected
memory regions, not QEMU VM snapshots. **Never use QMP `pmemsave`
(physical)** — Cerbios does not identity-map game VA on this dev box, so
physical reads return wrong bytes (verified 2026-06-07). XBDM `getmem` is the
fallback on real hardware only. Do not use `savevm`/`loadvm` for oracle
testing because those restore old loaded-XBE code pages and invalidate
original-vs-candidate comparisons.

When no live capture reaches the branch you need, hand-craft a snapshot
instead — see skill `lift-synthetic-equivalence` (regions ≥8B, pointer-param
content overrides, sibling-asymmetry handling, BIPED_SIBLING_RESOLVE=1).

Report:

- snapshot path and captured region intent
- coverage and confidence before/after the snapshot
- memory-trace differential result
- whether remaining gaps require a live runtime oracle instead of more seeds

### Runtime golden and dual-oracle checks

Use runtime golden checks for functions whose behavior depends on live engine
state or Xbox-side effects:

`rtk python3 tools/verify/run_golden_tests.py --target <target>`

For high-value or stateful targets, prefer a dual-oracle harness case when one
exists: one XBE should clone inputs, call the original implementation, restore
inputs, call the candidate implementation, and compare return values, output
buffers, selected globals, and structured debug records inside the same
initialized engine state.

Report:

- runtime artifact directory under `artifacts/runtime_oracle/`
- oracle/candidate or dual-oracle pass/fail summary
- first structured mismatch, memory mismatch, crash, or assertion
- whether real Xbox XBDM confirmation is still required

### Option 3 fallback ladder

Use Option 3 for runtime/xemu fallback only. Prefer the lift pipeline and XDK
verify for structural proof.

Run:

`rtk python3 tools/verify/verify_option3.py --target <target> <extra_flags>`

Report:

- stage results for `build`, `build_iso`, `objdiff`, `xemu_load_reset`,
  and `assert_tripwire`
- PASS or FAIL verdict
- summary path under `artifacts/verify_option3/.../summary.json`

Notes:

- Add `--objdiff-reference <path>` and `--objdiff-candidate <path>` when a
  delinked reference object exists.
- Add `--load-into-xemu` to hot-load and reset via `tools/xbox/xemu_qmp.py`.
- Use `--skip-build` or `--skip-iso` for quick reruns when artifacts already
  exist.

### Failure classification

- Build failure: fix the compile error only; do not rewrite the lift from scratch.
- ABI failure: verify `kb.json` declaration, `@<reg>` annotations, caller setup, and callee thunks.
- XDK `[FPU-WARN]`: verify x87 operand order, push-then-fstp arguments, and cross-product/subtraction order.
- `[LOADW-WARN]` (`--loadw-only`): a field narrowed to int16/int8 in the original but read wider in the lift (or vice versa) — verify the C type against disassembly (lift-learnings §24).
- `[IMM-WARN]` (`--imm-only`): a large inline constant (float bit-pattern or magic) differs between the lift and the original. Both sides are VC71 codegen, so it is a wrong numeric literal the LCS % aligns away — verify the source literal against the disassembly immediate (lift-learnings §25). Very low false-positive; treat as a near-certain source bug.
- Low match: inspect objdiff/XDK output for branch shape, memory access offsets, and missing side effects.
- Behavior/runtime failure: prefer XBDM state probes before xemu unless no console is reachable.

## Delink workflow

Before running delink comparison, verify:

- a live Ghidra GUI session is open on `cachebeta.xbe` or `default.xbe`
- the delinker plugin is enabled
- the project has been built so the candidate `.obj` exists

Then:

1. Resolve the target and function body range.
2. Export the delinked reference object under `artifacts/delinker/`.
3. Compare it against the built candidate object.
4. Focus on structural mismatches that affect field offsets, branch shape,
   and memory access behavior.

Do not save the Ghidra project after a delink export run.

## Regression debugging workflow

1. Start with git history before live probing.
2. Inspect recent commits touching `kb.json`, `src/`, types, or prototypes.
3. Check likely regression classes (see `halo-lift` evidence policy for
   labeling):
   - wrong calling convention or arg count
   - wrong return type or operand width
   - struct size, packing, or field offset drift
   - wrong `HDATA` indirection level
   - missing side effects or empty stubs
   - `@<reg>` thunk without implementation
4. Cross-check suspects in Ghidra disassembly.
5. Use live probing only when static evidence is insufficient.
6. Prefer XBDM probing on real Xbox over xemu whenever possible.
7. For reproducible runtime regressions, prefer replaying a matching per-level
   input recording from `input-recordings/` before manually driving the level.

Useful probes (XBDM preferred):

- `/xbdm status` — check stop state before context reads
- `/xbdm context` — read registers after a crash
- `/xbdm mem <addr> <len>` — inspect memory at a suspect address
- visual check via `rtk python3 tools/xbox/xbdm_screenshot.py --host <ip> --images 5 --png`
- input replay via native `state.data` sentinels; see `docs/xbox-pad.md`

Useful xemu probes (fallback only):

- `rtk python3 tools/xbox/xbdm_screenshot.py --host 127.0.0.1 --images 5 --png` — visible state
- `mcp__xemu__xemu_read_serial()` — serial assertions
- `mcp__xemu__xemu_send_monitor_command("info registers")` — register state
- `mcp__xemu__xemu_send_monitor_command("x /16xw 0x<addr>")` — memory inspection

## Debugging guardrails

Follow the `halo-lift` doctrine: fix only what evidence supports, prefer
narrow changes, do not repack or reorder structs without binary proof. If the
hypothesis is too weak to fix safely, stop and say so.

## Output expectations

Report:

- symptom
- commits investigated
- root cause with Confirmed, Inferred, and Uncertain labels
- exact fix made
- validation performed
- remaining risk or follow-up

## Lane Detail Moved From CLAUDE.md (2026-09-02)

### VC71 verify — warnings and reference derivation
After lifting FPU-heavy functions (geometry, math, projections), run
`rtk python3 tools/verify/vc71_verify.py src/path/to/file.c`. Review any
`[FPU-WARN]` (operand-order bugs), `[LOADW-WARN]` (int vs int16/int8
field-width bugs; `--loadw-only`), `[IMM-WARN]` (wrong float/magic numeric
literal; `--imm-only`), and `[SHAPE-WARN]` (call-count delta = a dropped or
wrongly aimed call, and reference-only stores to an incoming param slot = the
original reassigns a parameter; `--shape-only`, see lift-learnings 49-50)
output. **A call-count delta on a function with fewer than ~12 reference
instructions is a dropped call, not a codegen artifact** — decode the
reference's E8/E9 targets before accepting any tail-call or inlining
explanation (`hs_dispose` sat at 66.7% with one of its two calls missing).
References are derived automatically from the pristine XBE + the committed
bounds table (`tools/verify/function_bounds.json`) — no delinked export is
needed for scoring. `vc71_verify.py` regenerates `build/generated/decl.h` from
kb.json on every run (`--skip-decl-regen` opts out), so a kb.json prototype edit
can no longer be compiled against a stale header.

### A gate regression with no matching source change is a harness bug until it reproduces
`vc71_regression.py check` fans out 8 `vc71_verify` workers; until 2026-08-19
each rewrote `build/generated/decl.h` while its siblings compiled against it, so
two runs of the same command on the same tree reported 24 vs 26 regressions with
only 3 in common — and the wrong scores were then memoized under a valid-looking
key and served back to serial runs. Any new parallel fan-out over `vc71_verify`
MUST pin the header once in the parent and pass `--skip-decl-regen` to workers
(see `vc71_regression._pin_decl_header`). When triaging an unexplained
regression, run the gate twice and diff the `✗` lists, and use
`VC71_NO_MEASURE_MEMO=1` to tell "the measurement is wrong" from "the memo is
serving an old wrong measurement". See lift-learnings 52.

### A verify run that scores ZERO functions in a TU is a compile failure until proven otherwise
Read the first `cl.exe` diagnostic before investigating the reference side: a
reference problem degrades or drops individual functions, it never blanks a
whole TU. The classic cause is a stale `decl.h` (see §41 in
`docs/lift-learnings.md`) — a widened kb.json prototype turns every call site
into a hard error, and the empty result reads like a missing delinked reference
when the delinked object is fine.

### Bounds-table precondition (REQUIRED before VC71 verify)
Before running `vc71_verify.py` for any newly lifted target, confirm the
function's address has an entry in the committed bounds table:

```
rtk jq '."0x<addr>"' tools/verify/function_bounds.json
```

If missing (kb.json gained functions since the table was generated), regenerate
with `rtk python3 tools/verify/function_bounds.py` and commit the updated table
— the scoring reference is derived from it. Delinked COFF exports
(`mcp__ghidra-live__export_delinked_object`) are still required for the
**equivalence lane** (unicorn/z3 execute the oracle object) and for objdiff —
not for VC71 scoring.

### Permuter (`/verify permute`)
Last-mile match optimizer. Use ONLY when VC71 match is in **[85, 98]%**. The
reference COFF is derived automatically from the XBE (same reference VC71
scoring uses); a delinked object is opt-in via `--delinked-ref`. Below 85% the
lift has a structural bug — fix it first; permuter cannot recover from real
correctness issues. Above 98% it is not worth the cycles. Never accept a
permutation that lowers the existing match; always re-run the lift pipeline
against the new source.

### Equivalence (`/verify equivalence`)
Unicorn-Engine behavioral differential with seeded inputs, coverage tracking,
and concolic feedback. Use when byte-match is weak evidence: FPU-heavy code,
hashes/serializers, or structurally capped lifts (e.g. SEH wrappers stuck at
~55%). Works for both leaf and non-leaf functions:
- **Pure leaves:** `rtk python3 tools/equivalence/unicorn_diff.py <target> --seeds 100`
- **Non-leaf or FPU-heavy:** `rtk python3 tools/equivalence/unicorn_diff.py <target> --seeds 100 --allow-stubs --float-tolerance 32`.
  `--allow-stubs` stubs known callees (csmemcpy, fabs, _chkstk, etc.) and seeds
  known XBE globals. `--float-tolerance N` compares float* scratch buffers with
  N ULP tolerance instead of byte-exact (recommended: 16–32 for typical
  geometry, up to 256 for long chains), accounting for x87 rounding differences
  across compiler versions. With `--allow-stubs`, a stub-argument differential
  records each stubbed callee's register and stack args in oracle and candidate
  and fails seeds on mismatch, catching swapped, dropped, or wrong-constant call
  arguments that return-value and mem-trace comparison miss; disable with
  `--no-stub-arg-trace`.
- **Coverage & confidence:** Each run reports code coverage % and a confidence
  tier (high/moderate/weak). If coverage < 60%, a **concolic Phase 2**
  automatically injects non-zero values into zero-filled global memory to reach
  untested branches. The confidence tier and coverage are persisted to
  `leaf_cache.json` and shown in pipeline output.
- **Memory-trace differential:** `--mem-trace` (enabled by default in
  pipeline/batch) compares all non-stack memory writes between oracle and
  candidate, catching side-effect bugs (wrong struct offset, missing writes)
  that return-value comparison misses.
- **State snapshots:** `--state-snapshot path.json` loads real game-state memory
  (captured from xemu) instead of zero-fill. Use for functions that depend on
  complex runtime state (linked lists, hash tables). Capture with
  `tools/equivalence/state_snapshot.py`.
- Each run records leaf classification and confidence to
  `tools/equivalence/leaf_cache.json`, boosting pure leaves by `+5 eq_pure_leaf`
  and high-confidence results by `+3 eq_high_conf` in future selector runs.
- **Verification decision:** Match ≥99% → done (byte-match sufficient);
  [85, 98]% with delinked ref → try `/verify permute` first; [85, 98]% pure leaf
  FPU-heavy → `/verify equivalence` first; <85% → investigate lift (don't
  permute); structurally capped (~55%) → equivalence to prove behavior.
  **Interpret confidence:** `high` = strong evidence; `moderate` = concolic
  improved coverage but returns monotonic; `weak` = only early-exit path tested,
  needs investigation or live memory replay.

### Hazard scan — checks and their lift-learnings sections
XCALL (§1), buffer-alias (§2), intrinsics (table), duplicate-args (§3),
pointer-as-float (§4), frame-size (§2 stack), callee-output-size (projection
§5), x87-math, void-EAX (§16), CONCAT (§13, ERROR), float-smuggling (§6),
addr-value-add (§17), param-loop-corruption (§4), discarded-result (§8/§11),
vendored-source (§36, advisory: the TU is a public library — transcribe upstream
instead of reshaping Ghidra output), effect-marker-buf (§48, ERROR). Use
`--changed-only` to scan the union of staged, unstaged-tracked, and untracked
files you have touched; use `--staged-only` (what the pre-commit hook uses) for
staged files only. **WARN-level findings in files you touched are review items,
not ignorable noise** — an fmod/FPREM1 warning in `hud_messaging.c` was ignored
as pre-existing noise and shipped a visible HUD rendering bug (2026-06-10).

### Golden Master test harness — usage
A specialized test harness intercepts the engine boot in
`src/halo/shell_xbox.c`. It lets you run functions inside the engine context and
verify their side-effects/return values against the exact Xbox ASM output.
- *Usage:* Add tests to `src/halo/test_harness.c`. Ensure your function is
  unmapped in `kb.json` (`"ported": false`), run
  `rtk python3 tools/verify/run_golden_tests.py` to capture the original FPU hex
  values. Then map your function (`"ported": true`) and press Enter to verify
  your C implementation.
- *Use cases:* FPU math functions, struct/object initializers, and complex
  isolated state transitions.

### Dual-oracle runtime harness — procedure (moved from CLAUDE.md, 2026-09-02)
For high-value stateful targets, prefer a same-process harness case over two
separate emulator runs. Clone inputs, call the original implementation, restore
inputs, call the candidate implementation, then compare return values, mutated
buffers, selected globals, and structured debug records in one initialized
engine state.
