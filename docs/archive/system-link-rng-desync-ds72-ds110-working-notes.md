# Archived ds72-ds110 working notes — not a resume point

> Archived on 2026-09-11 to preserve detailed capture descriptions. This is an
> unreconciled working document: it contains superseded instructions, stale
> deployment statements, and stronger causal claims than the evidence supports.
> Do not follow its "current" or "next" steps. The authoritative corrected
> record is [../system-link-rng-desync.md](../system-link-rng-desync.md).

## Original working-document contents

Status: **OPEN** as of 2026-09-11. Several real floating-point fidelity defects
have been corrected, but the corrections were insufficient to stop the system-link
desync. The complete initiating defect set has not been identified.

This file is the authoritative handoff for the investigation. The earlier
committed chronological notebook is preserved as a clearly non-authoritative
[`archived investigation log`](archive/system-link-rng-desync-investigation-log.md).
It covers the history before the ds72--ds92 continuation, contains superseded
claims, and must not be used as the resume point. The decisive later evidence
is consolidated below. Saved captures under `artifacts/rng_trace/` remain the
primary evidence.

## Current conclusion

The RNG stream is not the initiating fault. The peers first acquire different
world-space object state. Grenade placement and later collision/damage queries
amplify that state difference into different branches and therefore different
numbers or callers of RNG draws.

The strongest localization remains the combined ds93--ds103 evidence:

- Ds93 localized an early one-ULP player-position difference to the
  `FUN_001a2f40` output consumed by original caller `FUN_001a5300`.
- Ds94 showed that using the pristine `FUN_001a2f40` body removed that early
  player-translation difference through the retained tick-97 window.
- Ds99--ds101 found and corrected two genuine x87 narrowing-lifetime defects:
  grenade throw inverse-ratio rounding and biped Z-delta rounding.
- Ds101 showed bit-identical grenade-placement ray inputs but different
  collision results, with the client hitting player `e2770008` and the host
  reporting no collision.
- Ds102 removed another proven extra narrowing in biped planar scaling. Its
  watcher paused on a one-ULP player difference, but the user confirmed that
  the game had not actually desynchronized; this is not a valid failure
  signal.
- Ds103 routed `FUN_001a2f40` to the pristine body. The user then observed a
  fast actual desync. Therefore that body is implicated in one early drift
  path but is not the sole system-link defect.

Therefore RNG, grenade placement, and the earlier node-matrix localization are
amplification boundaries. The investigation no longer has evidence for a
single faulty function. Resume with coarse-to-fine ported-function bisection
using actual game desynchronization as the pass/fail signal; do not resume with
another one-off floating-point probe.

## Test topology and current deployment

- Patched client guest: `10.0.0.21`; HMP: `127.0.0.1:4444`.
- Pristine cachebeta host guest: `10.0.0.25`; HMP:
  `127.0.0.1:4446`.
- Both xemu instances use bridged networking.
- XBDM/RDCP access to the guest IPs works from WSL with
  `HALO_WINDOWS_REEXEC=1`.
- Guest launch path on both instances:
  `E:\GAMES\halo-patched\default.xbe`.

Known failing ds103 baseline (later bisection deployments are recorded below):

Deployed artifacts:

- Client: `artifacts/rng_trace/ds103_biped_original_fallback_client.xbe`
  - SHA-256:
    `d1e31bf00254f783c925c31c41b15048c458b18801258f79a08817f4f43ca91c`
  - Symbols: `artifacts/rng_trace/ds103_biped_original_fallback_symbols.pe`
  - Symbol PE SHA-256:
    `2f3eedd1f7816a436c2b3d54ea3319df4042cbe602495e0a66b123662adb0d64`
- Host: `artifacts/rng_trace/host_rng_probe_focused.xbe`
  - SHA-256:
    `0dbb944b2a37e82ac3eeaf910cad68a3eed6ae123f31672b39944bfa6ad2669f`
  - Builder: `artifacts/rng_trace/build_original_probe_focused.py`

The host builder was edited after that artifact was produced; its current
contents do not establish the existing artifact's provenance. Do not rebuild
the host as an incidental part of client bisection. Ds103 was built with the
biped fallback, but a live fallback check after the failed match was unavailable
because XBDM had become unreachable. Preserve that qualification.

