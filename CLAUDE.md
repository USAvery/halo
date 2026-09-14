# Agent Instructions

These rules apply to any coding agent used in this repo (Claude, OpenCode, subagents). Keep `AGENTS.md` and `CLAUDE.md` aligned.

## Mission

Recover Halo CE Xbox behavior faithfully and incrementally.

- **Binary is source of truth:** Target is **Halo Xbox debug build 2276** (`halo-patched/cachebeta.xbe`, MD5 `c7869590a1c64ad034e49a5ee0c02465`, version `01.10.12.2276`, Oct 12, 2001). Addresses in `kb.json` are absolute VAs into THIS debug binary (richer symbols/asserts; do not swap in a retail `default.xbe`).
- **Functional Re-implementation:** We build C implementations and patch redirects into the original binary. Judge matches by **identical logic/operations**, not identical bytes. Import indirection (`[__imp__global]`) and equivalent codegen (store coalescing, `cmp $0` vs `mov`/`test`, `add esp,4` vs `pop`) are expected and acceptable.
- **Explicit Unknowns:** Prefer an explicit unknown over a plausible guess. Canonical struct-field names, because the distinction is load-bearing: **`field_<hex>`** = offset is accessed, meaning unproven; **`pad_<hex>[n]`** = never observed accessed (so a `pad_` field turning out to be read is a recovery bug). Lowercase hex, no `0x`, 2 digits min (`field_02`, `pad_08[4]`). `unk_<hex>`/`unk_N[]` is the legacy form — tolerated in existing structs, converted opportunistically when the struct is already being edited, never as a standalone rename campaign. Full rules: skill `naming-confidence`.
- **Confirm Struct Offsets:** Always check field offsets in `src/types.h`. NEVER guess a struct field — a wrong field offset is a wrong decompilation. Refine structs (e.g., `game_globals_t`, `players_globals_t`) to replace raw-offset casts (`[ptr+0x24]`) with named struct fields by splitting `unk_N[]` arrays; new splits land as `field_<hex>`/`pad_<hex>[n]`.
- **CMakeLists.txt Registration:** Every new `.c` file MUST be listed in `src/CMakeLists.txt`. Unregistered files silently fail to compile and patch.
- **Small Changes:** Make small, reviewable commits.
- **Preserve Shape:** Maintain original ABI, layout, side effects, and control-flow.

## Token Discipline

### Read-Once Rule
- **Never read the same file twice in one task.** Before reading, ask: "Have I already read this file in this conversation?"
- **The Edit tool confirms success.** Do not re-read to verify your edit worked.
- **Failure exception only:** If an edit fails, re-read *only* the failing line range (≤20 lines), never the full file.

### Wording and language
- Be brief and concise. Don't use words as "This is the smoking gun", "Honestly", "I'll be honest".
- Use natural normal concise language.

### Research Ratio Gate (4:1 minimum)
Target 4+ reads per edit. Before editing any function, perform at least these research steps:
  1. `rtk rg '<function_name>' src/` — find all callers and related symbols.
  2. `rtk read <file> -o <start> -l <limit>` — read the exact target lines only.
  3. `rtk jq '<filter>' kb.json` — verify signatures and ABI.
  4. `rtk git diff -- src/ kb.json` — check current uncommitted state.
If you have not done 4 reads, do more research before editing.

### File-Specific Bans & Caps
| File / Directory                                                                             | Action           | Max Lines | Why                                                      |
|----------------------------------------------------------------------------------------------|------------------|-----------|----------------------------------------------------------|
| `kb.json`                                                                                    | `rtk jq` ONLY    | **0**     | 6,000+ lines                                             |
| `objects.c`, `units.c`, `sound_manager.c`                                                    | `rtk read -o -l` | **100**   | Large; range-read only                                   |
| `build/`, `build_debug/`, `node_modules/`, `.git/`, `halo-patched/`, `__pycache__/`, `dist/` | **NEVER**        | **0**     | Generated artifacts                                      |
| Any `*.log`                                                                                  | **NEVER**        | **0**     | Run the command instead of reading output                |

### Session Memory
Keep a mental ledger of files already read. Need a fact from one? Recall it or `rtk rg` for the string — do not re-read.

The hook `tools/audit/token_discipline_hook.py` (wired in `.claude/settings.json`) warns on duplicate reads, banned-dir reads, and a read/edit ratio below 4:1. Treat its warnings as authoritative.

