# Handover — door-floor dark trapezoid

## Objective
Find and fix the root cause of a visual regression in the C-ported build. A
flat, hard-edged, uniformly dark trapezoid appears on the floor near doors. It
moves and reappears as the player turns. A side-by-side comparison against the
unmodified `cachebeta.xbe` at a matching camera pose confirms this is a
regression from the C port. It is not stock 2001 debug-build behavior.

## State on 2026-09-12 (end of session 2)

### The artifact is a MISSING contribution, not an added object
This is the main result of the session, and it changes the search direction.

I set `rasterizer_environment_diffuse_textures` (`0x3256cb`) to 0 with a live
XBDM write and measured a scan line at y=265:

| region | baseline | pass off |
|---|---|---|
| lit floor | 58-77 | 14 |
| dark trapezoid | 11-18 | 11-18 |

Turning the pass off pulls the lit floor down to the brightness of the
trapezoid. The trapezoid itself does not change. So the trapezoid is the area
where the environment diffuse-texture pass adds nothing. Stop looking for code
that draws a dark shape. Look for the code that drops surfaces from that pass.

### Every pass-sweep result before this session is VOID
`tools/xbox/rflag.py` sent `setmem addr=0x.. value=0x..`. XBDM accepts that form
and answers `200- set 0 bytes`. It writes nothing. The correct form is
`setmem addr=0x.. data=<hexstring>`, which answers `200- set 1 bytes`.

Every flag write before the fix was a silent no-op. Every "the artifact is still
present" reading from a flag toggle proves nothing. The tool is fixed and
verified by readback. Re-run any flag conclusion you want to rely on.

Two more tool faults fixed in the same file:
- Substring matching made `rasterizer_environment` match 14 names. The tool now
  tries an exact name match first.
- `--all 1` turns on `rasterizer_debug_meter_shader` and
  `rasterizer_fog_atmosphere`, which default to 0. That draws debug overlays and
  makes every image diff large. Read the original values first, then restore
  each flag to its own original value.

### The flag table is larger than recorded
Parsed from the XBE table at `0x2f23d8` (stride 12,
`{byte *flag, char *name, int type}`). The block runs to `0x325700`, past the
`0x3256dd` recorded earlier. `rflag.py` carries 30 entries.

### A deterministic repro now exists — and the level is d20, NOT c40
`10.0.0.21` boots straight to the door from a saved core. Verified working
2026-09-12 23:18, with the artifact visible and measurable.

**The level is `d20`.** Earlier sessions recorded c40 and filed the core under
`input-recordings/levels/c40/`. That was never checked against the file. The
core's own identity string is `#5levels\d20\d20`, and the engine says so:

    EXCEPTION halt in c:\halo\SOURCE\saved games\game_state.c,#409:
      expected "levels\c40\c40" but got "levels\d20\d20"

The core is now filed at `input-recordings/levels/d20/door-trapezoid/`.
**Do not commit `core.bin`** — it is copyrighted game memory. (The whole
`input-recordings/` tree is gitignored, so this is already safe.)

Repro procedure:
1. Repo-root `init.txt` must read:
   `game_difficulty_set impossible` / `map_name d20` / `core_load_at_startup`.
   `build_deploy_run.sh` sends this file on every deploy. It is gitignored.
2. `d:\core\core.bin` on the box must be the d20 door core. Upload it with a
   300-second socket timeout and `sendall()` in 64 KB chunks; a 60-second
   timeout fails mid-transfer. 3.4 MB takes about 2.5 seconds.
3. Deploy with `build_deploy_run.sh` (below). Wait about 70 seconds.

Measured signature (screenshot `artifacts/door_trapezoid_d20_repro.png`):
the floor steps sharply from **15-25** (dark trapezoid) to **50-85** (lit), and
the boundary moves right as y increases — the diagonal edge. Scan lines
y=240/255/265/275/285 all show it.

**Do not hand-write a `magicboot`.** Mine failed to launch the title, took XBDM
down, and a VM reset then landed in the XDK Launcher. Use `build_deploy_run.sh`.

**The pristine box (`10.0.0.24`) rejects every core** with the same
`game_state.c,#409` halt. That is now explained: it is a map-identity mismatch,
not a broken box. Give it `map_name d20` and the matching core and it should
load. Untested.

### `FUN_00197570` x87 association FIXED — artifact still present
A real defect, found by hand-diff and fixed (uncommitted, in the worktree).
The function computes a signed plane distance; its accumulation order did not
match the binary. The source comment above it already stated the correct order
("normal.z term first, then .y, then .x") — only the code disagreed.