`FUN_001a2f40` is currently `ported:false` in `kb.json`. This is an
allowlisted diagnostic fallback, not a complete fix. The four ds91 diagnostic
deactivations remain restored and `ported:true`:

- `object_compute_node_matrices` (`0x141b70`)
- `FUN_001093b0` (`0x1093b0`)
- `FUN_00109500` (`0x109500`)
- `matrix4x3_multiply` (`0x109850`)

## Proven causal chain

The following ordering is supported by retained paired traces:

1. A small world-position difference exists before grenade placement.
2. The grenade placement query sees different biped collision geometry.
3. One peer can accept an object hit while the other reports no hit.
4. The accepted hit changes the grenade position or later trajectory.
5. Damage-radius and line-of-sight queries then select different victims.
6. Different damage/animation branches consume a different RNG caller
   sequence.

The final RNG mismatch is consequently a symptom. A short LCG distance or a
different `model_animation_choose_random` call count does not locate the first
bad writer.

## Decisive captures

### A/A control: ds69

Both peers ran the same pristine cachebeta-derived probe build. Event alignment
found no true RNG/caller-sequence divergence. The earlier watcher alert came
from comparing snapshots taken at different ticks; one ring merely contained
one later event.

The retained analysis counted 1,187 aligned RNG/caller events and 16,296 shared
throw, sweep, damage, radius, and LOS probe records without a value difference.
`ds69_pristine_probe_aa_comparison.json` records no first difference.

This proves that the large mixed-build throw/sweep differences are not an
ordinary host/client-role offset in the exercised control. It does not prove
that every near-boundary collision must agree under slightly different inputs.

### Release-to-sweep boundary: ds77

The final grenade target at tick 177 was nearly identical:

- client: `(-2.6665967, 2.1356857, 3.8124990)`
- host: `(-2.6665967, 2.1356859, 3.8124990)`

At the first projectile sweep later in that tick, XY position was separated by
approximately `(-0.0213541, -0.0308695)`, magnitude `0.0375356`, while Z and
velocity agreed. The translation persisted through the observed sweeps and
collision responses. The later RNG split followed a different damage-victim
sequence.

This localized the visible grenade displacement to placement before normal
projectile motion, but did not yet locate the earlier player-state difference.

### Placement outcome: ds78 and ds79

In ds78, both peers entered `object_try_place` with bit-identical grenade
position and requested target at tick 213:

- position: `(-5.4714866, -6.2572231, 2.0124991)`
- target: `(-5.4236932, -6.2425332, 2.0124991)`

The host kept the original position. The client moved the grenade to
`(-5.4356112, -6.2461963, 2.0124991)`. Both returned success.

Ds79 identified the branch taken by the client:

- collision type: `3` (object)
- collided datum: `e2770008` (biped/player)
- surface: `21`
- fraction: `0.2493643761`

The host reported type `-1`, fraction `1.0`, and no hit. The client output is
exactly the point at the reported fraction along the placement segment.

These runs prove that the placement displacement is a consequence of a
client-only biped/object collision. They do **not** prove that
`object_try_place` or its collision algorithm is intrinsically wrong: later
captures show that the biped world state supplied to the query can already be
different.

### Collision-path body bisections: ds81 through ds83

The client-only object hit persisted while the following bodies were restored
progressively to pristine code:

- `FUN_0014dce0`
- `FUN_0014cb00`
- `collision_bsp_test_vector`

These results exclude those bodies as sole causes under the failing rays. They
are not blanket exclusions of their patched callees or of the state supplied
to them.

### Matrix-helper boundary: ds85 and ds86

Static comparison found a real expression-order mismatch in
`matrix_transform_point` (`0x109590`): the pristine x87 body forms each
component in `z + y + x` product order, while the lift used `x + y + z`. The
source was corrected and reached 100% VC71 instruction match. Ds85 still
reproduced the client-only collision, so this valid correction is not the
runtime cause being sought.

Ds86 compared the object-local transform boundary. Whenever a source node
matrix matched, `matrix_inverse`, `matrix_transform_point`,
`matrix_scale_transform_vector`, and the transformed ray matched bit-for-bit.
The differing queries instead received different source matrices.

### Pose and hierarchy localization: ds88 and ds89

