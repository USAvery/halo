# System-link desync: evidence, limitations, and current state

Updated: **2026-09-11**. Status: **OPEN — no proven root cause or verified fix**.

The investigation concerns a patched Halo CE Xbox client against a
cachebeta-derived host. Binary source of truth: debug build 2276,
`halo-patched/cachebeta.xbe`, MD5 `c7869590a1c64ad034e49a5ee0c02465`.
The recorded host artifact contains diagnostic probes; it is not byte-for-byte
pristine. Distinguish original gameplay bodies from an entirely unmodified XBE.

This file is the authoritative resume reference. Detailed older notes are
preserved, with their superseded claims and procedures explicitly marked:

- [Earlier investigation log](archive/system-link-rng-desync-investigation-log.md).
- [Archived ds72-ds110 working notes](archive/system-link-rng-desync-ds72-ds110-working-notes.md).

## Current operational state

Investigation and deployment work was stopped after the user raised concerns
about time, quota, and the quality of the bisection strategy. The subsequent
request was to correct this document. No guest access, restart, upload, or new
gameplay test was performed for this documentation update.

- **Last agent-launched and live-verified client: ds107.** Its launch returned
  `200 OK`; live verification at **19:43:48 UTC** passed 7,043 byte comparisons.
- **Last acknowledged upload to the client title path: ds109.** Its full
  5,242,880-byte upload returned `200 OK`. The agent **never issued its launch**.
  There is no ds109 live verification or gameplay verdict.
- The uploaded file and the running title must not be assumed to be the same
  artifact. Subsequent manual launches or changes have not been checked.
- The user later reported that **the build currently running had not desynced
  so far and seemed stable**. Its identity, exercise duration, and completion
  were not re-verified. Keep this as a separate, provisional observation. Do
  not assign it to ds109, turn it into a proven pass, or erase ds107's earlier
  confirmed failure report.
- The host was not redeployed during ds104-ds109 work. No translation watcher
  was started during these trials.
- No game-source or `kb.json` changes were made during this bisection work.
  The pre-existing `FUN_001a2f40` `ported:false` diagnostic fallback remains.

Saved topology, last successfully used in this session:

| Role | Guest XBDM IP | Emulator HMP endpoint |
| --- | --- | --- |
| Patched client | `10.0.0.21` | `127.0.0.1:4444` |
| Original-body probe host | `10.0.0.25` | `127.0.0.1:4446` |

Both guests use bridged networking and the title path
`E:\GAMES\halo-patched\default.xbe`. XBDM works from WSL with
`HALO_WINDOWS_REEXEC=1`. Initial reachability failures in this session cleared
when the user resumed paused guests. The listed monitor ports are HMP;
`xemu_qmp.py --port 4444 status` did not find a QMP instance.

## What the earlier captures establish

These findings are scoped to the retained captures and tested branches. They
do not establish one universal initiating defect for every reported desync.

- **RNG divergence is downstream in the captured failures.** Different
  collision/damage decisions precede different RNG caller sequences and draw
  counts. There is no retained evidence that a broken RNG generator initiates
  those failures. This does not prove every RNG-related path is correct.
- **Ds69 A/A control:** both peers used the same cachebeta-derived probe
  artifact. Analysis found 1,187 aligned RNG/caller events and 16,296 shared
  state-probe records without a value difference. Its watcher alert was
  snapshot tick skew. This is a historical control for that exercised setup,
  not a completed duration-matched control for the later slow failures.
- **Ds77-ds79:** a small grenade placement-input difference could become a
  much larger displacement. In ds78/ds79, equal placement start/target
  coordinates still produced a client-only type-3 hit on biped `e2770008`,
  surface 21; the host reported no hit. Explicit ray coordinates are not the
  complete collision input state.
- **Ds81-ds83:** restoring `FUN_0014dce0`, `FUN_0014cb00`, and
  `collision_bsp_test_vector` progressively did not remove the observed
  collision disagreement. This does not clear their callees or supplied state,
  nor exclude them as contributors in other configurations.
- **Ds85/ds86:** `matrix_transform_point` had an expression-order mismatch;
  correcting it reached 100% VC71 instruction match but did not remove the
  observed runtime problem. With equal source matrices, the sampled local
  transforms and rays matched. Other queries received different matrices.
- **Ds88/ds89:** descendant node matrices differed despite matching sampled
  pose words. A matching XOR fingerprint was not lossless proof of equal
  matrices. Synthetic equivalence passed 1,000 cases each for `FUN_00109500`
  and `matrix4x3_multiply`; the live in-place multiply case was not specifically
  established by that corpus.