## Workflow

### 1. Research & Analysis
- **Scope-first:** Start tasks with exact path(s), symbol(s), and line range(s).
- **Prior-fix lookup:** Before debugging any regression, crash, hang, assert, visual bug, wrong behavior, or build/deploy failure, run `rtk python3 tools/memory/prior_fixes.py "<symptom or target>"`. Matches are hypotheses only; confirm against binary/runtime evidence first.
- **Token Discipline:** See above. Use line-ranged reads; never re-read after a successful edit. Files >300 lines: always `rtk read -o <start> -l <limit>`, cap 100 lines per read.
- **kb.json:** `rtk jq` ONLY — never use the Read tool. Never use `python -c` for JSON.
- **Pre-edit research:** Before editing any function, run `rtk rg '<function_name>' src/` to find all callers and related symbols.
- **Ghidra Pre-flight:** Before using any `ghidra` or `ghidra-live` MCP tool, run `python3 tools/audit/check_ghidra_mcp.py`. If it fails, alert the user and stop.
- **Ghidra Token Discipline:** Never dump full decompile/disassembly "just in case." `get_function_callees` first; never re-decompile a function you already have ported C for; `read_memory` on a specific range, not `disassemble_function`; smallest target first. **After a Ghidra call that informs N edits to one file, record the line ranges from the first read and use `rtk read -o <line> -l 40` thereafter — never re-read from offset 0.**
- **Tooling:** Always prefix with `rtk`. Use `rtk fd` (files), `rtk rg` (text), `rtk ast-grep` (structure), `rtk fzf` (selecting). If `rtk rg` returns empty or errors with flags, fall back to bare `grep -rn` immediately — don't retry with different flag permutations.