Ds88 showed identical raw quaternion, position, and scale words for every
collision-visible pose node, while final matrices differed on the descendant
chain `7 -> 13 -> 15 -> 17`. Node 7 is a hidden ancestor not present in the
collision-region list.

Ds89 added node 7. Its eight raw pose words and XOR matrix fingerprint matched;
node 13 and descendants differed. Because XOR can cancel paired word changes,
this was not lossless proof that every node-7 matrix word matched.

Targeted Unicorn differentials provide supporting, scoped evidence:

- `FUN_00109500`: 1,000/1,000 cases passed, 100% instruction coverage.
- `matrix4x3_multiply`: 1,000/1,000 cases passed, 100% instruction coverage.

The corpus did not specifically prove the live in-place
`matrix4x3_multiply(parent, child, child)` case. More importantly, ds91 later
showed that the player world state was already different before placement, so
this hierarchy branch is no longer treated as the initiating boundary.

### Pristine node-matrix chain: ds90 and ds91

Ds90 restored only `object_compute_node_matrices`. The user reproduced the
desync, but the relevant grenade records had wrapped out. Ds90 is only an
operational persistence result. Its pristine caller still reached redirected
ported math callees, so it did not test the full chain.

Ds91 restored the producer and all three direct math bodies:

- `object_compute_node_matrices`
- `FUN_001093b0`
- `FUN_00109500`
- `matrix4x3_multiply`

The user reproduced the desync and the relevant placement survived in both
rings at tick 383.

Client:

- query start XY: `(0x3f2d7ba3, 0xc08f5482)`
- target XY: `(0x3f374325, 0xc0905cc9)`
- result: type `3`, biped `e2770008`, surface `21`
- fraction: `0x3e7f59cf`

Host:

- query start XY: `(0x3f2d7bbc, 0xc08f5480)`
- target XY: `(0x3f37433e, 0xc0905cc7)`
- result: type `-1`, no collision
- fraction: `0x3f800000`

The throw-release trace already contained the target discrepancy:

- client thrower/seat XY: `(0x3f374325, 0xc0905cc9)`
- host thrower/seat XY: `(0x3f37433e, 0xc0905cc7)`
- thrower Z and seat Z: bit-identical

Every sampled local matrix hash, including node 7, also differed slightly in
this run. This is consistent with matrices being downstream of differing
object/world state.

Over the retained shared interval, all 158 animation-update input/output pairs
and all 158 turn-gate records for `e2770008` matched. Desired X, current X, and
unit flags exist only in the client source trace; turn cosine exists only in
the host binary trace, so those fields are not symmetric ds91 evidence.

Ds91 proves that the complete node-matrix body/math chain is not the initiating
cause in this reproduction. It also changes the interpretation of ds78–ds89:
the collision path is where a small earlier state difference becomes a large
behavior difference, not necessarily where the earlier state difference is
created.

## Completed experiment: ds92 object-position writer

Probe kinds 83 and 84 record the requested XYZ and object handle at
`object_translate` entry on both builds.

Client instrumentation is at the lifted function entry. The pristine host
detour is at `0x143bf1`, after EDI contains the object handle and before
position validation or any object-position write. It replays these stolen
instructions before returning to the original body:

```text
MOV ESI,[EBP+0xc]
PUSH ESI
MOV EBX,EAX
```

The generated detour was disassembled after construction. It preserves flags,
general registers, EAX's original object pointer, and the stolen instruction
sequence.

Interpretation:

- If the first comparable `object_translate` request for `e2770008` already
  differs, the bad value is produced upstream in biped movement/collision
  integration or network-state application.
- If the request matches but later object/throw state differs, instrument the
  function exit and `object_connect_to_map` boundary before changing logic.
- Do not infer a writer from a later grenade collision after the values have
  already separated.

Kinds 83–84 add only two records per translation, but the client and host
builds still contain the earlier high-volume local-ray probes. Pause and dump
immediately after reproduction to avoid another wrapped placement event.

### Ds92 result

The paired rings were captured as `ds92_object_translate_client.json` and
`ds92_object_translate_host.json`. They contain 524 client and 780 host
`object_translate` calls over tick ranges 22044–22174 and 21997–22191.
All 524 calls sharing `(tick, object handle, occurrence)` matched bit-for-bit
in requested XYZ. This includes 131 shared calls for each of `e2710002`,
`e2740005`, `e2770008`, and `e27a000b`.

