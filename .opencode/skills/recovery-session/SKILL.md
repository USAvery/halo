---
name: recovery-session
description: End-to-end recovery session — recover enums, structs, fields, and readable source per object, improve that object's VC71 byte accuracy, run safety gates, and auto-land each small batch to main.
---

# Recovery Session

Run the full post-lift recovery loop without mixing concerns inside commits:

```text
source/type recovery -> byte-accuracy campaign -> safety gates -> auto-land -> repeat
```

`/recover-goal` owns evidence-backed names, constants/enums, struct definitions,
offset-to-field conversion, and optional risky cleanup. `score-campaign` owns
byte-shape changes. `auto_reintegrate.py` owns branch-to-main integration. This
skill only sequences those existing owners and stops on ambiguous state.

## Ownership And Non-Overlap

- `recover-goal`: selects objects and owns every source/type recovery category.
- `score-campaign`: selects score targets inside the recovered object and owns byte-shape edits.
- `auto-session` and `campaign`: lift previously unported functions; this skill never lifts.
- `auto_reintegrate.py`: owns integration gates and main advancement.

Do not duplicate their selectors, category workers, score levers, commit rules,
or merge implementation here. Invoke each owner unchanged and consume its
reported status.

## Arguments

All arguments are optional:

- Bare `N` or `--goal N`: objects recovered, scored, gated, and landed; default `1`.
- `--object <name>.obj`: explicit first object; later iterations use recovery frontier.
- `--min-funcs N`: recovery-frontier minimum; default `10`.
- `--min-score X`: recovery-frontier score floor.
- `--allow-risky`: permit `expr-simplify` and `control-flow` recovery. Their behavioral gates remain mandatory.
- `--score-goal N`: score improvements attempted per recovered object; default `8`.
- `--score-max-runs N`: score-campaign run cap per object; default `2`.
- `--score-min-score X`: byte-accuracy frontier floor; default `50`.
- `--attempt-ceilings`: pass through to `score-campaign`.
- `--no-score`: perform source/type recovery and landing without byte-score work.
- `--no-land`: keep gated commits on current branch. Do not auto-advance `main`.
- `--dry-run`: inspect one object's recovery and score frontiers; make no commits and never land.

## Preconditions

1. Run only from an isolated session branch, never `main`.
2. Record current branch and main worktree with `rtk git branch --show-current` and `rtk git worktree list`.
3. Fail closed on authored dirty work under `src/`, `kb.json`, `tools/`, `.claude/`, `.opencode/`, or `recovery/`. Tolerate only documented generated noise.
4. Never clean, stash, reset, or commit another actor's work to satisfy a guard.
5. Run objects sequentially. Never overlap recovery-category or score workers touching the same tree.

## Object Loop

For each object until the goal is reached:

### 1. Recover Source And Datatypes

Run `/recover-goal --goal 1`, forwarding `--object` on the first iteration and
forwarding `--min-funcs`, `--min-score`, `--allow-risky`, and `--dry-run` when
present. Follow `recover-goal` unchanged: sequential categories, evidence
artifacts, purity-gated commits, VC71 floors, and explicit parks.

Require exactly one newly finished object in its final report and
`recovery/goal_ledger.json`. If none or more than one is identifiable, stop as
`recovery_result_ambiguous`. Any `guard_failed`, `finalize_failed`,
`infra_blocked`, `systemic_*`, or exhausted queue stops this session.

`recovery_goal.py finish` updates the tracked goal ledger after category/report
commits. If that is the only remaining authored change, stage only
`recovery/goal_ledger.json` and commit it as
`recover(<object-stem>): update goal ledger`. Any other authored dirt is a stop,
not something this session may absorb.

### 2. Improve Byte Accuracy

Unless `--no-score`, run `score-campaign` restricted to the newly recovered
object:

```text
--object <finished-object> --goal <score-goal> --max-runs <score-max-runs>
--min-score <score-min-score>
```

Forward `--attempt-ceilings` and `--dry-run` when present. Zero eligible targets
or zero improvements is not a recovery failure; record it and continue. A score
regression, failed neighbor gate, unexplained measurement failure, or authored
dirty tree stops the session. Never fold score edits into recovery-category
commits and never squash the two lanes together.

### 3. Run Batch Gates

Unless dry-run, run these after both lanes finish:

```bash
rtk python3 tools/audit/check_lift_hazards.py --changed-only
rtk python3 tools/audit/check_param_types.py --check
rtk python3 tools/audit/extract_reg_args.py --check
rtk python3 tools/build/build.py -q --target halo
```

Any ERROR, new warning requiring review, ABI/type drift, missing function, or
build failure stops before landing. Do not weaken baselines or bypass hooks.
Require a clean branch worktree after committed generated outputs are handled by
their owning workflow.

### 4. Land Small Batch

Unless `--no-land` or `--dry-run`:

```bash
rtk python3 tools/integrate/auto_reintegrate.py --branch <session-branch> --json
```

- `landed`: count object complete and continue.
- `parked` or `inconclusive`: stop immediately. Report reason verbatim and use `reintegrate-to-main` for human resolution.

Never hand-merge `kb.json`, never advance `main` manually, and never continue
building a larger branch above an unlanded object.

With `--no-land`, count an object as completed-on-branch and continue only when
the user explicitly requested accumulated commits. Report that none reached
`main`.

## Dry Run

Dry-run examines one object only because recovery ledger state intentionally
does not advance. Run recovery planning first, then score discovery against the
current source. State that score results describe pre-recovery source and are a
planning estimate. Skip batch gates and integration.

## Stop Conditions

- Requested object goal reached.
- Recovery queue exhausted.
- Recovery or score workflow parks/fails.
- Safety gate fails.
- Integration returns `parked` or `inconclusive`.
- Dirty authored state cannot be attributed to the active workflow.
- Dry-run inspection completes.

## Final Report

Report objects attempted, recovered, scored, gated, landed, and left only on the
branch. For each object include recovery categories applied/parked, score gains,
commit SHAs, gate results, integration status, and exact stop reason. A park is a
finding, not routine progress.

## Usage

```bash
/recovery-session
/recovery-session 3
/recovery-session --object hud_weapon.obj --score-goal 6
/recovery-session 2 --allow-risky --attempt-ceilings
/recovery-session --object actors.obj --dry-run
/recovery-session 2 --no-land
```