| | grouping |
|---|---|
| original XBE | `(n.z*d2 + n.y*d1) + n.x*d0` |
| ours, before | `(n.x*d0 + n.y*d1) + n.z*d2` |
| ours, after  | `(n.y*d1 + n.z*d2) + n.x*d0` |

The `x` term was added FIRST where the original adds it LAST. Float addition is
not associative, so the result differed by an ULP. Proven by disassembling both
compiled objects, not inferred. The inner pair differs only by commutativity,
which is exact in IEEE.

Why it looked promising: the single runtime caller is inside `FUN_00197b00`'s
portal-neighbour loop, where the result gates a `continue` that skips an entire
neighbour cluster. An ULP flip at the `d <= threshold` boundary would drop a
whole cluster — a hard-edged, view-dependent region of missing geometry.

**It did not fix the artifact.** The trapezoid is unchanged with the fix
deployed. Keep the fix (it is correct against the binary), but the root cause
is elsewhere.

Two tools cannot see this bug class, which is why it survived: VC71's official
score is mnemonic-only (85.3% before and after), and
`tools/audit/check_fpu_association.py` skips this function
("register ebp not traced to a parameter") because of its register-arg ABI.
**Other reg-arg functions are equally unchecked — that is a live blind spot.**

### `FUN_00197310` is cleared by hand
The portal screen-space clip in `structures.c` (line 5997). Both x87 parity
branches and the sign order are faithful to the disassembly. Its 43.2% operand
score is frame layout, not a logic fault.

### PVS/portal block audit — 3 of 10 cleared by hand (2026-09-12, late)
Hand-diffed our C against the pristine XBE disassembly. No builds spent.

- `FUN_00196fd0` (`0x196fd0`) — de-duplicating surface gather. Faithful. The
  loop counters, the `sar 5 / shl 2` byte offset, the break-on-max inside the
  innermost loop, and the running EDI counter as the return value all match.
- `FUN_00197130` (`0x197130`) — per-leaf cluster gather. Faithful. `min(a,b)`
  cull, the `break` to the function exit on a full out array, and the per-
  iteration recompute of the loop end all match.
- `render_structure_visibility` (`0x198180`) — per-frame bitvector rebuild.
  Faithful. The `0x50678c` fill value selection (`-1` when the active cluster
  is `-1`, else `0`), both `((count + 0x1f) >> 5) * 4` sizes, the `0x5137d0`
  clear sized from `scenario + 0xf8`, the single loop counter kept as
  `cluster_index` plus its sign-extended `bit_index`, and the final
  `FUN_001966b0` / `FUN_00196850` dispatch all match.
- The register-arg contracts of the two unported cull callees are correct:
  `FUN_00196a60(cull_bounds@<ecx>, bounds@<edx>)` and
  `FUN_00196b10(bounds@<eax>, param_8, param_9)`.

Two defects found, both believed inert:
1. The doc comment on `FUN_00197130` says it "sets a bit in the global cluster
   visibility set at `0x5137d0`". The code only tests that bitvector. The
   comment is wrong, not the code.
2. The original `FUN_00197130` returns only `AX`, so the upper 16 bits of `EAX`
   are garbage. Our C returns a clean 32-bit `int`. Every caller must already
   truncate, because the original works. Confirm before relying on it.

Still to audit in this block: `0x1974f0`, `0x197570`, `0x1975e0`, `0x1978a0`,
`0x197e90`, `0x198070`. Start with **`0x198070`** — `render_structure_visibility`
calls it directly, and it runs the visibility sweep itself.

## Cleared earlier (still valid)
These used `ported:false` group deactivation plus a real rebuild and deploy. The
flag-write bug does not affect them.

1. `structure_visibility.obj` — 5 functions.
2. `rasterizer_xbox_environment.c` — 22 functions, including `FUN_00160f50`,
   which handles the bugged material.
3. `structures.c` render-structure family — 19 functions, `0x1954d0`-`0x196190`.
4. `decals.c` — 28 functions, `0x98970`-`0x9c4b0`.
5. `scenario.c` shadow gating and orchestration — 17 functions,
   `0x18b000`-`0x18c3a0`.

Hand-verified faithful against the pristine disassembly (no builds spent):
`FUN_00172a30`, `FUN_0018b990`, `FUN_00196190`, `render_camera_build_frustum`.
Note that the z-axis asymmetry in `FUN_0018b990` is Bungie's. Do not "fix" it.

## Open problems