This excludes a differing `object_translate` input request over the retained
shared interval. It does not yet prove that the stored object position matches
after the write or after `object_connect_to_map`. The earlier high-volume
local-ray probes filled the ring, and no grenade-placement records survived in
the ds92 captures; do not claim that the decisive placement tick itself was
retained.

## ds93 result: the translation boundary preserves the difference

Kinds 85–88 record object XYZ immediately after the stores and immediately
after `object_connect_to_map` returns. The pristine detour replaces the call at
`0x143c6a`, replays that call, and samples the same object pointer before and
after it. Generated detour disassembly was checked after construction.

The valid ds93 capture contains 3,249 client and 2,182 host translation calls.
Every call on each peer preserved XYZ exactly from entry through the stores and
through `object_connect_to_map`; this excludes both operations as the source of
the coordinate difference.

The first non-initial shared mismatch is player `e2770008` at tick 45. The X
coordinate differs by one ULP (`0xbed6d163` client versus `0xbed6d164` host),
while Y and Z match. Tick 44 matches exactly, and the differing value is already
present at `object_translate` entry. The caller is original `FUN_001a5300` at
return address `0x1a5ec5`; its requested position comes from the `+0xac`
`new_position` output of `FUN_001a2f40`.

At tick 286, grenade `e29b002b` reaches `object_try_place` with slightly
different input/target coordinates. The client then reports a type-3 collision
with player `e2770008`, while the host reports no collision. This is an
amplification of the earlier player-position difference, not its source.

## Completed experiments: ds94 through ds103

### Ds94: biped-body isolation

Restoring only `FUN_001a2f40` made early player translations exact through tick
97. The live bytes at `0x1a2f40` matched the pristine prologue
`55 8b ec b8 ac af 00 00 e8 93 61 03 00 33 c0 66`; both trace rings had valid
`RNGT` magic. `FUN_0014f2c0` remained patched.

This implicates the lifted biped body in one early numerical drift path. It
does not establish that the body causes every actual system-link failure.

### Ds99 through ds101: required float stores

In `unit_throw_grenade_release`, the original stores and reloads
`1.0f - ratio_val` as float. The source now forces that boundary:

```c
ratio_val = 1.0f - ratio_val;
HALO_FLT_ROUNDTRIP(ratio_val);
```

Ds100 localized a one-ULP player Z difference to a missing float store between
the subtraction and addition in `FUN_001a2f40`. The source now follows the
original `FSTP dword` at `0x1a366d`:

```c
z_delta = disp[2] - fdist;
HALO_FLT_ROUNDTRIP(z_delta);
physics[0x30] = z_delta + physics[0xd];
```

These are binary-backed fidelity corrections, not a complete desync fix.

At ds101 tick 129 all four player translations and the first grenade placement
occurrence were exact. Identical `object_try_place` start/target coordinates
still yielded a client hit on player `e2770008`, surface 21, type 3, while the
host reported no collision. Matching translations and ray inputs do not prove
that all hidden collision/world state matches.

### Ds102: excess planar narrowing and an invalid failure oracle

The original retains the wide x87 planar scale for both multiplies. The source
now uses an `x87_wide_t planar_scale`:

```c
planar_scale = *(double *)0x2573d8 / sqrtf(r);
gy = gy * planar_scale;
gx = planar_scale * gx;
```

The translation watcher paused on different words (`bf818bd5` client,
`bf818b9d` host). The user confirmed that the game had not actually
desynchronized. Treat this as numerical drift only; the recorded words are
56 representable float steps apart, despite the earlier one-ULP description.

Client artifact: `artifacts/rng_trace/ds102_biped_planar_scale_client.xbe`,
SHA-256 `a2a57ea30b139691ae2261e199b08737a3365a54c65e9b9287c5e0f3ab2b2440`.
Matching symbols SHA-256:
`64673d4c1218c064bcfa248fa8f60d5850449ffe269db69a97eb29e210d297f1`.
Its client ring VA was `0x80dd54`; do not reuse that address for another build.

### Ds103: biped fallback still visibly desynchronizes

The client artifact listed above restores `FUN_001a2f40` to the original body.
The user observed a fast actual desync. The biped body is therefore not the
sole cause, and leaving it deactivated is a diagnostic configuration only.
Post-match live verification was unavailable; artifact configuration and user
observation are the retained evidence.

