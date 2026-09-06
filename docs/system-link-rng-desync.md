# System-link lockstep desync: client/server random seed mismatch

Status: **OPEN** (2026-09-06). A float-association root cause was proposed and
then **REFUTED** by measurement -- see "Refuted: float addend association"
below. The desync mechanism remains unproven. The September 5 continuation
found and fixed a separate, exhaustive-test-confirmed RNG value discrepancy;
see "Continuation: RNG reciprocal and runtime isolation" below.

## September 6: animation-path accuracy audit

The paired trace's first extra draw is in `model_animation_choose_random`,
following a client animation-state transition. This prioritizes
`unit_animation_set_state`, `unit_update_animation`, their transition helper
`FUN_001a86b0`, and the animation update/set wrappers. It does not establish
which earlier state update caused the two machines to diverge.

The VC71 comparison parser was truncating valid cold switch arms after an
early return. Its whole-object path collected label positions but did not
pass label names to the existing back-edge-aware trimming routine. This
discarded 86 real instructions and eight calls in `unit_animation_set_state`.
`tools/verify/compare_obj.py` now passes those names; four regression tests in
`tools/verify/test_compare_obj_disassembly.py` cover cold arms, offset labels,
real trailing tables, and neighboring functions. Existing self-tests pass.
The historical 76.8% setter score below is therefore superseded by an
**83.9506% corrected baseline**, before any source improvement.

| Function | Corrected baseline | After source change | Binary-backed change |
| --- | ---: | ---: | --- |
| `model_animation_choose_random` | 98.2759% | 100.0000% | Return `int16_t`, matching `MOV AX, SI` at `0x120fc0`; all 14 direct callers checked. |
| `unit_animation_set_state` | 83.9506% | 87.2629% | Reload the animation graph tag index at the original call sites instead of caching it across calls. |
| `unit_update_animation` | 90.3790% | 92.8047% | Remove a redundant switch range guard and restore the nested matrix-call argument evaluation shape. |

These are VC71 instruction-match scores, not raw byte identity or runtime
equivalence. The chooser's final operand score is 96.5517%. Each kept source
candidate passed its complete translation-unit regression gate (208 functions
in `units.c`, 23 in `model_animations.c`) without missing functions, lowered
neighbor scores, or increased warnings. The knowledge-base change is limited
to the chooser's return type; parameter and register annotations are unchanged.

Other inspected functions: `FUN_001a6350` 89.8596%, `FUN_001ab870` 96.7742%,
`unit_set_animation` 95.5% with ABI modeling, and `FUN_001a86b0` 100%.
No speculative source edits were made to these functions. In particular,
the transition helper's perfect VC71 result does not cover its deployed clang
EDX live-out mismatch described below. That mismatch invalidates some partial
original/ported animation experiments, but has not been shown to cause the
normal fully patched build's desync.

Source accuracy improved; **the mixed-build desync remains open**. The next
runtime comparison must use coherent builds and capture both animation probe
histories at the first differing RNG draw, before the later disconnect.

Final validation: the combined units and model gates pass after the return-type
correction. The isolated `halo-rng-models` clang build with `HALO_RNG_TRACE=ON`
completed `tools/build/build.py -q --rng-trace --target patched_xbe` successfully.
The changed-file hazard scan has no new findings; the updater's duplicate
arguments match the original's intentional in-place vector operations.
The exhaustive RNG test still has zero binary32 and x87-result mismatches for
all 65,536 outputs at both tested precision settings. Baselines, candidate
ledgers, combined gates, and exact source hashes are retained under
`artifacts/rng_trace/accuracy/`. The new XBE has not been deployed.

## Symptom

A system-link game between a pristine build-2276 host (`cachebeta.xbe`,
xemu at 10.0.0.24) and our re-implemented client (`halo-patched/default.xbe`,
xemu at 10.0.0.21) can desync at startup or later during active gameplay:

    out of sync: client/server random seed mismatch, update= #N ... (#client/#server)

Reproduces **without any combat** (no shots, no grenades, no explosions), so
the divergence is not in the damage/projectile path. Reproduction matrix:

| host       | client     | result |
|------------|------------|--------|
| cachebeta  | cachebeta  | in sync |
| reimpl     | reimpl     | in sync |
| cachebeta  | reimpl     | **desync** |

The client's seed distance from the server is always a small number of draws
(1 behind, 1 ahead, 2 behind), sometimes self-correcting a tick later. This is
a draw-count drift, not a corrupted seed.

