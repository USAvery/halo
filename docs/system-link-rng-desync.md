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

## The fork is a float comparison (2026-09-06, same session)

The section above ended by saying the next step was to find which store in
`FUN_001a4c50` / `FUN_001a5300` writes state 3. It is `FUN_001a4c50` at
`0x1a5183`, and the answer changes the shape of the investigation.

### The writer

Neither dispatcher stores a literal 3; both write the state byte through a
register. In `FUN_001a4c50`:

    001a5160  fld   dword ptr [eax + 0x4c8]   ; per-tag threshold
    001a5166  fld   dword ptr [ebp - 0xc]     ; computed value
    001a5169  fcomp st(1)
    001a516b  fnstsw ax
    001a516f  test  ah, 5
    001a5172  jp    0x1a5193                  ; skip the write entirely
    ...
    001a5183  mov   cl, byte ptr [ebp - 1]
    001a5186  mov   eax, dword ptr [ebp + 0xc]
    001a5189  test  cl, cl
    001a518b  setne dl
    001a518e  add   dl, 2                     ; dl = 2 or 3
    001a5191  mov   byte ptr [eax], dl        ; <-- the desired state

So the state is 2 or 3 depending on the `[ebp-1]` flag, and it is written at
all only on one side of an x87 compare. `2` and `3` are the two turn-in-place
directions.

### What the compared value is

From the aligned disassembly at `0x1a5061` (a jump target, so a safe boundary
-- disassembling from an arbitrary address here decodes garbage and invents
operands, which cost one wrong reading before this):

    001a5061  lea   eax, [esi + 0x1d4]        ; desired facing
    ...       copy to [ebp-0x20..], force z = 0
    001a5083  call  0x12f10                   ; magnitude
    001a5088  fcomp dword ptr [0x2533c0]      ; degenerate? then fall back to
    001a5098  lea   edx, [esi + 0x24]         ;   the current facing
    ...
    001a50ac  fld   dword ptr [ebp - 0x20]    ; cross_z = a.x*b.y - a.y*b.x
    001a50b2  fmul  dword ptr [edi + 4]       ;   -> sign selects [ebp-1],
    001a50ba  fsubp st(1)                     ;      i.e. which way to turn
    001a50bc  fld   dword ptr [ebp - 0x1c]    ; dot = a.x*b.x + a.y*b.y
    001a50cb  faddp st(1)
    001a50cd  fstp  dword ptr [ebp - 0xc]     ; <-- the compared value

`[ebp-0xc]` is the **cosine of the angle between the biped's desired facing
(`+0x1d4`) and its current facing (`+0x24`)**, and `[ebp-1]` is the sign of
their cross product. The fork at `0x1a5172` is therefore "is the biped turned
far enough from where it wants to face to play a turn-in-place animation", and
our biped answered yes where the original answered no.

**Struck 2026-09-06.** This paragraph previously claimed the same signature as
"the open a10 report of a biped that rotates without translating". That report
is not open -- it was fixed long ago -- so the cross-reference was wrong and
carried no evidence either way. Nothing else in this document depends on it.

### This REOPENS the float-precision lane

The previous section recorded, correctly, that the captured build already had
the `FUN_0010a5e0` x87-narrowing fix and still desynced. That remains true, but
the conclusion drawn from it -- "closes the x87-narrowing lane as a cause" -- was
too strong and is **withdrawn here**. It only rules out that one function. The
fork is decided by a single `fcomp` of a computed cosine against a threshold, so
a sub-ULP difference in the facing vectors flips it. Float precision upstream is
now the PRIME suspect, not a closed lane.

### Where it is not

Checked and clean (not flagged by tools/audit/check_x87_narrowing.py):
`normalize3d`, `magnitude3d`, the dot/cross helpers, and `FUN_001b0630` (the
ported aiming-vector update called from inside `FUN_001a4c50` itself). Our
ported normalization of `+0x1d4` (`src/halo/units/units.c:1058-1065`) is a
faithful in-place normalize with the z component zeroed and a world-forward
fallback.

### Where to look next

The full detector run is 5891 functions compared, 207 flagged (the earlier
"397 compared, 29 flagged" figure was a partial run; use the 207). Ranked
candidates that feed biped facing, worst first:

    FUN_0002bd80            src/halo/ai/actor_moving.c   ours 4,  xbe 15  (-11)
    FUN_001a2f40            src/halo/units/bipeds.c      ours 14, xbe 21  (-7)
    actor_destination_update src/halo/ai/actor_moving.c  ours 0,  xbe 4   (-4)
    FUN_0002b020            src/halo/ai/actor_moving.c   ours 0,  xbe 4   (-4)
    FUN_001a2160            src/halo/units/bipeds.c      ours 1,  xbe 3   (-2)
    FUN_001a1a10            src/halo/units/bipeds.c      ours 1,  xbe 3   (-2)
    actor_move_update       src/halo/ai/actor_moving.c   ours 3,  xbe 4   (-1)