## What is proven, inferred, and unknown

### Proven

- A true mixed-build caller-sequence RNG divergence occurs after different
  collision/damage behavior.
- The A/A watcher alert in ds69 was tick skew, not a sequence divergence.
- A client-only biped collision can displace the grenade during placement.
- Matching source matrices produce matching local transforms and rays.
- The ds91 thrower/seat XY values differ before grenade collision.
- Restoring the full node-matrix producer/math chain does not remove the ds91
  reproduction.
- `matrix_transform_point` had a real ordering mismatch; fixing it did not
  remove the runtime problem.
- Ds93 translation entry, stores, and map connection preserve the differing
  input exactly; those coordinate stores did not initiate that drift.
- Ds94 removes an early drift window, but ds103 still visibly desynchronizes
  with the biped fallback configured in the artifact.
- Ds101 can disagree on collision despite exact placement-ray inputs.
- Ds102's watcher pause was not an actual game desync.

### Inferred

- The client-only collision is a sensitive amplification point for a small
  player/object position difference.
- Damage is an effective trigger because it changes victim selection and RNG
  call counts after trajectory/collision state has separated.
- Multiple independent simulation defects or interacting lifts may remain.
  Test complementary bisection sets if results are non-monotonic.

### Unknown

- The minimal active lift set responsible for actual system-link failure.
- Which hidden state explains the ds101 collision result, and whether that
  discrepancy is sufficient to cause a later real desync.
- Whether grenades are the only practical trigger. The retained evidence does
  not support excluding non-combat triggers globally.
- Whether the ds103 runtime contained exactly the intended artifact; the
  post-match live check could not complete.

## Retired claims and dead ends

Do not resume any of these as established conclusions:

- **“RNG is corrupt or serialized incorrectly.”** No retained evidence supports
  this. Caller-count divergence explains the seed separation.
- **“The animation chooser is the root cause.”** Its extra/missing draws are
  downstream of already-different collision or state.
- **“The issue reproduces without combat, so projectile handling is excluded.”**
  Earlier observations were not strong enough to support that exclusion.
- **“`object_try_place` itself is proven wrong.”** It receives different biped
  collision state; its displacement is proven, its intrinsic guilt is not.
- **“`FUN_0014dce0`, `FUN_0014cb00`, or
  `collision_bsp_test_vector` is the root cause.”** Pristine-body tests did not
  eliminate the result.
- **“The `7 -> 13 -> 15 -> 17` hierarchy branch is the first bad writer.”**
  Ds91 moved the initiating boundary upstream to object/world state.
- **“Ds90 proves the full node chain innocent.”** It did not restore redirected
  callees and its placement event wrapped out.
- **“A watcher pause proves desync.”** Snapshot tick skew caused a false alert
  in the A/A control, and ds102 paused on numerical drift before actual game
  desynchronization. Aligned traces describe drift; runtime verdicts require
  actual game failure.
- **“The biped body is the sole root cause.”** Ds103 still visibly failed with
  its fallback configured. Its deactivation is not a fix.
- **“A higher VC71 score proves a runtime fix.”** Static scores are supporting
  evidence only. Further score chasing on these paths is not the current plan.

## Resume procedure: coarse-to-fine simulation bisection

1. Preserve the dirty worktree and the ds103 baseline. Establish a new A/A
   control only if the emulator topology or the control binaries changed.
2. Enumerate active simulation-related ports and record the candidate set.
   Group related bodies and account for register-argument fallback thunks.
3. Build a broad fallback control and complementary half-set variants using
   `patch.py --kb-overlay` where possible, keeping `kb.json` unchanged.
4. Save each exact XBE, matching PE, overlay, and SHA-256 manifest. Verify
   original-entry bytes and implementation fallback targets, including ABI
   adapters, before deployment. A build exit status alone is insufficient.
5. Deploy through WSL and require successful upload and `magicboot` responses.
   Verify live toggles against that variant before recording a verdict.
6. Classify by visible/game-ending desynchronization or a fresh game-reported
   out-of-sync error. Record the exercise and duration for a no-failure run;
   a short quiet interval is not proof of a fix.
7. Halve the implicated set. Test complements when independent defects or
   interactions could make the results non-monotonic.