- **Ds90/ds91:** restoring the node-matrix producer, then its three tested
  math bodies, did not remove the reported failure. Ds91 retained a placement
  event whose thrower/seat XY values already differed beforehand. This moves
  that observed difference upstream; it does not prove the entire node chain
  universally correct.
- **Ds92:** all 524 shared translation requests matched within the retained
  interval, but the decisive grenade placement was not retained.
- **Ds93:** every sampled translation preserved XYZ from entry through its
  stores and map connection. Player `e2770008` first differed in the shared
  non-initial interval at tick 45: X was `bed6d163` client versus `bed6d164`
  host. The value already differed on entry, supplied by original caller
  `FUN_001a5300` from `FUN_001a2f40`'s new-position output. This localizes that
  drift upstream of the translation stores; it does not make a one-ULP drift
  an actual-desync verdict.
- **Ds94:** restoring only `FUN_001a2f40` made early player translations exact
  through tick 97. Its original entry was verified live. This implicates that
  lifted body in one early drift path, not every system-link failure.
- **Ds101, tick 129:** all four player translations and the first grenade
  placement occurrence matched. Equal placement rays still yielded a client
  hit on `e2770008`, surface 21, versus no host collision. The differing hidden
  state or remaining code responsible for this outcome was not identified.
- **Ds102:** the translation watcher stopped play before the user considered
  the game desynchronized. The recorded words `bf818bd5` and `bf818b9d` differ
  by 56 representable float steps, despite the earlier one-ULP description.
  Numerical drift and an actual game failure are separate observations.
- **Ds103:** the user reported fast actual desync with a client artifact built
  to restore `FUN_001a2f40`. Post-match live fallback verification was
  unavailable because XBDM was unreachable. The artifact configuration and
  user report argue against that body being the sole cause, subject to this
  runtime-identity qualification. Its deactivation is not a fix.

## Existing binary-backed source corrections

These corrections predate the bisection trials. They are fidelity improvements,
not a verified resolution of the overall desync. Source files also contain
extensive `HALO_RNG_TRACE` instrumentation; do not count that as gameplay fixes.

1. In `unit_throw_grenade_release`, the original stores and reloads
   `1.0f - ratio_val` as float:

   ```c
   ratio_val = 1.0f - ratio_val;
   HALO_FLT_ROUNDTRIP(ratio_val);
   ```

2. Ds100 localized a biped Z discrepancy to a missing float store between a
   subtraction and addition. `FUN_001a2f40` now follows the original store at
   `0x1a366d`:

   ```c
   z_delta = disp[2] - fdist;
   HALO_FLT_ROUNDTRIP(z_delta);
   physics[0x30] = z_delta + physics[0xd];
   ```

3. The same body prematurely narrowed a planar scale that the original keeps
   wide across two multiplies. It now declares `x87_wide_t planar_scale`:

   ```c
   planar_scale = *(double *)0x2573d8 / sqrtf(r);
   gy = gy * planar_scale;
   gx = planar_scale * gx;
   ```

The earlier `matrix_transform_point` product-order correction is described
above. The biped corrections remain in source but that body is disabled in the
ds103-derived variants.

## Bisection record: observations, not a proven culprit set

The initial selection contained 3,515 active exported redirects across 71
`kb.json` object groups. All variants derive from the frozen ds103 XBE and PE.
The first splits kept object groups together and balanced **function counts**;
they were not ranked by the prior captures, actual execution, or call-path
evidence. Some object assignments are semantically mixed: `real_math.obj`, for
example, contains actor-action functions. Treat group names as attribution
hints, not trustworthy subsystem boundaries.

Counts below mean **active members of this selected set**, not all patched
functions in the game. Ports outside the set remain. Every variant retains
ds103's original-biped fallback. Half B is ds105's 1,757 surviving candidates;
ds107 and ds108 partition that half, and ds109/ds110 partition ds107.

| Variant | Active selected candidates | Deployment evidence | Gameplay evidence |
| --- | ---: | --- | --- |
| ds104_simulation_off | 0 | Upload and launch 200; 7,043 live checks passed | User: "no desync here"; duration/completion unspecified. Not a proven clean control. |
| ds105_half_a_off | 1,757 | Upload and launch 200; 7,043 live checks passed | Initial no-desync report superseded by confirmed actual ds105 desync. Next upload overlapped the late report. |
| ds106_half_b_off | 1,758 | Upload timed out; launch connection failed; no successful live check | No attributable gameplay verdict. Later uploads replaced the uncertain destination file. |
| ds107_b1_off | 877 | Upload and launch 200; 7,043 live checks passed | User confirmed actual desync, **much slower** than earlier tests. Ds108 transfer overlapped the late report. |
| ds108_b2_off | 880 | Upload 200; **not launched by the agent** | Untested. |
| ds109_b2a_off | 439 | Upload 200; **not launched by the agent** | Untested. Last acknowledged file upload, not last verified running title. |
| ds110_b2b_off | 438 | Prepared locally only | Untested. |