## Mechanism

Lockstep determinism depends on both machines drawing from the global seed
(`0x46e3f4`, LCG `s*0x19660d+0x3c6ef35f`) the same number of times per tick.
Every draw is traced by the `HALO_RNG_TRACE` ring (`docs/rng-trace.md`), plus
"info" probes (kinds 15-18) that record animation state transitions without
touching the seed.

Trace analysis (`artifacts/rng_trace/a8_immediate.json`,
`a9_animation.json`, decoded with `tools/xbox/rng_trace_dump.py --probes`):

- Every seed mismatch coincides with an **animation** RNG draw:
  `model_animation_choose_random` (0x120f20) called either from the original
  `animation_update_internal` (0x121c30, unported) when an animation
  completes and re-randomizes, or from `unit_animation_set_state` (0x1ad260)
  when a unit changes animation state.
- a8: tick 66 client one draw behind, catches up at tick 67.
- a9: tick 97 unit `e2aa003b` transitions state 6 -> 0. Client draws twice
  (main anim + weapon idle: `e43aa3da`, `30fc2171` -> `7298ac1c`); server sits
  at `30fc2171`, one draw behind the client. Ticks 217-228 client two behind.
- So one side reaches an animation completion or state change one tick
  earlier than the other. The random draw itself is correct; its **timing**
  differs.

## Continuation: RNG reciprocal and runtime isolation

The plane store/reload correction in `FUN_0010a1c0` is present in commit
`e56821009`. A fresh traced build with that correction (XBE SHA-256 prefix
`5e5918c93c87c078`) was deployed to `10.0.0.21` and its running build identity
verified. Against the pristine host, it survived idle play, movement, and a
kill, then desynced following grenades at tick **3842**. The client seed
`143582d7` was four LCG steps behind the host's `ccf76ffb`. Therefore the plane
correction is **not sufficient** to fix system-link determinism. The capture
has no seed-continuity breaks:
`artifacts/rng_trace/roundtrip_failure_20260905.json` and
`roundtrip_client_pass_20260905.txt`.

Temporary patcher overlays, without changing `kb.json`, gave these results:

| Original implementations selected on client | XBE SHA-256 prefix | First mismatch |
|---|---|---|
| `unit_update_animation`, `unit_animation_set_state`, `FUN_001ab870`, `unit_set_animation`, `model_animation_choose_random`, `FUN_001a6350` | `71e43043639337ed` | tick 3 |
| `unit_update_animation`, `unit_animation_set_state`, `FUN_001a6350` | `2c823d7f7ea0cc91` | tick 3 |
| `unit_animation_set_state` only | `ade116bad2eaaf83` | tick 1840, following grenades |

Original entry bytes and implementation deactivation redirects were verified
over XBDM, with the still-patched plane function as a positive control. The
standard QMP gate was unavailable. The immediate-failure variants repeatedly
selected animation `-1` once per player per tick; this is a different failure
pattern and **does not rule out** the animation callers or their remaining
ported dependencies. Restoring the original state setter alone did not fix
the grenade reproduction. Captures are named `animation_original_20260905`,
`animation_three_20260905`, and `animation_setter_20260905` under
`artifacts/rng_trace/`.

### Confirmed numeric bug in `random_math_real` (0x10b240)

The original instruction at **0x10b268** is
`fmul dword ptr [0x2647f4]`. That constant is binary32 **0x37800080**,
approximately `1.5259021893143654e-5`. The lifted C divided by `65535.0f`, and
the shipping clang object emitted `fdiv`, so it computed a different value
despite advancing the seed correctly. The correction is:

```c
return (float)(s >> 16) * *(float *)0x2647f4;
```

`tools/verify/rng_real_native.py` compiles complete translation units with and
without the correction, extracts their actual function instructions, and runs
them beside the pristine instructions in a freestanding Linux i386 process.
Only absolute constant addresses are relocated for that test. It exhausts
all **65,536 possible upper-16-bit outputs**, validates seed updates, and
compares both the rounded binary32 result and the returned x87 value:

| Precision | Before: binary32 differences | Before: x87 differences | After: either representation |
|---|---:|---:|---:|
| PC=11 (64 bits) | 512 | 65,535 | 0 |
| PC=10 (53 bits) | 512 | 65,535 | 0 |