8. Inspect assembly only after narrowing the culprit set, implement proven
   corrections, then repeat A/A and patched-versus-pristine validation.

Do not run the translation watcher as a desync oracle or poll HMP during play.
Do not ask for another reproduction until a named, hashed bisection build is
deployed and its distinguishing question is stated. Existing traces remain
supporting evidence; add no one-off float probes before narrowing the set.

## Deployment procedure for bridged xemu

Build the complete traced XBE, not only the PE target:

```powershell
rtk wsl.exe bash -lc 'cd /mnt/g/dev/halo && rtk python3 tools/build/build.py -q --rng-trace'
```

Immediately save both the produced XBE and its matching `build/halo` symbol PE
under unique artifact names. Upload from WSL with
`HALO_WINDOWS_REEXEC=1`; direct Windows access is not assumed for bridged guest
IPs.

Example client upload:

```powershell
rtk wsl.exe bash -lc 'cd /mnt/g/dev/halo && HALO_WINDOWS_REEXEC=1 rtk python3 tools/xbox/xbdm_rdcp.py --host 10.0.0.21 --sendfile artifacts/rng_trace/ds103_biped_original_fallback_client.xbe "E:\GAMES\halo-patched\default.xbe"'
```

Launch with:

```text
magicboot title=E:\GAMES\halo-patched\default.xbe debug
```

Do not deploy or restore an HDD `init.txt`; this investigation uses XBE-only
deployment.

The normal build has left `halo-patched/default.xbe` stale before. Hash the
exact file being uploaded, verify its patch configuration, and invoke the patch
stage explicitly if required. For a bisection variant, substitute its recorded
artifact path in the example. Require both upload and launch acknowledgements.

## Prepared bisection variants: ds104 through ds106

The frozen ds103 XBE and symbol PE hashes were verified against the values
above. `artifacts/rng_trace/system_link_bisect/build_variants.py` derives these
variants directly from that XBE using the production patcher's
`generate_deactivation_redirect`. This avoids recompiling the dirty source or
changing the register baseline, `kb.json`, debugger files, or host artifact.
The generated overlays record equivalent port selections for the normal patch
stage; the frozen artifact is the authoritative binary for these runs.

The first candidate set contains **3,515 active exported redirects across 71
simulation-related object groups**. Groups are kept together and balanced by
function count. This is a broad semantic selection, not proof that all members
execute in multiplayer. Rendering, audio, UI, platform infrastructure and other
excluded active ports remain. All variants retain ds103's existing
`FUN_001a2f40` fallback.

| Variant | Additional ports deactivated | Candidate ports retained | XBE SHA-256 |
| --- | ---: | ---: | --- |
| ds104_simulation_off | 3,515 | 0 | `be74821fb3f1364b431a8eddd361e78d0a4df9499ae2d550c624b891455a45d5` |
| ds105_half_a_off | 1,758 | 1,757 | `ef624a21cdf3f18a8cf3858cc10c4d6f20323c85ac2840b7c205968b47b8ddb0` |
| ds106_half_b_off | 1,757 | 1,758 | `c66acbaad25c8a14cd9e5ddbce3b7cd96eeacc6a7d66cf6b79719406738db084` |

Artifacts and evidence are under `artifacts/rng_trace/system_link_bisect/`:

- `manifest.json`: every candidate, excluded active port, group, variant and
  overlay hash; runtime verdicts initially `UNTESTED`.
- `<variant>.xbe` and `<variant>.overlay.json`: fixed binaries and selections.
- `verify_live.py`: one-shot WSL XBDM comparison against the exact selected
  artifact. It checks all candidate original entries and implementation
  fallbacks, twelve retained active controls, and the fixed biped entry. Run
  before play; it does not poll during reproduction or pause either guest.

The builder checked all 3,515 candidate entries and implementation prefixes in
each output. It also verified the existing biped fallback at both addresses,
stub space before the next exported implementation, and that no bytes outside
the selected patches and section digests changed. The production patcher's
reverse/fallback thunk self-tests passed, including generation for 881 current
register-argument functions.

Ds104 is the broad fallback control. If it still genuinely desynchronizes,
expand or revise the candidate scope before halving. If the same exercise no
longer fails, test ds105 and ds106 to distinguish the halves; neither a short
quiet interval nor a float watcher pause settles the result. Fallback behavior
and interactions remain possible confounders if outcomes are non-monotonic.