### 1. RESOLVED — the `EIP=0x00000001` crash was NOT the deactivation
An earlier note blamed a crash (thread 28, ACCESS_VIOLATION `0xc0000005`,
`Eip=0x00000001`) on setting `ported:false` on ten `structures.obj` addresses.
**That attribution was wrong.** The identical crash reproduces with a clean
`kb.json` and no deactivations at all.

It is not a wild jump. It is the engine's own assert halt:

    EXCEPTION halt in c:\halo\SOURCE\saved games\game_state.c,#409:
      expected "levels\c40\c40" but got "levels\d20\d20"

`init.txt` was never being ignored — `debug.txt` shows all three commands
running (`init: map_name c40`, `init: core_load_at_startup`). The core simply
belonged to a different map than the one requested, so the engine halted by
design. Fixed by setting `map_name d20`.

Consequence: the ten-function deactivation test proved **nothing** in either
direction. The PVS/portal block `0x196fd0`-`0x198180` is still untested by
bisect, along with `render_cameras.obj`, `object_lights.obj`,
`rasterizer_xbox_lights.obj`, and `rasterizer_xbox_models.obj`.

### 2. The build on `10.0.0.21` is down
`src/halo/ai/actor_moving.c:1061:1: error: unused label 'build_vector'`
(`-Werror`). That file belongs to another agent. A retry loop has failed ten
times. `/tmp/retry_deploy.sh`, task `bsnebxg5n`.

Do not edit that file. Either wait, or add `-Wno-unused-label` for one build.

### 3. The full flag sweep has never run with working writes
`/tmp/fullsweep2.py` is written and never ran. It records the original values,
restores one flag at a time, and measures a full-image diff plus a dark-region
and light-region gap against a 1.0% noise floor.

## Next steps
1. Unblock the build. Prefer `-Wno-unused-label` over waiting, because the box is
   the only measurement device.
2. Run `/tmp/fullsweep2.py`. This confirms `0x3256cb` alone, or names a second
   pass.
3. **Audit the surface selection for the diffuse-texture pass.** This is the only
   step that can find the defect. The two draw functions gated by `0x3256cb` are
   `FUN_00161f00` (line 963) and `FUN_00162560` (line 1104) in
   `rasterizer_xbox_environment.c`. Both are bisect-cleared. So the fault is
   upstream: in the caller that builds the surface list, or in the per-surface
   test that decides which surfaces enter it. This step needs no running box.
4. Explain the `EIP=0x00000001` crash. See Open problem 1.

## Tools and hazards

- **`tools/xbox/rflag.py`** (untracked) reads and writes the 30 live render-pass
  flags over XBDM. It is the only way to name a render pass without a rebuild.
- **Never attach the gdbstub.** Any TCP connection to port 1234 halts the
  emulated CPU and freezes XBDM with it. Re-issuing the `gdbserver` HMP command
  has crashed xemu. Use XBDM `getmem` and `setmem`.
- **`tools/xbox/xbdm_rdcp.py` cannot connect from WSL.** It reports a connect
  timeout. A raw socket to the same host and port answers at once. `rflag.py`
  carries its own minimal RDCP client. `/tmp/xput.py` uploads files. Large
  uploads need a 300-second socket timeout and `sendall()` in 64 KB chunks.
- **Two bridged xemu instances.** `10.0.0.21` runs the patched build, QMP
  `127.0.0.1:4444`. `10.0.0.24` runs the pristine `cachebeta.xbe`, QMP
  `127.0.0.1:4446`, monitor `127.0.0.1:4449`. Both use XBDM on TCP 731.
- **Deploy with**
  `./tools/xbox/build_deploy_run.sh --xemu-bridged --xbox 10.0.0.21 -- -q`.
  It prints `verify: OK`. Do not substitute an `mcp__xemu__xemu_reset()`, which
  reboots whatever XBE was last uploaded.
- **Other agents work in this tree.** `kb.json`, `src/halo/ai/actor_moving.c`,
  and `src/halo/game/players.c` all carry changes that are not mine. Do not
  discard them. Scratch `ported:false` edits left in `kb.json` can be absorbed
  into an unrelated auto-generated commit within minutes. Commit or stash them
  at once.
- **`shadow_culling_analysis.md`** (repo root, untracked) comes from an external
  tool. It is a lead, not verified fact. Its dynamic-shadow hypothesis now looks
  wrong: the artifact is a missing diffuse-texture contribution, not a drawn
  shadow.
- **`rtk jq` returns empty results on some `kb.json` queries** that plain `jq`
  answers. Use plain `jq` with `--indent 1`.