`FUN_001a2f40` is notable because the unported dispatcher `FUN_001a5300` calls
it directly on the same tick, and it is the worst offender in the units/bipeds
group.

Note the whole chain runs through UNPORTED code (`FUN_001a4c50`,
`FUN_001a5300`), so the bug cannot be in the decision logic itself -- only in
the float inputs that ported code hands it. That is what makes the narrowing
detector the right instrument here rather than another capture.

## Candidate list corrected by intersection with the fork's inputs (2026-09-06)

The previous section ranked x87-narrowing candidates by raw delta. That was the
wrong instrument: a narrowing delta only matters if the function touches one of
the two vectors the `fcomp` at `0x1a5183` actually compares — the biped's
desired facing (`+0x1d4`) and its current facing (`+0x24`). Intersecting the
flagged set with the writers and readers of those two vectors reorders it and
drops one entry entirely.

| function | delta | touches the fork's inputs? | verdict |
|---|---|---|---|
| `FUN_0002bd80` (`actor_moving.c`) | -11 | reads `obj+0x24`, and its callers at `actor_moving.c:3717` take its `slerp`/`weight` outputs into the desired-facing path | **top candidate** |
| `FUN_001a2160` (`bipeds.c`) | -2 | it *is* the per-tick writer of current facing `+0x24` | second, but see below |
| `FUN_001a2f40` (`bipeds.c`) | -7 | 956 lines, no access to `+0x1d4`, `+0x24`, `+0x28`, `+0x2c` or `+0x30` anywhere in its body | **drop — noise for this bug** |

### `FUN_001a2160` site-level result

Per-site comparison (not just counts) narrows what its -2 means. The XBE
narrows three cross-product temporaries at `ebp-0x20/-0x1c/-0x18`
(`fstp dword` then `fld dword`, `0x1a21ef`..`0x1a2236`); our build keeps two of
the three live in ST at 64-bit. Those temporaries feed **only the up vector**
(`unit+0x30`). They reach the forward vector — the one the fork compares —
through exactly one edge: the degenerate test at `0x1a2248`,

    call 0x13010            ; normalize3d(up_ptr)
    fcomp dword ptr [0x2533c0]
    test  ah, 0x44
    jp    0x1a2283          ; skip the reset
    ...                     ; else fwd(+0x24) = global forward, up = global up

so a precision difference here only propagates when the rebuilt up vector is
near-degenerate. Real coupling, narrow band.

`cos_a`/`sin_a` are **not** a divergence here even though clang stores them as
`fstp tbyte [ebp-0x24]` / `[ebp-0x3c]`. Both are reloaded and narrowed with
`fstp dword ptr [esp+0xc]` / `[esp+0x8]` at the call boundary, so each value is
rounded to float32 exactly once, the same as the XBE's `fstp dword [ebp-8]`
straight after `fcos`. An 80-bit spill is only a finding when nothing narrows
the value before it is consumed.

### Two checks that must come before any more candidate grinding

The "it is an `fcomp`, therefore precision" step skips two questions, and two of
the three possible answers make the narrowing list the wrong tool entirely:

1. **Did the original even reach `0x1a5160`?** `FUN_001a4c50` has earlier
   integer exits — `je 0x1a52f9` at `0x1a4f65` when `[esi+0x257] == 0`, and the
   `and eax,0x40 / je 0x1a5061` split at `0x1a4f73`. If the host bailed before
   the compare, the divergence is in an integer or flag upstream and no float
   work touches it.
2. **How far apart were the cosine and the threshold?** A sub-ULP difference
   flips a compare *only when the operands are within an ULP of each other*.
   If `|cos - threshold|` is appreciable, the desired-facing vector is
   substantively wrong and this is a logic bug, not a precision one.

One probe at `0x1a5169` recording (reached-flag, ST0, ST1, unit handle)
discriminates all three outcomes in a single capture. Census its call sites
first, the same way `0x1ad260` was censused above.

Because of this, the earlier sentence "a sub-ULP difference in either facing
vector flips it" should be read as **only when the two operands are near-equal**.

### Tooling note