### Ds104 deployment and result

On 2026-09-11, initial WSL XBDM checks returned no route to both guests because
the instances were paused. The user resumed them; both recorded guest IPs then
answered `xbeinfo running`. No topology or host-binary change was requested.

Ds104 was uploaded to the client at `10.0.0.21`, destination
`E:\GAMES\halo-patched\default.xbe`: **200 OK**, 5,242,880 bytes. Its subsequent
`magicboot ... debug` launch returned **200 OK**. At 19:34:43 UTC, one-shot live
verification passed **7,043 checks with zero mismatches**, including all 3,515
candidate entries and implementation prefixes, twelve retained active
controls, and the original biped entry. Evidence:
`artifacts/rng_trace/system_link_bisect/ds104_simulation_off.live.json`.

The host was not redeployed. The user reported **"no desync here"** for ds104.
Record this as **NO_DESYNC_OBSERVED**, with exercise duration not reported; it
supports narrowing the broad set but does not prove a fix. Ds105 is the next
test: half A remains original and half B (1,757 candidates) runs patched code.
An actual ds105 failure would establish that half B can reproduce the failure
against this otherwise-original candidate background.

### Ds105 live verification and late failure report

Ds105 upload and `magicboot ... debug` each returned **200 OK**. Live
verification at 19:37:34 UTC passed all **7,043 checks**, with zero mismatches:
`artifacts/rng_trace/system_link_bisect/ds105_half_a_off.live.json`. The user
initially reported "no desync", then corrected this with "actually it desynced
now" while a ds106 transition was being attempted. The ds106 upload timed out
without an acknowledgement, and `magicboot` failed to connect. Ds106 is **not
confirmed deployed or launched**; its destination file may be partially
uploaded. The last successfully verified client remains ds105. Attribution of
the late failure was then clarified by the user: **"Actually no this was
ds105"**. Ds105 is **ACTUAL_DESYNC**, superseding the premature no-desync
report. Half B's 1,757 active candidates can reproduce the failure with half A
original. Ds106 has no gameplay verdict.

Do not launch the possibly partial destination file. Fully re-upload the next
selected artifact and wait for its successful upload completion before issuing
`magicboot`; a still-running tool session is not a successful upload. The host
remains unchanged; no watcher is running.

`record_result.py` updates both the manifest and a variant's deployment record
from an explicit user report, requiring a matching successful live check.
`derive_halves.py <parent> <first_name> <second_name>` prepares complementary
halves of a parent's retained candidates without changing the running guest.
It keeps object groups together until only one group remains, then splits by
function address. New variants receive the same byte verification and hashes.

Fallback also removes trace instrumentation inside the selected bodies. A
runtime localization implicates the enabled body/instrumentation set; it does
not by itself distinguish a gameplay-lift defect from a tracing side effect.

The next prepared pair splits half B while keeping half A original:

| Variant | Active candidates | XBE SHA-256 |
| --- | ---: | --- |
| ds107_b1_off | 877 | `dea89c7ed30ec11dba5ceb2d5fb6a55f84484b352700178933d7375269d37a34` |
| ds108_b2_off | 880 | `a471a8490a22cd0d07d5391112753aa840f86fed4ce16da655dd2589ac18c3fb` |

Both derive from the frozen ds103 artifact, with selections nested under ds105.
All 3,515 original entries and implementation prefixes were verified again,
and no unexpected bytes changed. WSL XBDM access recovered before the ds107
upload was attempted. For future no-desync reports, finish the test and report
duration before changing builds; ds105 demonstrated that an early quiet
interval can end in a real desync.

### Ds107 result: delayed actual desync

Ds107 upload completed with **200 OK**, 5,242,880 bytes, before its launch was
issued. `magicboot ... debug` then returned **200 OK**. Live verification at
19:43:48 UTC passed **7,043 checks with zero mismatches**. Evidence:
`artifacts/rng_trace/system_link_bisect/ds107_b1_off.live.json` and the matching
`.deployment.json` receipt. This full upload replaced the uncertain ds106
destination. The host remains unchanged.

