# RNG draw trace

A runtime trace of every draw from the **global** random seed (`0x46e3f4`), used
to find which lifted function desyncs a system-link game against a pristine
build-2276 console ("client/server random seed mismatch" right after a kill).
Static per-function draw-count parity already checks out, so the divergence is
branch/state dependent and only shows up at runtime.

The instrumentation is compiled **only** when `HALO_RNG_TRACE` is defined. With
the flag off the emitted objects for `random_math.c`, `game.c` and
`network_game_globals.c` are byte-identical to the uninstrumented build (the
inserted lines are followed by `#line` directives that restore the original
physical numbering, because `assert_halt()` stamps `__LINE__` into the binary).
Every inserted block in `random_math.c` carries its own restore, so each
physical line below the preamble maps to exactly its HEAD line number -- an
`assert_halt` added anywhere in the file later keeps the right `__LINE__` in
both flag states.

## What is recorded

`src/halo/math/rng_trace.h` defines a 16-byte record:

| field         | meaning                                                          |
|---------------|------------------------------------------------------------------|
| `tick`        | `(kind << 24) | (game_time_globals->time & 0xffffff)`            |
| `seed_before` | global seed before the draw (setters: the value being installed) |
| `caller`      | `__builtin_return_address(0)` of the RNG primitive               |
| `caller2`     | reserved, always 0 (the EBP-chain hop was dropped as unsafe)     |

Records go into `halo_rng_trace`, a 65536-entry ring in the appended PE's
`.data` section, in its zero-initialised tail (`vsize 0x117040` against
`rawsize 0xe00`): 1 MiB of virtual space, zero bytes of XBE file size.  There
is no separate `.bss` section in the image -- the buffer lives past the end of
`.data`'s raw data and is zero-filled by the loader.

Hooked events (`kind`): `random_math_real`, `random_real_range`,
`random_seed_step`, `random_range`, `random_seed_get_direction3d`,
`seed_random_orientation` (3 LCG steps), `random_direction3d`,
`set_random_seed`, `network_game_set_random_seed`, plus the two direct seed
stores in `game_initialize_for_new_map` and `periodic_functions_initialize`
(kinds 9 and 10, treated as reseeds). Draws against the **local**
seed (`0x46e3f8`) are ignored -- they do not affect network determinism.

## Finding the buffer

`tools/xbox/rng_trace_dump.py` derives the runtime VA exactly the way
`tools/xbox/symbolize_exception.py` does:

    runtime_base = round_up(max(section.virtual_addr + virtual_size
                                for section in halo-patched/cachebeta.xbe), 0x1000)
    buffer_va    = runtime_base + <RVA of the `halo_rng_trace` PE export in build/halo>

`patch.py` maps the appended `.data` at `runtime_base + VirtualAddress`, so the
export RVA is the whole story. `halo_rng_trace` is listed in
`DIAGNOSTIC_DATA_EXPORTS` in `tools/build/patch.py` so the export-to-kb.json
matcher does not treat it as an unresolved re-implementation.

## Procedure

### Run A -- our fully-ported build

    rtk python3 tools/build/build.py -q --rng-trace
    ./tools/xbox/build_deploy_run.sh -q
    # play a system-link game until the seed-mismatch message appears
    rtk python3 tools/xbox/rng_trace_dump.py --host 127.0.0.1 --out a.json

### Run B -- stock behaviour, instrumented RNG

    rtk python3 tools/xbox/rng_trace_variant.py --baseline   # ported=false everywhere except random_math.obj
    rtk python3 tools/build/build.py -q --rng-trace
    ./tools/xbox/build_deploy_run.sh -q
    # play the SAME scenario
    rtk python3 tools/xbox/rng_trace_dump.py --host 127.0.0.1 --out b.json
    rtk python3 tools/xbox/rng_trace_variant.py --restore

For a system-link run with bridged xemu guests, replace each local deploy with
`rtk ./tools/xbox/build_deploy_run.sh --xemu-bridged --xbox <guest-ip> -q`.
See `docs/xemu-bridged-deploy.md` for the WSL/XBDM transport details.