Example: upper bits `257` produce `0x3b808081` with the old division, versus
the original's `0x3b808080`. This is a measured **numeric** difference, unlike
the earlier symbolic association finding. The neighboring `random_real_range`
already uses the correct reciprocal and its arithmetic was checked against
the original instructions.

Validation: native exhaustive comparison passes; VC71 is 100% instruction
match (16/16, operand score 87.5%); changed-file hazard scan is clean. This
proves the RNG value fix, **not yet its responsibility for the desync**.
All six diagnostic animation toggles were removed for the runtime test of
the reciprocal correction (XBE prefix `03dfe393601605d9`), and their live
redirects were verified. That build still desynced at tick **1705**, with
client/host seeds `416f5584`/`9867bb02`; the user reported **no grenades** in
this reproduction. The numeric fix is therefore also **not sufficient**.
Capture: `artifacts/rng_trace/rng_reciprocal_20260905_trace.json`, with no
continuity breaks. `kb.json` is unchanged.

### Paired traces and the partial-toggle ABI confound (September 6)

The normal client above was compared with a separately named host diagnostic:
original behavior except `random_math.obj` and the trace baseline's 15 retained
functions. The host diagnostic SHA-256 is
`a2a004b653cda20e795cfc2f06884921da0b463764b06ff297bf817a2caba59d`.
Idle play and then movement alone remained synchronized for several minutes.
The later reproduction disconnected at tick **18949**, seeds
`956ab6d3`/`e8460c39`. Both rings have no continuity breaks.

The first differing draw is earlier, at **18883**: client
`model_animation_choose_random` consumes seed `d67e8b5d`; the host consumes
that seed at **18886**. The client also draws at 18886 and 18889, leaving it
two draws ahead. Client probes identify unit `e4c70035`: after spawn-selection
draws at 18881, it changes animation state `0x15 -> 0` at 18882, then
`0 -> 2` at 18883. Thus disconnect time is delayed relative to the first
observed RNG divergence; it is not evidence of a fixed elapsed-time trigger.
The host capture lacks corresponding animation probes, so the earlier
unit-state divergence is still unlocalized.

Evidence: `artifacts/rng_trace/paired_original_20260905_{trace,host_trace}.json`,
`compare_pair.py paired_original_20260905`, and its comparison JSON.

The earlier immediate failures with original `unit_update_animation` have a
confirmed ABI confound. Original `FUN_001a86b0` preserves EDX. At `0x1b120c`,
the original caller calls this helper, then at `0x1b1215` pushes EDX as the
desired state without reloading it. The deployed reverse thunk at `0x9091aa`
and C implementation at `0x6eefd0` overwrite EDX. A machine-code comparison
over all 256 old-state bytes and 44 requested states finds identical AL
returns in all 11264 cases, but EDX differs in 11220. For old state 1 and
requested state 0, original EDX is 0 and candidate EDX is `ffffffff`.
Therefore toggling the original caller without also restoring this helper
is not a valid isolated comparison. This does **not** yet attribute the
normal-build desync to that helper; the compiled C caller keeps its requested
state separately. Evidence: `check_transition_liveout.py` and
`transition_liveout_evidence.json` in `artifacts/rng_trace`.

For the next paired capture, the host now has probes inserted directly around
the original main-animation update and desired-state transition calls.
The diagnostic preserves all registers and flags and performs no x87 work.
The stolen calls execute once with their original arguments. Eighteen
machine-code tests cover register/flag preservation, argument and unit-write
equivalence, state packing, and ring wrap. Both detours and original helpers
were verified in live host memory. Diagnostic SHA-256:
`fa079c608c6c350f2e0cb0b017b129f48dff7c24b8298d1fb9853fbfa8e72556`.
Scripts: `build_original_probes.py`, `test_original_probes.py`,
`verify_original_probes.py`. The host's original executable remains at
`E:\GAMES\halo-patched\cachebeta.xbe`; `host_diagnostic.py restore` relaunches
its recorded path. No `kb.json` toggles were changed.

Another build replaced the shared `build/halo` symbol file during capture.
The deployed symbols were recovered from the saved diagnostic XBE as
`artifacts/rng_trace/session_symbols.pe`; subsequent captures explicitly use
that file. Its function bodies include host deactivation stubs, so it is a
symbolization artifact, **not an unmodified compiler output for rebuilding**.

## Previously audited paths (not blanket exclusions)

Audited semantically against the pristine XBE (Capstone on
`halo-patched/cachebeta.xbe`; Ghidra MCP was intermittently down):

