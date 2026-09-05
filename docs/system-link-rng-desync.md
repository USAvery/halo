# System-link lockstep desync: client/server random seed mismatch

Status: **OPEN** (2026-09-05). A float-association root cause was proposed and
then **REFUTED** by measurement -- see "Refuted: float addend association"
below. The desync mechanism is unknown again.

## Symptom

A system-link game between a pristine build-2276 host (`cachebeta.xbe`,
xemu at 10.0.0.24) and our re-implemented client (`halo-patched/default.xbe`,
xemu at 10.0.0.21) desyncs within the first few seconds of gameplay:

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

## What has been ruled out

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
- `unit_animation_set_state` (0x1ad260, VC71 76.8%): weapon-idle draw guard
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
- Sub-90% VC71 on the path: `unit_animation_set_state` 76.8,
  `FUN_000f7e60` 72.2, `FUN_000f9c40` 89.1, `FUN_001a6350` 89.9,
  `FUN_001a6280` 86.4.