The later stable-running-build report is separate and unattributed; see current
operational state. No trial has a recorded completed, duration-matched stable
exercise sufficient to exclude its candidate set.

### Limits of the bisection evidence

- The agent prematurely treated interim no-desync updates as reasons to start
  another transfer. This happened around the ds105 and ds107 late failures.
  Runtime identity is supported for those failures, but transfer timing/network
  interference remains a possible confounder. It has not been shown to cause
  or not cause desync.
- Slower failure does not clear removed bodies. Multiple defects, interactions,
  or reduced amplification could account for the different time to failure.
- The broad zero-candidate configuration ds104 was not exercised for a recorded
  duration comparable to slow ds107. A defect outside the selected set has not
  been excluded. The count reductions do not prove that the cause lies within
  the final 877, or within the untested 439-candidate variant.
- Reverting a body also removes its internal trace instrumentation. A changed
  result does not by itself distinguish a gameplay defect from a tracing effect.
- Entry and fallback byte verification proves the intended patch bytes were
  present. It is not behavioral proof of every ABI adapter or every possible
  execution path; retained inlined copies would require separate inspection.

### Artifact provenance and checks

All paths below are relative to `artifacts/rng_trace/`:

| Artifact | SHA-256 |
| --- | --- |
| ds103_biped_original_fallback_client.xbe | `d1e31bf00254f783c925c31c41b15048c458b18801258f79a08817f4f43ca91c` |
| ds103_biped_original_fallback_symbols.pe | `2f3eedd1f7816a436c2b3d54ea3319df4042cbe602495e0a66b123662adb0d64` |
| ds102_biped_planar_scale_client.xbe | `a2a57ea30b139691ae2261e199b08737a3365a54c65e9b9287c5e0f3ab2b2440` |
| host_rng_probe_focused.xbe | `0dbb944b2a37e82ac3eeaf910cad68a3eed6ae123f31672b39944bfa6ad2669f` |
| system_link_bisect/ds104_simulation_off.xbe | `be74821fb3f1364b431a8eddd361e78d0a4df9499ae2d550c624b891455a45d5` |
| system_link_bisect/ds105_half_a_off.xbe | `ef624a21cdf3f18a8cf3858cc10c4d6f20323c85ac2840b7c205968b47b8ddb0` |
| system_link_bisect/ds106_half_b_off.xbe | `c66acbaad25c8a14cd9e5ddbce3b7cd96eeacc6a7d66cf6b79719406738db084` |
| system_link_bisect/ds107_b1_off.xbe | `dea89c7ed30ec11dba5ceb2d5fb6a55f84484b352700178933d7375269d37a34` |
| system_link_bisect/ds108_b2_off.xbe | `a471a8490a22cd0d07d5391112753aa840f86fed4ce16da655dd2589ac18c3fb` |
| system_link_bisect/ds109_b2a_off.xbe | `557ba7d6b3622d0c46c9413c831d7b6546c4fd98ca16f6003492e4a3ae5edf23` |
| system_link_bisect/ds110_b2b_off.xbe | `603a4893c5f230079a792e6d8d271a71c61ff91663b8b59c4c001d753b223e23` |

`build_original_probe_focused.py` was edited after the recorded host artifact
was built. Do not assume its current contents reproduce that artifact. The
saved host artifact's hash was rechecked locally and matched the table; that
is not a fresh runtime host-identity check.

Under `artifacts/rng_trace/system_link_bisect/`:

- `manifest.json` records candidate addresses, exclusions, hashes, overlays,
  and reported verdicts. `UNTESTED` does not mean not uploaded; consult this
  document and deployment receipts for the distinction.
- `build_variants.py` uses the production patcher's ABI-aware deactivation
  generator on the frozen XBE. It restores selected original entries and
  replaces compiled entries with original-body fallbacks, without recompilation
  or editing `kb.json`. It checked all 3,515 selected entries and compiled
  prefixes, the fixed biped fallback, available stub space, and byte changes
  restricted to selected patches and section digests.
- `derive_halves.py` produced further complementary partitions with byte
  checks. Its function-count balancing is mechanical; do not mistake it for
  evidence-based prioritization. Re-running `build_variants.py` can overwrite
  the evolving manifest; preserve results before using it again.
- `verify_live.py` performs one-shot XBDM comparisons before play. Ds104,
  ds105, and ds107 each passed 7,043 checks: 3,515 original entries, 3,515
  implementation prefixes, 12 retained active controls, and the biped entry.
  `.live.json` files record those checks; `.deployment.json` files record
  deployment/results for ds104-ds108. The ds109 upload acknowledgement is in
  the session tool output and this document; no ds109 live receipt exists.