- Grenade/damage path, all clean: `object_find_in_radius`,
  `collision_bsp_test_vector`, `FUN_00148780`, `FUN_00148240`,
  `object_find_in_cluster`, `structure_find_in_cluster`,
  `object_cause_damage` (only diff: missing debug store to `0x46f070`),
  `FUN_00136f40`, `FUN_0009dcf0`, `FUN_00138e30`, `damage_data_new`,
  `FUN_0009d2d0`, `object_new`, `object_placement_data_new`,
  `unit_throw_grenade`, `FUN_000f9c40`, `FUN_000f7e40`, `FUN_000f8920`,
  `FUN_000f7e60`, `FUN_000f90d0`. Moot anyway: desync reproduces with no
  combat.
- `unit_animation_set_state` (0x1ad260; historical VC71 score corrected above): weapon-idle draw guard
  (`was_none || FUN_001a88b0(new) != FUN_001a88b0(old)`) matches the XBE;
  a 6 -> 0 transition must draw twice on both sides.
- `unit_update_animation` (0x1b0d90): clean vs XBE after byte-accuracy edits
  (dword `global_seat` load, signed `+0x256` switch, `anim_status_wide`).
- `FUN_001ab870` (0x1ab870) wrapper around the original
  `animation_update_internal`: probes show the frame counter (`state[1]`)
  and anim index (`state[0]`) going in, result coming out.

## Refuted: float addend association

**This section proposed a root cause that measurement later killed. Kept as a
record of a dead end, not as a finding.**

The refutation: reassociating a 3-term float dot product can only change the
result if the x87 is truncating intermediates to 24-bit single precision. At
53-bit or 64-bit it is exactly inert, because a float x float product needs only
48 mantissa bits and sums of three such products stay exact. Measured over
1,000,000 plausible inputs for `plane3d_distance_to_point`:

    intermediate precision   general-case   near-cancellation
      24-bit (PC=00)           31.075%          39.835%
      53-bit (PC=10)            0.000%           0.000%
      64-bit (PC=11)            0.000%           0.000%

Halo runs at 64-bit. Game code (0x11000-0x1d0000) contains **zero** `fldcw`
instructions; every one in the binary is in the CRT (`_controlfp`, and `_ftol`
setting rounding-control, not precision-control), and `fninit` at 0x1db4de
leaves the default 0x037F (PC=11, 64-bit extended). Nothing in the engine
narrows FPU precision, so addend order cannot produce a ULP difference.

`tools/audit/check_fpu_association.py` compares symbolic expression trees. It
proves a *structural* difference in how our clang binary accumulates, which is
necessary but **not** sufficient for an observable numeric difference. Treating
its output as a numeric result was the error here.

The original hypothesis follows, for the record.

## Superseded hypothesis: dot-product association

`FUN_00013070` (0x13070, the 3D dot product) accumulated its three terms in the
opposite order from the original.

Ours (as lifted):

    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];   /* ((x + y) + z) */

The original at 0x13070:

    fld [eax+8]; fmul [ecx+8]    ; z
    fld [eax+4]; fmul [ecx+4]    ; y
    faddp st(1)                  ; (z + y)
    fld [eax];   fmul [ecx]      ; x
    faddp st(1)                  ; ((z + y) + x)

Floating-point addition is commutative but not associative, so `((x+y)+z)` and
`((z+y)+x)` differ by a ULP. `FUN_00013070` has 22 call sites and feeds AI
facing (`actions.c`, `actor_looking.c`), biped orientation (`bipeds.c`),
physics (`collision_usage.c`) and structure queries — so every 3D dot in the
engine was off by a ULP on our client and exact on the host.

That is enough to desync lockstep. The animation state byte comes from
`state_pair`, written by the **unported** originals `FUN_001a4c50` (turning) and
`FUN_001a5300` (moving). Those originals read our ported floats and compare them
against thresholds (`FUN_001a5300` at 0x1a5531 gates moving-vs-idle on the
throttle vector at `unit+0x228` being nonzero). A ULP flips such a threshold a
tick early or late, which moves an animation state change by a tick, which moves
the RNG draw by a tick. Symptom fit is exact: drift in both directions,
self-correcting, no combat needed, and reimpl-vs-reimpl stays in sync because
both sides are wrong the same way.

### Why every gate passed