`--baseline` snapshots `kb.json` to `artifacts/rng_trace/kb.json.pre-baseline`
and `--restore` puts it back byte for byte. **Never commit the baseline
kb.json** -- it is a throwaway build; the pre-commit deactivation gate
(`tools/audit/check_ported_deactivations.py`) will reject it.

It deactivates 5361 lifts and keeps 51: the 36 in `random_math.obj`, plus 15
listed in `KEEP_FUNCTION_ADDRS`.  Three of those are the traced seed writers
(`set_random_seed`, `network_game_set_random_seed`,
`game_initialize_for_new_map`), which must stay live so both captures carry
the same reseed records.  The other 12 are not a judgement call --
`patch.py` deactivates a lift by splatting a redirect over our own impl entry,
and for an `@<reg>` function that redirect is a register-marshalling stub of up
to 41 bytes.  Where our impl body is shorter than its stub, `patch.py` refuses
rather than overwrite the next export and the `patched_xbe` step fails:

    ERROR Deactivation stub for "FUN_001550c0" (37 bytes) exceeds impl room
          (32 bytes to next export at 73dca0)

The 12 were found by replaying `patch.py`'s own
`generate_deactivation_redirect()` over `build/halo`'s export table and
comparing each stub against the gap to the next export.  None of them draw from
the global seed, so run B's trace is unaffected; they simply mean run B is
"stock behaviour except for 12 leaf lifts".  If a future lift batch makes
`--baseline` fail again, recompute the list the same way rather than dropping
objects from the run.

### Compare

    rtk python3 tools/xbox/rng_trace_dump.py --diff a.json b.json

Prints the first record index where `(tick, kind, caller, seed_before)` diverges,
10 records of context on each side, and a per-caller draw-count table for the
diverging tick -- the caller whose count differs is the desyncing function.
Comparison is by **symbol name**, not address: run B's callers live in the
original XBE while run A's live in the appended PE.

## Caveats

- **Ring wrap.** 65536 records. A busy firefight burns through them fast; dump
  soon after the mismatch. `wrapped: true` in the JSON means the oldest draws
  were overwritten and the two captures may no longer start at the same event.
- **Per-draw overhead.** One non-inlined call, a pointer compare, a NULL check,
  and a 16-byte store. Enough to shift frame timing
  slightly, so replay both runs from the same deterministic input fixture where
  possible (`tools/xbox/capture_scenario.py`).
- **`caller2`.** Always 0. The one-hop EBP walk was removed: an unframed caller
  can leave an aligned garbage saved-EBP that passes a range check and faults.
- **Untraced writers.** Any code that advances `0x46e3f4` without going through
  a hooked primitive would shift the sequence and produce a phantom divergence.
  This was measured rather than assumed: scanning the pristine
  `halo-patched/cachebeta.xbe` for the LCG multiplier immediate `0x0019660d`
  finds **10 occurrences, all 10 inside `random_math.obj`**
  (`[0x10a930, 0x10cb20)`), spread over exactly the seven hooked primitives --
  `random_math_real`, `random_real_range`, `random_seed_step`, `random_range`,
  `random_seed_get_direction3d`, `seed_random_orientation` (3 steps) and
  `random_direction3d` (2 steps, the second one via `random_real_range`).  The
  original build inlined the LCG nowhere else, so no global-seed draw in run B
  is invisible to the hooks.  On our side, the only inlined LCG outside
  `random_math.c` is in `src/halo/effects/decals.c` (two sites) and both use
  `random_math_get_local_seed_address()`, i.e. the local seed we deliberately
  skip.  `continuity_breaks` should therefore be empty; the dump tool checks it
  for free (consecutive draws must satisfy `seed[n+1] == lcg(seed[n])` applied
  `steps` times) and a non-empty list means a new writer has appeared and the
  trace can no longer be trusted as a complete record.
- **Not thread-safe.** `write_index` is a plain increment. Game logic draws from
  one thread; a concurrent draw would interleave, not corrupt the header.