- `record_result.py` stores a supplied report after checking that a successful
  live-check file matches the variant hash. It does not itself establish test
  duration, later title identity, clean conditions, or causality.
- The production patcher's reverse/fallback thunk self-tests passed, including
  generation for 881 register-argument functions. Generation success is not
  runtime equivalence proof for all of them.

## Evidence-based leads, not demonstrated defects

The saved original-binary call graph at
`artifacts/ntsc_callgraph/callgraph.json` records the debug-2276 MD5 above.
Intersecting its direct CALL edges with ds107's surviving candidate redirects
identified these **six direct callees of original `FUN_001a2f40`**:

| Address | Surviving patched helper |
| --- | --- |
| `0x12170` | `FUN_00012170` |
| `0x121a0` | `distance_squared3d` |
| `0x12f10` | `magnitude3d` |
| `0x12f80` | `vector3d_scale_add` |
| `0x12fe0` | `FUN_00012fe0` |
| `0x13010` | `normalize3d` |

This gives a concrete connection to the earlier position-producer evidence,
unlike an arbitrary half of all active functions. It does **not** establish
that one of these helpers is wrong, executed on the decisive branch, or caused
ds107's later failure. No new instruction-level defect in these helpers was
demonstrated in this session. A remaining x87 precision/operation-order issue
is a working hypothesis; hidden state, other surviving code, multiple defects,
and instrumentation effects remain unresolved.

`system_link_bisect/evidence_priorities.json` records additional surviving paths
from `FUN_001a5300`, `object_try_place`, the node-matrix producer, and the tested
collision bodies, along with prior scoped equivalence evidence. Its graph
omits indirect calls and tail jumps. Static reachability is not an execution
trace. The ds88/ds91 matrix-helper evidence lowers priority for the exercised
cases; it does not justify blanket exclusions.

## If investigation is resumed

1. Preserve the running session and dirty worktree. Establish actual running
   artifact identity before assigning any further observation to a variant;
   uploaded-file identity is insufficient.
2. Use the known causal boundaries and surviving call paths to inspect a
   bounded hypothesis. Do not resume automatic function-count halving or add
   another one-off float watcher. State what a proposed test distinguishes.
3. Record scenario, completed exercise duration, build identity, and actual
   game-reported/visible failure. Interim "no desync so far" remains interim.
   Leave the test undisturbed until failure or explicit completion: no upload,
   build, or debugger polling during play.
4. Obtain a duration-matched original-body control before claiming causal
   isolation, accounting for ds107's slow failure. The historical ds69 A/A
   result and brief ds104 observation do not substitute for that comparison.
   Repeat full A/A only when the control setup/binaries changed or a specific
   new concern requires it.
5. For any targeted variant, verify the exact artifact and fallback ABI, retain
   controls and complements where needed, and require upload completion,
   launch acknowledgement, and a matching live check before requesting play.
   A running command session is not a successful upload.
6. Only accept binary-backed source corrections. Validate the eventual fix
   against both the isolated failure and the full patched configuration;
   resolving a slow residual defect need not resolve faster contributors.

Do not use `watch_object_translate_diff.py` as the final desync oracle. Do not
call a higher static match score, one-ULP difference, finite stable interval,
or smaller active set proof of a fix.

## Deployment and worktree cautions

Use WSL XBDM with `HALO_WINDOWS_REEXEC=1`; direct Windows XBDM timed out in this
bridged setup. Select an explicitly identified artifact, hash that exact file,
and require a completed successful `--sendfile` to the title path before:

```text
magicboot title=E:\GAMES\halo-patched\default.xbe debug
```

The normal build has previously left `halo-patched/default.xbe` stale. Save
the exact XBE and matching PE; invoke the patch stage explicitly when needed
and verify the resulting bytes. Do not rebuild the host incidentally. This
investigation's deployments were XBE-only; do not change HDD `init.txt` as an
incidental deployment step.

The worktree is heavily dirty, including existing source precision fixes and
tracing in units, bipeds, objects, collision, game, and math files. Preserve
unrelated changes. `kb.json`'s existing biped deactivation is diagnostic and
allowlisted; no additional bisection deactivations were written there. The
earlier ds91 node-chain deactivations had already been restored in `kb.json`;
individual artifact overlays can override their runtime state.

Earlier full builds encountered a stale top-level `0xb5d60` entry in
`tools/kb_reg_baseline.json`. Treat that as a recorded build caveat, not a new
verification of the current file. Ds104-ds110 reused the frozen ds103 artifact
and did not require editing that baseline or debugger configuration files.
