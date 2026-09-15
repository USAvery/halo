# Handover

## Objective
Audit recent VC71 score-improvement commits, remove fake-match constructs, and recover binary-correct behavior without treating byte score as correctness proof.

## Current State
- Work is partial. Six corrective source files are staged but uncommitted.
- Commit was attempted twice and stopped by repository gates; hooks were not bypassed.
- Unrelated unstaged changes remain in `README.md`, `tools/raw_cast_baseline.txt`, and `tools/verify/vc71_scores.json`.

## Confirmed
- `FUN_000a54b0` reference tail-jumps to `weather_particle_system_render` at `0xa557f`; missing call restored.
- `FUN_000cc0a0` high-bit HS globals index directly; low-bit scenario globals add `0x27d504`. Branch fixed from disassembly at `0xcc0a8`.
- `load_symbol_table` failed-open path skips `_fclose`; current fix returns before cleanup, matching `0x9277b`.
- `FUN_000d2300` uses float store/reload plus `FISTP`; `x87_round_to_int` produces 100% VC71 mnemonic and operand match.
- Typed input accesses replaced cross-array `smoothed[-8]`, `digital[INPUT_KEY_COUNT]`, and handle-relative rumble reads.

## Important Changes
- `src/halo/game/cheats.c`: restore weather rendering side effect.
- `src/halo/hs/hs_runtime.c`: correct HS global-reference partition.
- `src/halo/cseries/stack_walk_windows.c`: avoid `fclose(NULL)` and remove dead GNU-attributed parser helpers that prevented VC71 compilation.
- `src/halo/interface/hud.c`: replace parameter-slot aliasing and truncating cast with `x87_round_to_int`.
- `src/halo/math/random_math.c`: replace float writes through a two-byte parameter with a real `sample` local.
- `src/halo/input/input_xbox.c`: restore `input_gamepad_state` and `input_rumble_state` field accesses; make unused annotation portable.

## Validation
- `rtk python3 tools/build/build.py -q --target halo`: passed.
- VC71: `FUN_000a54b0` 80.9% / operand 76.6%; `FUN_000cc0a0` 87.8% / 82.9%; `load_symbol_table` 76.7% / 58.6%; `FUN_000d2300` 100% / 100%; `FUN_0010aa60` 81.2% / 71.6%; `input_get_device_states` 64.5% / 46.2%; `input_flush_rumble` 82.0% / 72.1%; `input_update_keyboard_devices` 69.5% / 55.7%.
- HUD Unicorn smoke: 99 passed, 0 failed, 1 unmapped-pointer error; result marked inconclusive because outputs were vacuous.
- Commit gate: 76 Unicorn regression targets passed, 0 failed, 1 infrastructure error. Missing kb target `data_next_index[valid-pool]` blocked commit.
- Initial commit-message generation also found 28 pre-existing `kb_reg_baseline` drifts; second attempt used documented `--skip-abi-audit`, but normal pre-commit hooks remained enabled.

## Uncertain / Risks
- Automated reviewer rejected current patch because `random_math.c` still contains `*(unsigned int *)&sample = 0x3f800000u`; replace with `sample = 1.0f` before acceptance.
- `FUN_0010aa60` retains FPU warnings. Input device and keyboard functions retain load-width warnings.
- Stateful input, weather, HS, and map-parser paths lack targeted runtime or dual-oracle coverage.
- Weather correction lowers mnemonic score by 0.6 points because reference uses a tail jump while candidate records a call; required side effect is binary-confirmed.
- Broader audit queue remains: weapon trigger fields/enums, `input_globals_t`, `game_variant_t`, `player_data_t`, game-engine vtable, stack symbol structs, network client, and D3D resource types.

## Next Steps
1. Replace the remaining `sample` type-pun with `1.0f`, then rerun `FUN_0010aa60` VC71 verification.
2. Repair or update the stale `data_next_index[valid-pool]` Unicorn regression target, rerun normal commit hooks, and commit the six staged files.
3. Investigate target FPU/load-width warnings against disassembly before further score shaping.
4. Start evidence-table-backed weapon trigger and input-global struct recovery.

## Resume Prompt
Continue `docs/handover-byte-accuracy-audit-2026-09-15.md`. Preserve staged corrective changes, do not touch unrelated unstaged files, remove the final `random_math.c` aliasing cast, resolve the pre-commit regression-target infrastructure error, rerun focused VC71/build gates, then commit without bypassing hooks.