VC71 scores `FUN_00013070` at 100.0% (14/14 insns, opnd 100.0%) both before and
after the fix. VC71 compiles our C with **cl.exe (MSVC 7.1)**, which reassociates
the expression back to the original's order. The binary we ship is built by
**clang**, which honours C's left-associativity. Any float-association
difference cl.exe normalises away is invisible to the byte-match lane by
construction. See `docs/lift-learnings.md` section 57.

Two-term reductions are safe (`a+b == b+a` is exact in IEEE); only chains of
three or more terms can diverge.

### Change applied anyway

    /* src/halo/math/vector_math.c */
    return a[2] * b[2] + a[1] * b[1] + a[0] * b[0];

Harmless and marginally more faithful to the original, but **not a fix for the
desync** -- see the refutation above. VC71 remains 100.0%.

`FUN_00012f60` (2D dot, 0x12f60) loads its terms in the other order too, but
with only two terms the result is bit-identical. Not a bug; left alone.

## Sweep: 16 more functions in the same class

`tools/audit/check_fpu_association.py` (new) symbolically executes the x87
stream of our clang objects and of the original XBE, builds an expression tree
for each, canonicalises the differences that carry no numeric content, and
reports the rest. Canonicalisation covers: commutative two-term nodes (`a+b ==
b+a` is exact), negation placement inside mul/div chains (`-(a/b) == (-a)/b`),
`.rdata` float literals versus `FLDZ`/`FLD1`, and a uniform parameter-index
shift. Register-to-parameter binding is flow-sensitive, because the original
reuses one register for two different parameters (e.g. "ECX switches from
param_1 to param_2" in `FUN_001057c0`). Anything outside pure-FPU straight-line
code is reported SKIP rather than guessed at.

Run: **736 compared, 31 mismatches across 16 functions** (plus `FUN_00013070`,
now fixed). Full report in `artifacts/fpu_assoc/sweep_20260905.txt`.

| function | object |
|----------|--------|
| `distance_squared3d` | `math/vector_math.c` |
| `FUN_0001ad60`, `FUN_0010a1c0` | `math/real_math.c` |
| `matrix_transform_point`, `matrix_transform_vector` | `math/real_math.c` |
| `real_matrix3x3_transform_vector`, `real_matrix4x3_transform_point` | `math/real_math.c` |
| `FUN_0010c340`, `FUN_0010c8e0` | `math/random_math.c` |
| `FUN_001057f0` | `structures/structures.c` |
| `FUN_00193b80` | `structures/structure_detail_objects.c` |
| `FUN_0018d670` | `scenario/scenario.c` |
| `midpoint3d` | `ai/actor_moving.c` |
| `plane3d_distance_to_point`, `triple_product3d` | `effects/decals.c` |
| `real_rgb_color_brightness` | `bitmaps/bitmap_utilities.c` |

Every one is the same shape as `FUN_00013070`: our clang code accumulates
`((x+y)+z)` where the original accumulates `((z+y)+x)`. None of them are fixed
yet — see "Confirming the fix" for why.

## Superseded hypothesis

Some **ported state writer upstream of the animation update** produces the
unit's animation state byte (`unit+0x253`), movement byte (`unit+0x256`), or
frame timing input one tick off from the original. Candidates, in order:

1. `unit_update_animation` (0x1b0d90) or the callee chain under it
   (`unit_animation_set_state`, `FUN_001ab870`).
2. The `state_pair` writers in the biped update `FUN_001a6350` (0x1a6350,
   89.9%): `FUN_001a4c50` (turning), `FUN_001a5300` (moving),
   `FUN_001a6280` (dying, 86.4%), `FUN_001a2900`, `FUN_001a2a60`.
3. The dead flag at `unit+0xb6` bit 2/4 and other `unit_update_animation`
   callers (`0x1b300a`, `0x1b9735`).

## Toggle-bisect: abandoned

`ported: false` is per-function, so deactivating `unit_update_animation`
(0x1b0d90) leaves its separately-ported callees (`unit_animation_set_state`,
`FUN_001ab870`, `unit_set_animation`, `model_animation_choose_random`) still
redirected to our C. "Desync persists therefore the callees are ruled out" would
have been an invalid inference. The experiment was dropped once the dot-product
divergence was found; the diagnostic `ported: false` has been reverted.

## Confirming the fix

The remaining step is an in-game repro: rebuild, deploy to 10.0.0.21, and run a
system-link match against the pristine host on 10.0.0.24 using the procedure
below.

- (Obsolete: the float-association theory is refuted; the repro below no longer
  tests anything about it.)
- Desync gone: `FUN_00013070` was the cause; work through the sweep list next.
- Desync persists: `FUN_00013070` was a real but separate latent bug; resume from
  the trace evidence, starting with `distance_squared3d` and
  `plane3d_distance_to_point`, which are on the same movement path.

Fix exactly one thing before the repro. The other 15 are deliberately left
unfixed: changing 16 reduction orders at once makes a negative result
un-attributable.

Authoritative build path for the deploy is `/mnt/g/dev/halo/build`. A concurrent
build in the `/mnt/g/dev/halo-bugs` worktree runs on this box; do not deploy
from it.

## Procedure (bridged xemu from WSL)

Guests are reachable from Linux only; Windows Python times out
(`WinError 10060`).

    # build (about 5 min)
    rtk python3 tools/build/build.py -q --rng-trace
    # push to the patched client
    HALO_NATIVE_XBDM=1 HALO_WINDOWS_REEXEC=1 python3 tools/xbox/deploy_xbox.py --skip-build --xbe-only -x 10.0.0.21
    # after reproduction, while still in game (ring is lost on return to dashboard)
    HALO_WINDOWS_REEXEC=1 python3 tools/xbox/rng_trace_dump.py --host 10.0.0.21 --out artifacts/rng_trace/aN.json
    python3 tools/xbox/rng_trace_dump.py --probes artifacts/rng_trace/aN.json
    HALO_WINDOWS_REEXEC=1 python3 tools/xbox/xbdm_debug_txt.py --host 10.0.0.21 --lines 200 --output artifacts/rng_trace/debug_client_aN.txt --timeout 30
    HALO_WINDOWS_REEXEC=1 python3 tools/xbox/xbdm_debug_txt.py --host 10.0.0.24 --lines 200 --output artifacts/rng_trace/debug_host_aN.txt --timeout 30

The push command trips the skill-router gate once; rerun it unchanged.
`debug.txt` on both boxes carries the `out of sync` line with the tick and
both seeds; correlate its tick with the trace records.

## Uncommitted work tied to this investigation

- Probes: `src/halo/math/rng_trace.h` kinds 15-18; `units.c`
  (`unit_animation_set_state` kind 16, `FUN_001ab870` kinds 17/18);
  decoder in `tools/xbox/rng_trace_dump.py`. All under `#ifdef HALO_RNG_TRACE`
  with `#line` restores.
- Byte-accuracy edits in `units.c` (`unit_animation_state_allows_impulse`,
  `unit_update_running_blind`, `unit_update_animation`, `FUN_001b1400`) and
  `damage.c` (`object_cause_damage`).
- `kb.json`: 0x120670 decl `build_damage_animation_index`; diagnostic
  `ported=false` on 0x1b0d90.
- `tools/xbox/deploy_xbox.py`: `HALO_NATIVE_XBDM=1` uses Linux Python for
  XBDM upload.

## Latent issues found along the way (not the desync)

- `0x9dcf0` missing `@eax/@edx/@ecx/@esi` annotations.
- `FUN_000f7e60` missing `@esi/@edi/@eax/@edx/@ecx`.
- `FUN_00148eb0` param_3 declared int, is float.
- `object_cause_damage` lacks the original's debug store to `0x46f070`.
- Sub-90% VC71 on the path: `unit_animation_set_state` 87.3 after the audit above,
  `FUN_000f7e60` 72.2, `FUN_000f9c40` 89.1, `FUN_001a6350` 89.9,
  `FUN_001a6280` 86.4.

## Paired capture 2026-09-06 (shoot-only): divergence localized

First paired capture with animation probes live on BOTH sides. Client
`10.0.0.21` ran the traced build (rev `25e26a513`, `RNGT` ring present); host
`10.0.0.24` ran `rng_probe.xbe` (SHA `fa079c60...`, detours re-verified in live
memory after a CI incident). Artifacts:
`artifacts/rng_trace/shoot_only_20260906{,_host}.json`,
`shoot_only_20260906_host.bin` (raw ring), and
`debug_{client,host}_shoot_only.txt`.

Desync reported by the client at update #1121
(`#5e9321ab`/`#8719cf51`); the host declared client machine #1 out of sync at
game tick #1249.

### Alignment

Both sides reseed to `000040b2` at the match start (host ring index 119496,
client 119262). Frame numbering is IDENTICAL on the two sides -- an assumed
one-tick offset scores 41.9% agreement on `anim_update_in` values versus 87.3%
at offset 0, so offset 0 is the alignment. Do not assume a tick skew.

### First divergence

1217 consecutive seed-consuming draws match exactly. Draw ordinal 1218, with
seed `cc6b2274` still identical on both sides:

    HOST    tick 996  random_direction3d   caller 0x1aba66
    CLIENT  tick 995  random_math_real     caller model_animation_choose_random+78

The client consumes a draw the original never consumes. Its cause is one frame
earlier, on unit handle `0xe3170037`, at the state-request site
(`unit_update_animation+862`, original `0x1b1215`, which IS instrumented on both
sides):

    frame 993   anim_state=0x15 old=0xff    host YES   client YES
    frame 994   anim_state=0x00 old=0x15    host YES   client YES
    frame 995   anim_state=0x03 old=0x00    host NO    client YES  <-- extra

The extra transition calls `unit_animation_set_state+779`, which calls
`model_animation_choose_random`, which draws. From frame 996 the unit's state
byte is `0xbd` on the client versus `0xa8` on the host and never reconverges.

### What is NOT the cause

- **Not an extra animation-update call.** Site-matched (client `+484` only,
  the site corresponding to the host's sole `anim_update_in` probe at
  `0x1b0f58`), call counts are (1,1) on all 7757 comparable frames.
  An earlier "client calls 2-3x, host never double-calls" reading was an
  INSTRUMENTATION ARTIFACT: `unit_update_animation` has FOUR call sites to
  `FUN_001ab870` (+413, +456, +997, +1071) and only +456 carries a host probe,
  while our build probes four sites (+436, +484, +682, +765). Any future
  cross-side count comparison must filter to the site the host actually probes.
- **Not `state[1]`.** The second half of the pair is a frame counter that
  increments in lockstep on both sides.

### The remaining question

At `0x1b11f9` the original loads the CURRENT state into CX and takes either of
two skips before transitioning:

    001b1201  cmp   dx, cx
    001b1204  je    0x1b121f        ; skip 1: desired == current
    001b120c  call  0x1a86b0        ; gate; preserves EDX
    001b1211  test  al, al
    001b1213  je    0x1b121f        ; skip 2: gate returned 0
    001b1215  push  edx             ; EDX reused WITHOUT reload
    001b1217  call  0x1ad260        ; unit_animation_set_state

Current state was `0x00` and our desired state was `0x03`, so skip 1 cannot
have fired for us. Either the original's desired state (EDX) was `0x00` at that
moment and it took skip 1, or its gate `FUN_001a86b0` returned 0 and it took
skip 2. Distinguishing these two is the next step, and it decides the fix:

- If EDX differed, the bug is UPSTREAM in whatever computes the desired state.
- If the gate differed, the bug is in `FUN_001a86b0` -- note its AL return was
  previously verified identical across all 11264 (old_state, requested_state)
  machine-code cases, but it takes a POINTER (`lea ecx,[edi+0x248]`) and reads
  `[ecx+0xb]`, so its result depends on struct contents that sweep did not vary.

A third probe recording EDX and the gate's AL at `0x1b1211` on both sides would
settle it in one capture.

## Static resolution of the frame-995 fork (2026-09-06, no new capture)

The previous section ended by proposing a third probe on EDX and the gate's AL
at `0x1b1211`. That capture is NOT needed: both branches of the fork resolve
statically, and one of them also closes an instrumentation gap that would have
invalidated the whole section.

### Instrumentation gap check (this had to pass first)

The `FUN_001ab870` episode taught that a single host probe on a multi-site
callee manufactures fake findings. So before trusting "host took no transition
at frame 995", count the call sites to the transition callee:

    call 0x1ad260 (unit_animation_set_state) inside unit_update_animation
      0x1b1217  (+1159)   <-- the probed site (host patch at 0x1b1215)
      TOTAL: 1 site

One site, and it is the probed one. The claim holds: the host really did not
transition at frame 995.

### The gate `FUN_001a86b0` is exonerated analytically

Its entire input domain is two values -- `byte [ecx+0xb]` and DX:

    001a86b0  movsx ecx, byte ptr [ecx + 0xb]
    001a86b4  add   ecx, -2
    001a86b7  cmp   ecx, 0x27
    001a86ba  mov   al, 1
    001a86bc  ja    0x1a86ec          ; -> ret with AL=1
    001a86be  movzx ecx, byte ptr [ecx + 0x1a8704]
    001a86c5  jmp   dword ptr [ecx*4 + 0x1a86f0]

All five jump-table arms (`0x1a86cc/d5/e5/ea/ec`) read only DX. Nothing else is
loaded, so the earlier 11264-case (old_state, requested_state) sweep WAS
exhaustive -- the doc's earlier note that "struct contents the sweep did not
vary" could matter is wrong, and is corrected here.

Stronger still, for this exact frame: the caller does
`lea ecx,[edi+0x248]`, so `[ecx+0xb]` is `[edi+0x253]` -- the *same* current-state
byte the caller loads into CX. Current state was 0x00, so `ecx = 0 - 2 =
0xFFFFFFFE`, which is `ja 0x27`, so the gate returns **AL=1 unconditionally**.
Skip 2 cannot have fired on either side.

### There is a THIRD path to the call, not two

    001b11ee  mov   al, byte ptr [ebp - 1]
    001b11f1  test  al, al
    001b11f3  mov   edx, dword ptr [ebp - 0xc]
    001b11f7  jne   0x1b1215            ; force: bypasses BOTH skips
    001b11f9  movsx cx, byte ptr [edi + 0x253]
    001b1201  cmp   dx, cx
    001b1204  je    0x1b121f            ; skip 1
    001b1206  lea   ecx, [edi + 0x248]
    001b120c  call  0x1a86b0
    001b1211  test  al, al
    001b1213  je    0x1b121f            ; skip 2 (proven inert here)
    001b1215  push  edx
    001b1216  push  esi
    001b1217  call  0x1ad260

`[ebp-1]` is a force flag, set at `0x1b115a` when `FUN_001a8790` returns 0.
With skip 2 inert, the host not transitioning means the host had force==0 AND
desired state == current state == 0. Ours pushed 3.

### The desired state is an input PARAMETER, so the bug is in the caller

`[ebp-0xc]` has exactly two writes in the whole function:

    001b0dcd (+61)   mov dword ptr [ebp - 0xc], eax    ; eax = movsx ax, byte [ebp+0xc]
    001b0fe3 (+595)  mov dword ptr [ebp - 0xc], 0x28

`[ebp+0xc]` is param_2. So `unit_update_animation(unit_handle, char *anim_state)`
does not compute the desired state -- it receives it, and 0x28 != 3, so ours came
straight from `*param_2`. Nothing inside this function is at fault.

(Note for whoever edits this: at `0x1b0db8` the load is `movsx ax, ...`, a 16-bit
movsx that writes only AX, so the dword stored at `[ebp-0xc]` carries a stale
upper half from the preceding `tag_get` return. Harmless here because every
consumer uses DX, but do not "clean it up" into a 32-bit movsx.)

### Caller narrowed to three ported functions

`unit_update_animation` has no direct `call` site in the image; ours is
`src/halo/units/units.c:1186`, passing `state_pair`, initialized to 0 and then
filled by five callees (`units.c:1119-1138`):

    FUN_001a4c50   ported: null   <- runs ORIGINAL code, cannot diverge
    FUN_001a5300   ported: null   <- runs ORIGINAL code, cannot diverge
    FUN_001a2900   ported: true       writes 0x28 / 0x14
    FUN_001a2a60   ported: true       writes 0x15 / 0x16
    FUN_001a6280   ported: true       writes 0x18 / 0x19

No ported code anywhere in `src/` writes 3 into that byte (`rg '\*state(_out)? = 3'`
is empty; the only state writes in bipeds.c/units.c are the six values above).

So the value 3 is written by original code, and our divergence is that original
code *chose* to write it -- i.e. some input it reads differed, or a ported callee
it dispatches to returned differently. `FUN_001a5300` and `FUN_001a4c50` are
step dispatchers that call ported step functions (`FUN_001a2b90`'s header
comment names `FUN_001a5300` as its dispatcher), so a ported step corrupting
biped state upstream is the live hypothesis.

Next step is therefore NOT another probe on `0x1b1211` -- it is to find which
store in `FUN_001a4c50` / `FUN_001a5300` writes 3, and which ported callee feeds
its predicate.

### Negative result worth recording

The captured client build INCLUDED the `FUN_0010a5e0` x87-narrowing fix, and the
desync still reproduced with the same `model_animation_choose_random` signature.
That closes the x87-narrowing lane as a cause of this desync. The fix remains a
genuine correctness fix (see docs/lift-learnings.md and
tools/audit/check_x87_narrowing.py); it is simply not this bug.