Do not name a scratch analysis script `/tmp/dis.py`. Python's `inspect` imports
the stdlib `dis` module, so a shadowing script in the CWD produces a confusing
circular-import traceback (and breaks `apport`'s excepthook) even though the
script's own output is correct.

## Paired capture 2026-09-06 (repro B): the divergence is TWO records

Second reproduction, both guests instrumented (client `10.0.0.21` trace build,
host `10.0.0.24` running `host_rng_probe.xbe`, ring at `0x7ff900` — dump it
with `--pe artifacts/rng_trace/session_symbols.pe`, the default cachebeta
symbol lookup finds the wrong VA and reports a magic mismatch).

Client ticks 0..6763, host 425..6847, first `out of sync` at tick **6635**.
Diffing every `probe:unit_state` record by (tick, unit, state) over the whole
overlap gives exactly two client-only records and **zero** host-only:

    t=6627  handle=0xe3c20037  new=3  old=0
    t=6633  handle=0xe3c20037  new=0  old=3

Everything else in ~6,800 ticks matches. The unit enters animation state 3 on
our build, sits there six ticks, and leaves; the original never enters it. The
exit at 6633 draws from the global seed via `model_animation_choose_random`
(three draws at t=6633/6634), and the seeds mismatch two ticks later. That
closes the mechanism: **an animation transition is a seed draw, so one extra
transition is one extra draw.**

### The recorded state value was always in the capture

`probe:unit_state` has no `value` field; the packed `(new<<8)|old` state is
carried in **`seed_before`**. Reading `r.get("value", 0)` yields a histogram of
all zeros and looks like "the probe records no state". It does. This also
retroactively confirms the earlier *inference* that the spurious state is 3 —
it is now measured, not deduced from `setne dl; add dl,2`.

### Corrections to the previous section

- Ranking client-only transitions without the paired host capture suggested a
  burst at 6625/6626/6627 and therefore a gross, repeating error. Wrong: 6625
  and 6626 occur on **both** sides. Only 6627 and 6633 are ours alone.
- One transient excursion in 6,800 ticks that self-corrects after six ticks is
  the knife-edge signature, not the gross-error one. The precision hypothesis
  is back in first place, and `FUN_0002bd80` / `FUN_001a2160` are live again.

### The fork has three gates, only one of which is float

    0x1a5142  eax = [esi+0x1b8]
    0x1a5148  test ah,1   / jne 0x1a52f9        ; gate 1 — integer flag, exits
    0x1a5151  test al,0x20 / je  0x1a515d       ; threshold select
    0x1a5155    fld dword [0x28ace8]            ;   A: constant
    0x1a515d    fld dword [eax+0x4c8]           ;   B: from tag data
    0x1a5166  fld dword [ebp-0xc]               ; the facing cosine
    0x1a5169  fcomp st(1)                       ; gate 2 — FLOAT
    0x1a516f  test ah,5   / jp  0x1a5193        ;   skip if not below
    0x1a5177  test [ecx+0x17c], 0x100000
    0x1a5181  jne 0x1a5193                      ; gate 3 — integer flag
    0x1a5183  setne dl; add dl,2 -> state 2 or 3

`setne`/`add dl,2` can only produce 2 or 3, never 0. The host wrote no
transition at all, so the original did not reach `0x1a5183` — it failed gate 1,
2 or 3. Gates 1 and 3 are integer flag tests; a wrong flag bit would normally
diverge persistently rather than for six ticks, which is why gate 2 (the
`fcomp`) remains the leading candidate. But gates 1 and 3 are now explicit
alternatives that must be ruled out rather than assumed away.

Next probe, if one is needed, should record at `0x1a5169`: the two `fcomp`
operands, plus `[esi+0x1b8]` and `[ecx+0x17c]`, which distinguishes all three
gates in one capture. Note the host probe framework already supports a value
payload — `log(at, kind, value_code, caller)` in
`artifacts/rng_trace/build_original_probes.py`.

## Causality proven: the seed streams are one stream, shifted two draws (2026-09-06)

The previous section established a *temporal* correlation -- two client-only
animation transitions at ticks 6627 and 6633, first `out of sync` at 6635 --
and inferred causality from the mechanism (a transition calls
`model_animation_choose_random`, which draws). That inference is now a
measurement.

Diffing the seed-consuming draws in the paired repro-B capture, keyed by tick:

    385 of 385 shared draw ticks (591..6633) agree on seed_before exactly
    first divergent seed value:  tick 6634
    client-only draw ticks:      6627, 6633, 6751
    host-only draw ticks:        6685

The raw sequence around the excursion shows what actually happened. These are
the same seed values on both sides, consumed at different ticks:

    tick   CLIENT                          HOST
    6626   2615001737                      2615001737
    6627   2174052948   <- extra draw      (no draw)
    6633   3147223459   <- extra draw      (no draw)
    6634   1401522854                      2174052948
    6634   3521061325                      3147223459
    6642    423647432                      1401522854

Both machines walk the identical LCG sequence. The client simply reaches each
value two draws earlier, because it burned two extra draws -- one entering the
animation state at 6627, one leaving it at 6633 -- and those are exactly the two
client-only `probe:unit_state` records. This is not "a different random
stream"; it is the same stream, phase-shifted by two.

It also answers a loose end: the state *entry* at 6627 does consume a draw
immediately. The mismatch is not logged until 6635 only because the next draw
the host performs after 6626 is at 6634.

`seed_before` carrying the packed state is likewise no longer an inference:
`tools/xbox/rng_trace_dump.py` documents kind 16 as
`seed_before = (anim_state << 8) | old_state`.

### Gate 3 and the threshold are read-only tag data -- eliminated

`FUN_001a4c50`'s prologue resolves what `[ebp-8]` is:

    0x1a4c69  push 0x62697064        ; 'bipd'
    0x1a4c6e  call 0x1ba140          ; tag_get(group, index)
    0x1a4c73  mov  edx, eax
    0x1a4c81  mov  dword ptr [ebp-8], edx

`[ebp-8]` is the **biped tag definition pointer** -- map content, byte-identical
on both machines, written once at once at load. There is exactly one write to
the slot in the whole function. Therefore:

- Gate 3, `test dword ptr [ecx+0x17c], 0x100000` with `ecx = [ebp-8]`, **cannot
  differ between the two machines. Eliminated.**
- The gate-2 threshold, `fld dword ptr [eax+0x4c8]` with `eax = [ebp-8]`, is
  also identical. So is the alternative `fld dword ptr [0x28ace8]`, a constant.
  Only the *other* `fcomp` operand -- the facing cosine at `[ebp-0xc]`, which
  ported code computes -- can differ.
- `test bl,1` at `0x1a5117` reads `[edx+0x2f4]`, tag flags from the same
  pointer. Also identical, also eliminated.

### The gate list was incomplete -- corrected from a clean boundary

Disassembling from the jump target `0x1a5061` (starting at `0x1a5130` decoded
mid-instruction and invented operands -- the same trap recorded earlier in this
document) shows more runtime gates than previously published:

    0x1a5109  al = [esi+0x42a];  cmp al,1;  je  0x1a51aa      RUNTIME
    0x1a5117  test bl,1          -> 0x1a51aa                  tag, eliminated
    0x1a5120  test al,al         -> exit 0x1a52f9             RUNTIME (+0x42a)
    0x1a5128  al = [ebp-2];      test al,al -> exit           RUNTIME (local)
    0x1a5133  eax = [esi+0x1b4]; test ah,0x40 -> exit         RUNTIME
    0x1a5142  eax = [esi+0x1b8]; test ah,1    -> exit         RUNTIME
    0x1a5151  test al,0x20       threshold select             (both operands tag)
    0x1a5169  fcomp st(1)  + test ah,5 + jp                   FLOAT
    0x1a5177  test [ecx+0x17c],0x100000                       tag, eliminated
    0x1a5183  setne dl; add dl,2 -> writes state 2 or 3

`esi` is the unit object. The surviving runtime integer gates are `+0x42a`,
`+0x1b4` bit 0x4000, `+0x1b8` bit 0x100, and the local `[ebp-2]`.

### The immediate caller is ported and writes one of the gates

`FUN_001a6350` (`src/halo/units/units.c`) is the per-tick biped dispatcher and
the direct caller of `FUN_001a4c50`. It is ported, and in the same block it

- normalizes the desired-facing vector at `+0x1d4` (`units.c:1053-1063`) -- one
  of the two vectors whose cosine gate 2 compares, and
- writes `+0x42a` from the animation state at `+0x253` (`units.c:1068-1083`) --
  a surviving runtime gate.

The x87-narrowing detector does **not** flag `FUN_001a6350`, nor `normalize3d`.
Of the fork's upstream chain only two functions are flagged:

    FUN_0002bd80  src/halo/ai/actor_moving.c   ours  4, xbe 15  (-11)
    FUN_001a2160  src/halo/units/bipeds.c      ours  1, xbe  3   (-2)

which is the ranking already recorded above, now with the caller ruled out.

### Detector fix: unported thunks were 30% of the findings

`check_x87_narrowing.py` was comparing `unported_thunks.c` entries -- JMP-only
stubs containing no FPU code at all -- against real XBE functions, so every
unported function scored "ours 0" and sorted to the top. 63 of the 207 reported
findings were this artifact. With `unported_thunks.c.obj` skipped the run is
**5375 compared, 144 MISSING-NARROWING**. Quote 144, not 207.

### The `+0x42a` gates are eliminated too -- by measurement plus a table check

`+0x42a` is written *only* by the ported `FUN_001a6350` switch, as a pure
function of the animation state at `+0x253`. Two independent facts close it.

**The switch is correct.** The XBE compiles it as a jump table:

    0x1a64b8  movsx eax, byte ptr [esi+0x253]
    0x1a64bf  cmp   eax, 7
    0x1a64c2  ja    0x1a64e4                 ; unsigned -- negatives take default
    0x1a64c4  movzx ecx, byte ptr [eax + 0x1a67a4]
    0x1a64cb  jmp   dword ptr [ecx*4 + 0x1a6798]

    index table @0x1a67a4 : [0, 2, 0, 0, 1, 1, 1, 1]
    jump targets @0x1a6798: 0x1a64db -> +0x42a = 0
                            0x1a64d2 -> +0x42a = 1
                            0x1a64e4 -> +0x42a = 2  (also the `ja` default)

So the original maps `0,2,3 -> 0`, `4..7 -> 1`, `1 and everything else -> 2`.
Our C (`units.c:1068-1083`) is `case 0,2,3 -> 0`, `case 4,5,6,7 -> 1`,
`default -> 2`, with `anim_state` declared `signed char`. State 1 falls to our
`default` and to their index-2 arm, both giving 2. **Identical, including the
negative-state case.** No bug here.

**`+0x253` was identical at 6627.** The kind-16 probe payload decodes as

    0f b7 c2                movzx eax, dx                 ; new state
    c1 e0 08                shl   eax, 8
    0f b6 8f 53 02 00 00    movzx ecx, byte ptr [edi+0x253]   ; OLD state
    09 c8                   or    eax, ecx

so the low byte of `seed_before` is `+0x253` read live at each call. Comparing
the full `(tick, unit, new, old)` tuple across the paired capture: 171 client
records, 169 host, **zero host-only**, and the two client-only records are the
excursion itself. For the excursion unit `0xe3c20037`:

    CLIENT  (6625, 21<-255)  (6626, 0<-21)  (6627, 3<-0)  (6633, 0<-3)
    HOST    (6625, 21<-255)  (6626, 0<-21)

Both machines set `+0x253 = 0` at tick 6626 and neither writes it again before
6627. State 0 maps to `+0x42a = 0` on both. Therefore at the fork:

    0x1a5109  cmp al,1   -> not taken on either machine
    0x1a5120  test al,al -> not taken on either machine

**Both `+0x42a` gates passed identically. Eliminated.**

### `[ebp-2]` traced

    0x1a4f59  al = [esi+0x257]
    0x1a4f5f  test al,al
    0x1a4f61  [ebp-2] = 0
    0x1a4f65  je 0x1a52f9        ; +0x257 == 0 -> exit
    0x1a4f6b  cmp al,5
    0x1a4f6f  [ebp-2] = 1        ; only when +0x257 == 5

`[ebp-2]` is not independent state: it is `(+0x257 == 5)`.

### Where that leaves the fork

Eliminated: gate 3, the gate-2 threshold, `test bl,1` (all tag data); both
`+0x42a` gates (measured identical). Still unaccounted for, all runtime unit
fields nobody has captured:

    +0x257                (via [ebp-2], and the 0x1a4f65 early exit)
    +0x1b4 bit 0x4000
    +0x1b8 bit 0x100
    the fcomp at 0x1a5169 -- facing cosine vs tag threshold

The float gate is now the *largest* surviving candidate rather than the only
one, and it is the only one whose input ported code computes through the FPU.

**Probe placement, corrected:** a probe at `0x1a5169` only fires if the original
reaches it, so on the host it would record nothing and name no gate. Probe the
top of the chain at `0x1a5109` instead, logging `+0x42a`, `+0x257`, `+0x1b4`,
`+0x1b8` in one payload -- it fires unconditionally on both machines and the
diff names the gate directly. A second probe at `0x1a5169` then supplies the
two `fcomp` operands when the chain is reached.

### Scope of the seed-stream proof

The wide tick-keyed comparison was `385 / 397` agreeing, where the 12
disagreements are all at ticks >= 6634 and the comparison used only the *first*
draw of each tick. The one-for-one value alignment above comes from the
6600-6645 zoom, which covers the high-volume ticks in full. The whole proof is
scoped to ticks >= 591 because the 65536-record ring had wrapped on both sides.

## Gate capture, 2026-09-06: all four remaining integer gates agree; the float does not

Both machines ran with the new probes (client `c3768a1c3`, host
`host_rng_probe.xbe` with binary probes at `0x1a5109` and `0x1a5142`). The
desync reproduced. Scoped to the desynced game:

    shared draw ticks           293  (0 .. 1596)
    agree                       282
    first divergent seed tick   1478
    transitions before 1478     client-only: (1474, 0xe33a0035, new=2 old=0)
                                host-only:   none
    gate snapshots compared     5579
    gate snapshots differing    0

**Zero.** Every time both machines reach the fork for the same unit on the same
tick, all four surviving runtime gate inputs are identical. At the causing tick:

    t=1474  CLIENT  +0x42a=0  +0x257=2  +0x1b4&0x4000=0  +0x1b8&0x100=0
    t=1474  HOST    +0x42a=0  +0x257=2  +0x1b4&0x4000=0  +0x1b8&0x100=0

So the integer gates are exonerated by measurement, not by argument. **The
`fcomp` at `0x1a5169` is the only remaining difference.**

### And the float gap is gross, not sub-ULP

The host probe recorded the compared operand. For that unit, on every tick from
1474 onward:

    probe:turn_cosine  bits=0x3f800000  = 1.0   (exactly)

The threshold is `0.99` (`0x3f7d70a4` at `0x28ace8`), i.e. the cosine of about
8.1 degrees. Working the branch:

    fld dword [ebp-0xc]     ; ST(0) = cosine
    fcomp st(1)             ; vs threshold
    fnstsw ax               ; C0 -> ah bit 0, C2 -> ah bit 2
    test ah,5               ; C0|C2
    jp 0x1a5193             ; PF=1 (C0=0, cosine > threshold) -> SKIP the turn

The host's biped was facing **exactly** where it wanted to face, so it correctly
skipped the turn-in-place animation. Our client took the transition, so our
cosine was **below 0.99** -- more than eight degrees of facing error against the
original's zero.

**This retires the sub-ULP precision hypothesis for this site.** A last-bit
rounding difference cannot move a cosine from 1.0 to below 0.99. Our biped's
current facing (`+0x24`) and desired facing (`+0x1d4`) genuinely diverge, by a
visible angle, where the original holds them identical. The x87-narrowing lane
is not the explanation here; something in the ported facing update is wrong by
a wide margin, and the knife-edge reading of the six-tick excursion was the
wrong model.

Two caveats on this run:

- The threshold is selected by `test al,0x20` on `+0x1b8` (`0x1a5151`). Bit 0x20
  set uses the `0.99` constant; clear uses the tag value at `+0x4c8`. The gate
  probe records bit 0x100, not bit 0x20, so which threshold applied is not yet
  captured. It does not change the conclusion: the host was at exactly 1.0 and
  did not fire.
- Only the host records `probe:turn_cosine`. `[ebp-0xc]` lives inside the
  unported fork, so the client needs its own binary probe, or an equivalent
  value computed in the ported caller, to state our cosine as a number rather
  than as an inequality.

Note the transition state was **2** this run and **3** in repro B. `setne dl;
add dl,2` selects on `[ebp-1]`, the turn direction, so that difference is which
way the biped turned, not a different fault.

## RETRACTED: "our build desyncs against itself"

**Struck 2026-09-06, same day.** The user later reported the opposite: the same
patched build on both machines does **not** desync. The section below is kept
for the record but its conclusion is wrong.

Most likely reconciliation, consistent with every observation: the earlier
patched-against-patched test ran two patched builds at *different revisions*.
Different code desyncs. Identical code does not.

    pristine    vs pristine    -> no desync
    patched X   vs patched X   -> no desync   (measured, clean-run baseline below)
    patched     vs pristine    -> desync      (all captures in this document)
    patched X   vs patched Y   -> desync      (the misread test)

So the simulation is deterministic and the original framing holds: our code
computes a different value than the original. The non-determinism hypotheses
below (uninitialized stack, pointer values, timing) are not supported and are
not being pursued. The clean-run baseline that follows this section is still
valid and still useful -- it shows the probes agree bit for bit when both
machines run identical code, which is the control the instrument needed.

## Superseded reframe: our build desyncs against ITSELF

User report, and it changes the target of the whole investigation:

- pristine `cachebeta` against pristine `cachebeta` -- **no desync**
- our patched build against our patched build -- **desyncs, either machine hosting**
- our patched build against pristine -- desyncs (all captures above)

Two *identical* binaries cannot diverge in a lockstep simulation unless the
simulation is non-deterministic. So the framing used up to this point -- "our
code computes a different value than the original" -- was wrong, or at least
incomplete. The defect is that our code computes a different value **than
another copy of itself**.

That narrows the mechanism class sharply. A deterministic difference from the
original would reproduce identically on both of our machines and could not
desync them against each other. What can differ between two machines running
the same image:

1. **A read of uninitialized stack memory.** The two machines run different
   non-simulation code between ticks (rendering, audio, input, network), so
   stale stack contents differ. This is the classic cause.
2. **A read of uninitialized pool or heap memory** -- a struct field the
   original initializes and we do not. A `pad_` field that turns out to be read
   is exactly this bug.
3. **A pointer value used in arithmetic.** Addresses need not match.
4. **Dependence on wall-clock or frame timing rather than tick count.**

The facing evidence still stands and becomes more useful: with both machines
running our build, both log the reconstructed cosine (kind 21) and the forward
z (kind 22) from source. No binary patch is needed, and any difference between
the two is by definition our own non-determinism.

### Uninitialized-read sweep: 51 warnings, top candidates are false positives

    clang -Wconditional-uninitialized -Wuninitialized   (full tree, gnu90)
    -> 51 warnings; artifacts/scratch/uninit.txt

Concentrations: `encounters.c` 9, `units.c` 7, `objects.c` 7,
`breakable_surfaces.c` 6, `model_animations.c` 3.

The animation-path hits looked promising and are **not** bugs. Both
`units.c:247/277/298` (`has_rotation`, `has_translation`, `has_scale`) and
`model_animations.c:1385/1404/1429` (`local_14`, `local_1c`, `local_20`) load
inside `if ((node_idx & 0x1f) == 0)` in a loop whose index starts at zero, so
the first iteration always initializes them. clang cannot prove the loop runs
at least once with index 0. The remaining 45 are unreviewed.

This sweep is worth keeping as a standing check, but it did not find the fault.

### Clean-run baseline, and the `f.z` hypothesis is refuted

Both machines on our build, one full game, no desync. Scoped to that game:

    tick range              2417 .. 4589 on both
    seed ticks compared     418, all agreeing
    probe:turn_cos_c        7454 compared, 0 differing
    probe:turn_fwd_z        7454 compared, 0 differing
    probe:turn_gates        7454 compared, 0 differing

So when the game does not desync, the two machines agree bit for bit on every
value this fork reads. The instrument is sound and any difference in a
desyncing run is real.

**`f.z` is always exactly zero in our build** -- 7454 of 7454 samples, min and
max both 0. The hypothesis that a non-zero forward z was dragging the 2D dot
below the threshold is therefore **wrong**. Our cosine falls below 0.99 because
the biped genuinely is turning in the XY plane, which is the normal case: 1292
of 7454 samples sit below the threshold in an ordinary game.

What remains is unchanged: get a desyncing run with these probes on both
machines. The first differing `probe:turn_cos_c` names the tick and unit, and
from there the question is which input to the facing update went wrong.

## The caller-side cosine reconstruction is INVALID: the fork updates `+0x24` first

Kind 21 samples `+0x1d4` and `+0x24` in the ported caller, immediately before
`FUN_001a4c50`. That is not equivalent to what the fork compares, because the
fork **writes the current facing in place** before computing the cosine:

    0x1a4dd5  lea ecx, [esi + 0x24]          ; ecx = current facing
    ...                                       ; (no reassignment of ecx)
    0x1a4f08  mov eax, [ebp-0x20]
    0x1a4f0b  mov edx, [ebp-0x1c]
    0x1a4f0e  mov [ecx],   eax               ; <-- turns the biped
    0x1a4f13  mov [ecx+4], edx
    0x1a4f16  mov [ecx+8], eax
    ...
    0x1a5061  lea eax, [esi + 0x1d4]         ; only now is the cosine built
    0x1a50cd  fstp dword ptr [ebp-0xc]

So kind 21 reads the facing one update too early. It is correct only for a
biped that is not turning, where the write is a no-op.

The paired capture shows exactly that signature, and it is the reason the
control failed:

    unit 0xe2710002   128 samples   128 identical   every value exactly 1.0
    unit 0xe2740005   128 samples   128 identical   every value exactly 1.0
    unit 0xe27a000b   128 samples   128 identical   every value exactly 1.0
    unit 0xe2770008   128 samples     0 identical   the only unit that moves

**All 384 agreeing samples are the constant 1.0 from stationary bipeds.** This
is the vacuous-agreement trap: a control that passes only where the quantity
under test is constant proves nothing. The one moving unit disagreed on every
sample, and that disagreement is the probe's error, not a simulation
divergence. No conclusion about our simulation can be drawn from this capture.

Kinds 23 to 27 (the raw components) have the same defect and are equally
invalid; they sample the same pre-update values.

**Correct fix:** the client needs the same *binary* probe the host has, at
`0x1a5142`, reading `[ebp-0xc]`. `FUN_001a4c50` is unported in our build too,
so the identical patch applies at the identical address. The obstacle is cave
space: `build_original_probes.py` hides its caves inside
`unit_update_animation`'s body, which is dead in the baseline build but live in
ours. Our build needs a dedicated reserved buffer instead.

## MEASURED 2026-09-06: our desired facing never leaves the current facing

First capture with a binary probe on BOTH machines at the same instruction
(client `tools/xbox/patch_fork_probes.py`, host
`artifacts/rng_trace/build_original_probes.py`), kind 20 = `[ebp-0xc]` at
0x1a5142, the operand of the fork's `fcomp`.

    client: 2 distinct cosine values in the whole game
              0x3f800000 (1.0)          384 samples
              0x3f7fffff (0.99999994)   128 samples
    host:  19 distinct values, a real sweep -0.986 .. +0.998 .. -0.99

    shared (tick,unit) cosine keys 511, DIFFERING 127
    unit 0xe2770008, ticks 2..22: client 1.0 flat, host swings through a
    full turn (-0.986 -> +0.998 -> +0.924)

Each of the four units is pinned to ONE bit-exact value for all 130 ticks.
A cosine that never moves off 1.0 by even an ulp means the fork is dotting a
vector with itself.

The gates are NOT the difference. Same capture, same probe pair:

    shared gate keys 520, DIFFERING 2 (t=1 and t=5, one unit, one-tick phase)
    every sample: +0x257=2, +0x1b4&0x4000=0, +0x1b8&0x100=0

### Why 1.0 is the self-dot signature

Disassembly of the fork at 0x1a5061..0x1a50cd:

    0x1a5061 copy (+0x1d4,+0x1d8,+0x1dc) to [ebp-0x20], force z = 0
    0x1a5083 call 0x12f10 (normalize3d), returns length in ST0
    0x1a5088 fcomp [0x2533c0] ; test ah,0x44 ; jp 0x1a50ac
             -> length == that constant falls through to the FALLBACK
    0x1a5098 fallback: copy the CURRENT facing (+0x24) over [ebp-0x20]
    0x1a50ac cross-z  = d.x*f.y - d.y*f.x            -> [ebp-1] turn direction
    0x1a50bc cosine   = d.x*f.x + d.y*f.y            -> [ebp-0xc] COMPARED
    0x1a50e4 fcomp [0x2568c0] ; test ah,5 ; jp 0x1a5109

With d = f the cosine is f.x^2 + f.y^2, which is 1.0 for a normalized facing
with f.z = 0 (and 0x3f7fffff for one that is an ulp short). So on our build the
desired facing either normalizes to zero, or already equals the current facing.

### The producer chain, traced to one field

Every instruction in .text that references offset 0x1d4 was decoded (50 sites)
and mapped to its kb.json function. The per-tick writer is `unit_set_control`
(0x1af990, ported), at 0x1afcfb:

    unit+0x1d4 <- control+0x1c        (facing_vector)
    unit+0x1e0 <- control+0x28        (aiming_vector)
    unit+0x204 <- control+0x34        (looking_vector)

Our C at units.c:10273 matches the original store-for-store. The fault is
upstream of it. Two producers fill that control block:

    AI     actors.c:7652   control+0x1c <- actor->output_facing_vector (+0x718)
    player players.c:3074  control+0x1c <- unit+0x1d4 (a self-copy, input off)

and `output_facing_vector` has exactly one writer, actor_looking.c:9303, which
stores `actor->control_desired_facing_vector` (+0x5a4). That field is written
by actor_moving.c in three places:

    3686  = actor->input_facing_vector (+0x174)   <- THE DEFAULT, self-facing
    3837  = normalized (actor+0x12c - actor+0x6a8), guarded by normalize3d != 0
    3924  = -vec_scratch, vehicle-stuck arm

Line 3686 is the default assignment at the top of the function: desired facing
:= input facing. Our measurement is exactly what that default produces if no
later branch overwrites it. `FUN_0002bd80` (actor_moving) was already suspect
number 1 from the x87-narrowing ranking, reached independently.

### Open, and the next measurement

Which link breaks is NOT yet measured. Three candidates, in order:

1. actor_moving never leaves the 3686 default (a branch condition is wrong).
2. actor_look_update overwrites +0x5a4 or fails to propagate it.
3. the units are player bipeds, not actors, and players.c takes the
   input-disabled arm at 3063 -- which self-copies the facing and would also
   pin the cosine. Settle this first: it changes which file to read.

A CLIENT-ONLY probe answers 1 and 2 -- no host run needed, because the host's
behaviour is already measured. Record, per tick and unit: actor+0x5a4,
actor+0x174, actor+0x718, and unit+0x1d4. If +0x5a4 == +0x174 always, the break
is in actor_moving. If +0x5a4 moves but unit+0x1d4 does not, it is downstream.

Captures: artifacts/rng_trace/cos_{c,h}.json, dbg2{1,4}_cos.txt.
Scripts: artifacts/scratch/{cos_cmp,gate_cmp,find_1d4}.py.