### 2. Implementation & kb.json Discipline
- **C89 only.** All lifted code must be valid C89: declare all variables at the top of their block scope, before any statements. No mixed declarations (C99). Enforced by VC71 verify. We stay C89 until the game is fully reimplemented.
- **No Speculation:** Do not invent behavior or names without binary evidence.
- **No Fake Matching:** A byte-perfect or high-VC71 result never justifies disallowed constructs — `volatile` shaping, redundant/dead stores, arbitrary barriers or pragmas, raw-offset access where a proven struct field exists, representation tricks, undefined behavior, or invented source. Logic that matches but is nonsensical is a bug: investigate or park it, never ship it.
- **Authentic Types & Idioms:** Use engine types (`real`, `boolean`, fixed-width ints from `src/types.h`), not `float`/`bool`/stdlib substitutes; `cseries` flag/math macros and typed tag/object accessors, not hand-rolled bitwise logic or casts after raw `tag_get`/`object_get`; named enum constants in `switch` cases, not bare numbers. Declarations live in their genuine owning header (or kb.json's `decl.h`), never an unrelated `.c`. Details: `halo-xbox-re`.
- **Preserve Inline Schedule:** Do not force, duplicate, suppress, or hand-copy inlining to chase bytes; match the original's inline vs. out-of-line placement. An unintended extra COMDAT (out-of-line copy of an inlined helper) is a fidelity bug.
- **Zero-Regression & Scoring Honesty:** Never trade an existing exact/ported match for a new one. Count only strict matches as exact; report meaningful-exact, padded-exact, and fuzzy bytes separately. A coherent-but-unmatched lift gets zero exact credit and is parked with a reason (`source-recovery`).
- **ABI Stability:** `@<reg>` annotations in `kb.json` are **immutable**. Never remove or change register assignments.
- **New Symbols:** Register-arg callees must be added to `kb.json` with `@<reg>` and called by name.
- **No Inline ASM:** The build system handles thunks via `kb.json`. Do not use inline assembly in C.
- **`ported` is a real toggle.** Setting `"ported": false` on a function in `kb.json` deactivates the patch at build time: `tools/build/patch.py` skips the redirect at the original address AND writes a JMP at the impl entry that tail-calls the original. Both original code and our lifted C code reach original behavior. The C implementation stays in source and in the binary as dead code. Use this for **bisecting** which lift introduced a regression — flip ports off one at a time and rebuild. Re-activate by setting `"ported": true`. `pre-commit-ported-deactivations.sh` **BLOCKS** commits introducing deactivations not in `tools/audit/deactivation_allowlist.json` (bypass: `HALO_ALLOW_DEACTIVATIONS=1 git commit`, or `--no-verify`); CI gates via the `DeactivationGate` job in `main.yml`. New allowlist entries need a `"reason"` field plus `--update-allowlist`. `tools/audit/check_ported_deactivations.py --check` exits non-zero only on non-allowlisted deactivations.
- **Separation:** Keep logic changes separate from cleanup/formatting.
- **Auto-lift delegates to `/lift`:** `tools/llm_auto_lift.py` does target selection, liftability scoring, and Ghidra context caching; code generation belongs to `/lift`. Legacy `review`/`promote` subcommands exist for old batch artifacts.
- **Never transcribe MSVC intrinsics as C function calls.** Ghidra shows them as regular calls but they have non-standard ABIs that corrupt the stack or registers when called from C. Use the equivalent C idiom — the compiler generates the intrinsic automatically:

  | Address  | Intrinsic      | Refs | Ghidra shows          | Write in C instead                                                                                     |
  |----------|----------------|------|-----------------------|--------------------------------------------------------------------------------------------------------|
  | 0x1d90e0 | `_chkstk`      | 71   | `regparm(1)` call     | declare locals normally (or `static` for large buffers)                                                |
  | 0x1d9068 | `_ftol2`       | 228  | `_ftol2(var)` or cast | `(int)float_expr`                                                                                      |
  | 0x1dd5c8 | `__SEH_prolog` | 74   | mangled params        | `__try/__except` — clang supports this natively on `-target i386-pc-win32`; see `docs/seh-handling.md` |
  | 0x1dd601 | `__SEH_epilog` | 73   | mangled return        | (paired with prolog — handled automatically by `__try/__except`)                                       |
  | 0x1dd620 | `_allmul`      | 10   | 4-arg call            | `(int64_t)a * b`                                                                                       |
  | 0x1dd660 | `_aullshr`     | 1    | register call         | `(uint64_t)val >> shift`                                                                               |
  | 0x1dd680 | `_aullrem`     | 1    | 4-arg call            | `(uint64_t)a % b`                                                                                      |
  | 0x1dd770 | `_aulldiv`     | 1    | 4-arg call            | `(uint64_t)a / b`                                                                                      |

  Never add these to kb.json. They are compiler runtime, not game functions. SEH-wrapper handling (`__try/__except`, the ~55% VC71 cap, `src/halo/cseries/xbox_crt.c`, `XAPILIB:xbox_crt.obj`): skill `lift-decompiler-traps` §5.

- **MSVC-style `__asm` is broken under clang.** `-target i386-pc-win32` makes clang define `_MSC_VER`, so `#ifdef _MSC_VER` guards select MSVC-style `__asm {}` blocks, which do **not** communicate register clobbers to the optimizer — a live GPR overwritten inside the block is reused stale afterwards (silent corruption or ACCESS_VIOLATION). **Rule:** guard MSVC-only asm with `#if defined(_MSC_VER) && !defined(__clang__)` and give a GCC-style `asm volatile` alternative in the `#else` branch with proper output constraints and clobber lists. **Dangerous:** `RDTSC` (EAX:EDX), `CPUID` (EAX/EBX/ECX/EDX), `MUL`/`DIV` (implicit EAX/EDX). FPU-only asm (`FLD`, `FMUL`, `FPATAN`) is safe — it doesn't touch GPRs.

- **Audit XCALL return and param types against kb.json.** A raw function-pointer cast that doesn't match the real signature silently uses the wrong register or push convention: `float` return reads ST(0), `int`/`uint32_t` return reads EAX; `float` params go via FLD+FSTP[ESP], `int` params via PUSH (so `(int)float_var` truncates). **Run `rtk python3 tools/audit/check_xcall_types.py` after adding or modifying any XCALL macro.** ERROR-level (float↔int) mismatches are blockers.

- **Verify callee buffer sizes.** Ghidra may under-size local buffers; check the callee's `memset`/init size in disassembly for the true required size. Detail: `lift-decompiler-traps` §5.

- **MSVC stack layout overlap hazard.** When a lifted function calls an **unlifted** function by pointer (vtable, callback, function table), the callee may read offsets within a local array that MSVC placed overlapping other locals; our clang layout puts garbage there. Detection and fix: `lift-decompiler-traps` §5.

- **Verify every call site against disassembly.** The decompiler is a draft. Five known pitfalls (worked examples in `lift-decompiler-traps`):
  1. **Register aliasing:** Ghidra substitutes the wrong variable for EBX/ESI/EDI in long functions — trace each PUSH backward from the CALL.
  2. **Push-then-fstp:** MSVC passes floats via `PUSH <dummy>; FSTP [ESP]`; Ghidra reports the dummy (often a pointer) as the argument.
  3. **Struct field rotation:** MSVC reorders stores; derive offsets from `MOV [EBP±N]`, not the decompiler.
  4. **Cross-product operand swap:** `cross(A,B)` vs `cross(B,A)` look nearly identical — verify subtraction order against disassembly.
  5. **Buffer-alias confusion:** Ghidra names every stack offset an independent `local_XX` even inside a local buffer. After any call taking a buffer pointer, compute `EBP_offset - buffer_base_EBP_offset`; within `[0, buffer_size)` means it is a buffer field.

### 3. Build & Verification
- **Toolchain & Configure:** `clang + lld (lld-link) + cmake + python3`; `cmake -B build -S . -DCMAKE_TOOLCHAIN_FILE=toolchains/llvm.cmake` then `cmake --build build`. Pinned deps and Windows/Linux bootstrap: skill `tool-reference`.
- **Lift Pipeline:** `rtk python3 tools/lift_pipeline.py --target <name_or_addr> --no-metadata-update --verify-policy auto` is the primary post-lift orchestrator (build, ABI audit, VC71 verify, optional behavior/runtime checks, low-match policy gates).
- **xemu Configuration:** System Memory MUST be set to **128 MiB** in xemu (required for debug build 2276!). Standalone-ISO recipe: skill `debug-xemu`.
- **Hazard Scan:** Run `rtk python3 tools/audit/check_lift_hazards.py` after source edits or when reviewing auto-lift output (`--changed-only` for files you touched; `--staged-only` is the pre-commit hook's mode). Intrinsic calls, undersized buffers, duplicate arguments, pointer-as-float, and CONCAT survival are blockers until investigated. **WARN-level findings in files you touched are review items, not ignorable noise.** Check-to-learnings mapping: `halo-verify-debug`.
- **Learnings-must-ship-a-detector rule:** Every new `docs/lift-learnings.md` section **must ship, in the same commit**, either (a) a check wired into `check_lift_hazards.py` / `draft_decompiler.py` / `buffer_alias_detector.py` / the call-site audit, or (b) a one-line `Automation: not mechanically detectable because …` line. A documented grep counts as (a) only when actually implemented as a check.
- **XCALL Type Audit:** `rtk python3 tools/audit/check_xcall_types.py` after adding or modifying XCALL macros (see §2 rule).
- **Golden Master Test Harness:** Runs functions inside the engine context (`src/halo/shell_xbox.c`) against Xbox ASM output; tests in `src/halo/test_harness.c`, driver `tools/verify/run_golden_tests.py`. Usage: `halo-verify-debug`.
- **Live Memory Capture + State Replay:** Capture live game state and replay into `unicorn_diff.py --state-snapshot <path>` (or `--from-halorec`). **Use the proven virtual-memory paths (`memsave_snapshot.py`, `qmp_capture.py`) — never physical `pmemsave`**, and never QEMU `savevm`/`loadvm` for oracle tests. **VERIFY EVERY CAPTURE** against a known global first. Flows, verification gate, xemu-MCP bypass: `debug-xemu`.
- **Dual-Oracle Runtime Harness:** For high-value stateful targets prefer a same-process harness case over two emulator runs — original and candidate called on cloned inputs in one initialized engine state. Procedure: `halo-verify-debug`.
- **RTK Build:** Use `rtk python3 tools/build/build.py -q --target halo` (warnings/errors only).
- **Build error triage — undeclared/renamed symbols:** When the compiler reports `call to undeclared function 'FUN_XXXXXXXX'` or `wrong argument count`, do NOT read source files first. Use two shell commands:
  ```bash
  rtk rg "FUN_XXXXXXXX" build/generated/decl.h   # → not there = renamed
  rtk jq '[.. | objects | select(.addr? == "0xXXXXX")] | .[0] | {name, decl}' kb.json  # → real name + signature
  ```
  The function was renamed in kb.json; update the call site. Read source only if the signature itself is wrong.
- **Build error triage — `ported=true` symbol absent from EXE exports:** When `patched_xbe` fails with "symbol absent from EXE exports", the **first** step is always `grep -rn "FUN_XXXXXXXX" src/`.
  - **Implementation found** → the source file has compile errors or is missing from `CMakeLists.txt`. Fix the errors; do NOT set `ported=false`.
  - **No implementation found** → the function was marked ported without an implementation. Set `ported=false` until a proper lift is done.
- **VC71 Verify:** After lifting FPU-heavy functions (geometry, math, projections), run `rtk python3 tools/verify/vc71_verify.py src/path/to/file.c` and review `[FPU-WARN]`, `[LOADW-WARN]`, `[IMM-WARN]`, `[SHAPE-WARN]` output. **A call-count delta on a function with fewer than ~12 reference instructions is a dropped call, not a codegen artifact** — decode the reference's E8/E9 targets first. References derive from the pristine XBE + `tools/verify/function_bounds.json`; no delinked export is needed for scoring. Warning semantics and flags: `halo-verify-debug`.
- **A gate regression with no matching source change is a harness bug until it reproduces.** Any parallel fan-out over `vc71_verify` MUST pin `build/generated/decl.h` once in the parent and pass `--skip-decl-regen` to workers. Run the gate twice and diff the `✗` lists; `VC71_NO_MEASURE_MEMO=1` separates a wrong measurement from a stale memo. See lift-learnings 52.
- **A verify run that scores ZERO functions in a TU is a compile failure until proven otherwise.** Read the first `cl.exe` diagnostic before investigating the reference side; the classic cause is a stale `decl.h` (§41 in `docs/lift-learnings.md`).
- **Score-Improvement Gate:** Before changing an existing lift to raise its VC71 score, record `score_improve.py baseline` for the whole file. Apply one binary-backed hypothesis, then `score_improve.py check` the target. Keep it only if it gains ≥0.01pp without regressing another scored function, dropping a score, or adding warnings; otherwise revert that candidate change before the next hypothesis. See `lift-score-improve`.
- **Bounds-table precondition (REQUIRED before VC71 verify):** confirm the target's address has an entry via `rtk jq '."0x<addr>"' tools/verify/function_bounds.json`; if missing, regenerate with `rtk python3 tools/verify/function_bounds.py` and commit the table. Delinked COFF exports are still required for the equivalence lane and objdiff — not for VC71 scoring.
- **Permuter (`/verify permute`):** Last-mile match optimizer, ONLY when VC71 match is in **[85, 98]%**. Below 85% the lift has a structural bug — fix it first; above 98% it is not worth the cycles. Never accept a permutation that lowers the match; always re-run the lift pipeline against the new source. Details: `halo-verify-debug`.
- **Equivalence (`/verify equivalence`):** Unicorn behavioral differential (`tools/equivalence/unicorn_diff.py`), for when byte-match is weak evidence: FPU-heavy code, hashes/serializers, structurally capped lifts. **Decision:** ≥99% → done; [85, 98]% with delinked ref → permute first; [85, 98]% pure-leaf FPU-heavy → equivalence first; <85% → investigate the lift (don't permute); structurally capped (~55%) → equivalence to prove behavior. Flags, coverage/confidence tiers, mem-trace, state snapshots: `halo-verify-debug`.
- **Auto-Lift Selector:** `rtk python3 tools/llm_auto_lift.py select` for target selection, `cache-context` for Ghidra context caching; code generation is delegated to `/lift`.
- **Auto-Build After Lift:** `/lift` and `/auto-lift` automatically trigger `/build` on completion (enforced by `.claude/settings.json` hooks).
- **Validation:** Run the narrowest meaningful validation first.
- **XBDM Priority:** Prefer real Xbox XBDM verification over xemu when available.
- **Failure Policy:** If an edit fails, re-read only affected ranges before retrying.

### 4. Commit Discipline
- **Use `/lift` for all new function ports.** Never manually implement and commit lift work outside it — it runs the ABI audit, build, and verification stages that catch calling-convention and register-arg bugs.
- **No Freeform Messages:** Never write freeform lift commit messages.
- **Auto-Lift Commit Policy:** `/auto-lift` auto-commits on pipeline success (via `generate_lift_commit.py`) and reverts+logs on failure (to `artifacts/auto_lift/failures/`). Legacy `review`/`promote` artifacts must not be committed directly.
- **Standard Command:** After staging changes, run:
  ```bash
  MSG=$(mktemp /tmp/halo-commit-msg.XXXXXX)
  rtk python3 tools/audit/generate_lift_commit.py --batch-name "<short description>" > "$MSG"
  rtk git commit -F "$MSG" && rm -f "$MSG"
  ```
  **Never use a fixed path such as `/tmp/commit_msg.txt`** — it is shared by every concurrent agent and worktree on the box, and a second actor overwriting it silently commits YOUR staged changes under THEIR message. Always `mktemp`. Background: skill `halo-lift`.
- **ABI audit gate:** `generate_lift_commit.py` runs `audit_reg_abi.py` on all newly ported functions. If any fail, no commit message is generated. Use `--skip-abi-audit` only for emergencies.
- **Pre-commit hook:** The git pre-commit hook runs both the baseline guard and lift ABI audit on staged changes. `--no-verify` is the emergency bypass.

## Repo Guardrails
- **Noisy Dirs:** Never read or search inside `build/`, `build_debug/`, `node_modules/`, `.git/`, `halo-patched/`, `__pycache__/`, `dist/` unless explicitly asked. These are generated artifacts.
- **No Log Files:** Never read `build.log`, `build_output.log`, or any `.log` file. Run the build or command instead.
- **Scoped Diffs:** When checking changes, scope git diffs to source: `rtk git diff -- src/ kb.json CMakeLists.txt`. Bare `git diff` includes tracked build artifacts.
- **RTK Always:** Prefix ALL shell commands with `rtk` (e.g., `rtk git status`, `rtk pytest`).
- **Output Schema:** For non-trivial work, report: Target, Confirmed, Inferred, Uncertain, Proposed Code, kb.json updates.

## Command Decision Tree & Analysis Tools

Moved to skill `tool-reference` (`.claude/skills/tool-reference/SKILL.md`) — the full
which-command/which-script reference for RE/lift/verify/equivalence tasks now loads
on demand instead of every session.

## Architecture and Skills

**Skills are agent doctrine the assistant self-invokes — not a menu the user
picks from.** `tools/memory/skill_router_hook.py` surfaces the relevant skill(s)
from each `SKILL.md`'s `triggers` every prompt; treat those `[skill-router]`
picks — and trigger words (`crash`, `page fault`, `@<reg>`, `ADD ESP`, `_chkstk`,
`VC71`, `low match`, `permuter`, `wrong color`, `trajectory`, `xemu`) — as an
instruction to load and apply the named skill before acting. Before any lift,
score-recovery, call-site, hazard, or crash/regression work, apply the matching
skill via the Skill tool when surfaced, else read `.claude/skills/<skill>/SKILL.md`
and follow its checklist (`lift-*` are often unsurfaced — not a reason to skip).
Name the skill(s) in any subagent brief.

Full catalogue: `.claude/skills/SKILLS.md`, generated from frontmatter by
`tools/memory/gen_skills_index.py` (on-demand). To add or retier a skill: edit its
`SKILL.md` frontmatter (`tier:` + `triggers:`), then re-run
`migrate_skill_frontmatter.py` + `gen_skills_index.py`.

<!-- rtk-instructions v2 -->
# RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. Even in command chains with `&&`:
`rtk git add . && rtk git commit -m "msg" && rtk git push`

## RTK Commands by Workflow

| Category   | Typical Savings | Examples                                        |
|------------|-----------------|-------------------------------------------------|
| **Build**  | 70-90%          | `rtk cmake`, `rtk tsc`, `rtk lint`              |
| **Test**   | 90-99%          | `rtk pytest`, `rtk cargo test`, `rtk vitest`    |
| **Git**    | 60-80%          | `rtk git status`, `rtk git diff`, `rtk git log` |
| **Files**  | 60-75%          | `rtk read`, `rtk grep`, `rtk find`, `rtk ls`    |
| **Ghidra** | 70-90%          | `rtk python3 tools/audit/check_ghidra_mcp.py`   |

Use `rtk gain` to view savings statistics.
<!-- /rtk-instructions -->