## Resume prompt
Continuing a C-port regression in Halo CE Xbox (`/mnt/g/dev/halo`): a flat,
hard-edged dark trapezoid on the floor near doors. The artifact is the area
where the environment diffuse-texture pass (`0x3256cb`) adds nothing, proved by
a live XBDM flag write and a y=265 scan line. Read this file, then start at Next
step 3: find the code that selects surfaces for that pass. Note that every
render-flag conclusion recorded before 2026-09-12 is void, because `rflag.py`
was writing nothing.

---

# Session update 2026-09-13: live deactivation, and the render path is CLEARED

## The big result

With **all 5,605 lifted functions deactivated at once at runtime**, the
trapezoid disappears. The dark floor patch rises from 20.0 to 75.9 while the
lit patch stays at 64.8. Re-activating every redirect brings the artifact back.

    BASE      dark=20.0  lit=64.5
    ALL-OFF   dark=75.9  lit=64.8    <- artifact gone
    RESTORED  dark=20.0  lit=64.5

So the defect **is** in our C code. It is not stock behaviour, and it is not
baked into `core.bin`.

## The render path is cleared

Deactivating **all 608 ported functions** in `rasterizer*`, `structure*`,
`render*`, `shaders.obj` and `lightmap` objects at once changes **nothing**:
`dark=20.0 lit=64.5`, byte-for-byte the baseline. Every address was read back
to confirm the write landed.

The cause is therefore in the other ~4,997 lifts. Search the code that
*produces* the data the renderer consumes, not the renderer.

## Single-function deactivation inside a chain is UNRELIABLE

Deactivating `render_structure_visibility` (0x198180) alone drops the lit floor
from 64.5 to 16.9. Deactivating it **together with the rest of the render path**
changes nothing at all. The solo result is a hybrid-state artifact: the original
function running against our lifted callees. Never conclude from a solo
deactivation inside a call chain without also testing the whole chain.

## New technique: live per-function lift deactivation (no rebuild)

`tools/build/patch.py` writes `68 <imm32> C3` (`push <impl>; ret`) over bytes
[0,6) at the function's original VA. The pristine box runs the same base image,
so the original prologue can be read from it.

1. Read 6 bytes at the VA from `10.0.0.24` (pristine) - the original prologue.
2. Read 6 bytes at the same VA from `10.0.0.21` (patched) - the redirect. It
   must start with `0x68`, or the function is not really redirected.
3. `setmem` the pristine bytes on `10.0.0.21` to deactivate the lift.
4. `setmem` the saved redirect back to re-activate it.

Always read the bytes back. The write is confirmed, and the operation is
reversible in milliseconds. A full A/B of one function takes about 15 seconds
instead of a 6-minute rebuild and deploy.

Use **one persistent XBDM socket** for many commands: about 22 ms per
`getmem`/`setmem`, against about 250 ms when each command opens its own
connection. 5,605 functions take 90 seconds to deactivate.

Harness scripts for this session live in `$CLAUDE_JOB_DIR/tmp`: `xsess.py`
(persistent XBDM session), `livedeact.py` (one function), `groupdeact.py` and
`bisect.py` (delta-debug over a set), `xdeploy.py` (Linux-native deploy).

## Deploy from WSL is broken in the repo tooling

`tools/xbox/deploy_xbox.py` calls `build_windows_python_command()` on WSL, so
XBDM traffic goes through **Windows** Python. Windows cannot reach the
Linux-side bridge at `10.0.0.21`, so every connection fails with
`[WinError 10060]`. `xbcp.exe` fails for the same reason.

`build_deploy_run.sh` also defaults to `XBOX_HOST=127.0.0.1`. Running it
without that variable resets the **local** xemu through QMP `127.0.0.1:4444`,
which is the bridged box's QMP port.

Deploy from Linux instead. The exact command sequence the tool uses is:

    reboot                                              (releases default.xbe)
    sendfile name="E:\GAMES\halo-patched\default.xbe" length=N   + raw bytes
    magicboot title=E:\GAMES\halo-patched\default.xbe debug

The earlier note "do not hand-write a magicboot" was wrong about the command,
not the risk: the failure came from omitting `debug` and the full path. The
line above is the tool's own string.

The built XBE is `halo-patched/default.xbe`, not `build/default.xbe`.

## The pristine box cannot load our cores

`10.0.0.24` halts on any core saved by the patched build:

    EXCEPTION halt in c:\halo\SOURCE\saved games\game_state.c,#413:
    allocation checksum mismatch