Ds107 retains 877 candidates across 17 object groups: `actor_combat`,
`breakable_surfaces`, `cinematics`, `circular_queue`, `damage`, `game_engine`,
`game_time`, `hs_runtime`, `integer_math`, `network_client_manager`,
`network_client_message_handler`, `network_game_globals`, `path`,
`path_smoothing`, `player_queues_new`, `real_math`, and `vector_math`.
The user initially reported no desync, then confirmed **"ds107 eventually did
desync but it's much much much slower to desync than our earlier tests"**.
Record **ACTUAL_DESYNC**, with exact play duration unreported. The verified
ds107 configuration was still running: ds108's upload had begun but no ds108
`magicboot` was issued. Ds108 eventually uploaded successfully but was not
launched and has no gameplay verdict.

Both ds105 and ds107 late failure reports overlapped a next-artifact transfer.
That is a possible timing/network confounder. Future tests must have no upload,
build, or polling during gameplay. Wait for an actual failure or an explicitly
completed stable exercise before starting the next transfer. Do not interpret
slower failure as a pass or as proof that removed bodies are innocent; faster
contributors and a separate slow defect can coexist.

Ds104's short no-failure observation is not a duration-matched clean control.
Before assigning causality to a final small set, verify that it reproduces
without concurrent transfer and compare against an original-body control
exercised for at least as long. This also tests for defects outside the selected
candidate set. The current bisection identifies smaller reproducing
configurations, not yet a proven unique culprit.

The next prepared pair splits ds107:

| Variant | Active candidates | XBE SHA-256 |
| --- | ---: | --- |
| ds109_b2a_off | 439 | `557ba7d6b3622d0c46c9413c831d7b6546c4fd98ca16f6003492e4a3ae5edf23` |
| ds110_b2b_off | 438 | `603a4893c5f230079a792e6d8d271a71c61ff91663b8b59c4c001d753b223e23` |

No game-source or `kb.json` changes were made in this bisection session; the
underlying cause remains unfixed.

## Relevant artifacts

Primary paired captures:

- `ds69_pristine_probe_aa_trace.json`
- `ds69_pristine_probe_aa_host_trace.json`
- `ds69_pristine_probe_aa_comparison.json`
- `ds77_response_inputs_trace.json`
- `ds77_response_inputs_host_trace.json`
- `ds77_response_inputs_comparison.json`
- `ds78_try_place_io_trace.json`
- `ds78_try_place_io_host_trace.json`
- `ds79_collision_kind_trace.json`
- `ds79_collision_kind_host_trace.json`
- `ds81_dce0_fix_validation_trace.json`
- `ds81_dce0_fix_validation_host_trace.json`
- `ds82_cb00_original_trace.json`
- `ds82_cb00_original_host_trace.json`
- `ds83_bsp_vector_original_trace.json`
- `ds83_bsp_vector_original_host_trace.json`
- `ds85_matrix_transform_point_fix_trace.json`
- `ds85_matrix_transform_point_fix_host_trace.json`
- `ds86_local_ray_trace_client.json`
- `ds86_local_ray_trace_host.json`
- `ds88_raw_node_pose_client.json`
- `ds88_raw_node_pose_host.json`
- `ds89_node7_boundary_client.json`
- `ds89_node7_boundary_host.json`
- `ds90_pristine_node_producer_client.json`
- `ds90_pristine_node_producer_host.json`
- `ds91_pristine_node_math_client.json`
- `ds91_pristine_node_math_host.json`

Supporting equivalence results:

- `ds88_pose_to_matrix_equivalence.json`
- `ds88_matrix_multiply_equivalence.json`

Probe/build support:

- `build_original_probes_v5.py`
- `host_rng_probe_v15.xbe`
- `host_rng_probe_v15.json`
- `ds92_object_translate_client.xbe`
- `ds92_object_translate_symbols.pe`
- `tools/xbox/rng_trace_dump.py`

## Worktree cautions

The worktree contains uncommitted changes beyond this investigation, including
changes in game, projectile, math, object, and physics source files. Preserve
them and use scoped diffs. The trace probes and documentation are also
uncommitted.

`tools/kb_reg_baseline.json` has a stale top-level `0xb5d60` entry. A traced
build currently requires temporarily adding the register annotations from
`kb.json`; the build migrates that entry into `functions`, after which the file
must be restored exactly. Do not leave the migration as an unrelated diff.