Cross-box comparison at an identical camera through a shared core is therefore
impossible. Do not spend more time on it.

## Cleared this session

- `render_camera_build_frustum` (0x187250) - rebuilt with `ported:false`,
  measured, no change. kb.json restored.
- `FUN_001966b0`, `FUN_00197b00`, `FUN_00198070`, `FUN_00197570`,
  `FUN_001975e0`, `FUN_001978a0`, `FUN_00197e90` - live deactivation, no change.
- The entire 608-function render path, deactivated together - no change.

## Measurement

Sample the dark patch at `x 200-360, y 255-285` and the lit patch at
`x 420-500, y 255-275` on a 640x480 XBDM screenshot. The camera is static, so
frame-to-frame spread is 0.0 and a single frame is enough. Artifact present
means dark about 20; artifact gone means dark about 76.

## Next step

Finish the delta-debug bisect over the lifts (`bisect.py`, log in
`$CLAUDE_JOB_DIR/tmp/bisect.log`). If no single half clears the artifact, the
cause is an interaction and `frontier.json` holds the two halves to recurse
into.

---

# RESOLVED 2026-09-13: comparator return type was one byte, not four

## The fix

`src/types.h:1748-1749`

    -typedef int  (*profile_sort16_compare_proc)(uint16_t a, uint16_t b);
    -typedef int  (*profile_sort32_compare_proc)(int32_t  a, int32_t  b);
    +typedef bool (*profile_sort16_compare_proc)(uint16_t a, uint16_t b);
    +typedef bool (*profile_sort32_compare_proc)(int32_t  a, int32_t  b);

Both selection sorts in `profile.c` test the comparator result with
`TEST AL,AL` -- one byte (`0x91d22` in `FUN_00091cf0`, `0x91d7c` in
`FUN_00091d50`). Declaring the comparator as returning `int` made clang emit
`TEST EAX,EAX`, which also reads whatever the callee left in the upper 24 bits
of EAX. When that residue was non-zero on a comparison that should have been
false, the "is greater" test flipped and the sort produced a different order.
That order reached the renderer as a floor patch the environment
diffuse-texture pass never contributed to.

## Measured result

    before                      dark=20.0  lit=64.5
    all 5,605 lifts off (ref)   dark=75.9  lit=64.8
    after the fix               dark=75.7  lit=64.6

The fixed build matches the all-lifts-off reference to within 0.2. Confirmed
visually: `artifacts/door_trapezoid_d20_repro.png` (hard-edged dark trapezoid)
against `artifacts/door_trapezoid_d20_FIXED.png` (clean textured floor). The
rebuilt binary emits `TEST AL,AL`, verified by reading it back off the box.

## How it was found

Live per-function deactivation, narrowing by measurement at every step:

1. All 5,605 lifts off -> artifact gone. So it is our C code.
2. All 608 render-path lifts off -> no change. So it is NOT the renderer.
3. Per-object sweep of 55 objects -> only `profile.obj` moves the dark patch
   (+55.7 of the +55.9 total). Every other object read exactly 20.0/64.5.
4. Per-function sweep of profile.obj's 35 lifts -> `0x091d50` alone, +55.7.
5. Disassembly A/B of `0x091d50` against the pristine box -> `TEST EAX,EAX`
   where the original has `TEST AL,AL`.

## Why the earlier hunt kept missing it

`profile.c` is instrumentation, not rendering. `profile_enter_private` is
called from inside `FUN_001966b0` and its siblings, so a defect there reaches
the renderer without any render code being wrong. Every earlier bisect that
"cleared" the render path was correct -- the cause was never in it.

## A scratch file named after a stdlib module corrupted measurements

A delta-debug script named `bisect.py` shadowed the stdlib `bisect` module.
`PIL` imports `tempfile` -> `random` -> `import bisect`, so every later script
that put that directory on `sys.path` silently **executed a second full bisect
run against the box** while something else was measuring. Cleanup found 5,079
addresses left deactivated by the phantom run. Never name a scratch script
after a stdlib module. On a hardware-in-the-loop harness this does not fail
loudly -- it quietly invalidates every measurement taken afterwards.

## Generalise the defect

The bug class is "return width narrower in the binary than in our C". Any
lifted function pointer whose result the original tests with `TEST AL,AL`,
`TEST AX,AX`, or a byte/word compare must be declared byte- or word-wide.
Declaring it `int` is not a harmless widening: it makes the caller read
register bits the original never looked at. Worth a detector over kb.json
callback typedefs.
