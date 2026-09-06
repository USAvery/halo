# Deploying to the bridged xemu guests

How to get a build onto the two networked xemu instances and pull diagnostics
back off them. This is the setup used for system-link work, where two guests
have to see each other on the same broadcast domain.

## Why bridged, and who can reach whom

System link needs both guests on a shared broadcast domain. xemu's NAT mode does
not provide one, so both instances run **bridged** networking via npcap.

The consequence that trips people up: npcap injects frames onto the wire without
reflecting them back up the local stack, so the **bridged guests are invisible
to Windows** — but they are directly reachable **from WSL2**. Run every XBDM
tool from WSL, not from a Windows shell.

## Addresses

As of 2026-09-05. These come from DHCP and can change.

| Instance | Debug IP (XBDM) | Title IP |
|---|---|---|
| Our patched build | `10.0.0.21` | `10.0.0.22` |
| Pristine `cachebeta.xbe` | `10.0.0.24` | `10.0.0.23` |

## Build

Build **only from `/mnt/g/dev/halo`**. Other worktrees on this box
(`halo-bugs`, `halo-fix-*`, …) build their own XBE, and deploying one of those
by accident silently invalidates a capture — you end up analysing a binary that
isn't the one you think you changed.

```bash
# normal build
rtk python3 tools/build/build.py -q --target halo

# with the RNG draw-trace ring compiled in (about 5 min)
rtk python3 tools/build/build.py -q --rng-trace
```

## Deploy

```bash
HALO_NATIVE_XBDM=1 HALO_WINDOWS_REEXEC=1 python3 tools/xbox/deploy_xbox.py \
    --skip-build --xbe-only -x 10.0.0.21
```

Two environment variables, both load-bearing:

- `HALO_WINDOWS_REEXEC=1` — several XBDM tools call `maybe_reexec_on_windows`
  and hand themselves to Windows Python, which cannot see the bridged guests and
  times out with `WinError 10060`. This keeps them on Linux.
- `HALO_NATIVE_XBDM=1` — makes `deploy_xbox.py` use Linux Python for the upload.

The deploy command trips the skill-router gate once. Rerun it unchanged.

## Capture, while still in game

The trace ring lives in **title memory**. Once the game returns to the
dashboard, the dashboard XBE is loaded over it and `getmem` returns `????` —
the capture is gone. Dump before quitting.

```bash
# ring from our client
HALO_WINDOWS_REEXEC=1 python3 tools/xbox/rng_trace_dump.py \
    --host 10.0.0.21 --out artifacts/rng_trace/aN.json

# decode the info probes
python3 tools/xbox/rng_trace_dump.py --probes artifacts/rng_trace/aN.json

# debug.txt from both boxes
HALO_WINDOWS_REEXEC=1 python3 tools/xbox/xbdm_debug_txt.py --host 10.0.0.21 \
    --lines 200 --output artifacts/rng_trace/debug_client_aN.txt --timeout 30
HALO_WINDOWS_REEXEC=1 python3 tools/xbox/xbdm_debug_txt.py --host 10.0.0.24 \
    --lines 200 --output artifacts/rng_trace/debug_host_aN.txt --timeout 30
```

`debug.txt` on both boxes carries the `out of sync` line with the tick number
and both seeds. Correlate that tick against the trace records.

Host-side probe tooling lives in `artifacts/rng_trace/`, not `tools/xbox/`:
`build_original_probes.py`, `test_original_probes.py`,
`verify_original_probes.py`, `host_diagnostic.py`, `compare_pair.py`.
`host_diagnostic.py restore` relaunches the host's recorded path
(`E:\GAMES\halo-patched\cachebeta.xbe`).

## Troubleshooting

**"The guest is down / connection timed out."** Usually neither. A 3-second
connect timeout reports a guest as down when it is merely in-game, and far more
so while a build is saturating the box. Use 30s, which is what the commands
above already pass. Measured 2026-09-06: `10.0.0.21` timed out at 3s during a
concurrent worktree build, then answered `201- connected` on the first attempt
at 5s once the box went idle. In-game XBDM is not a dead channel — the existing
18 MB in-game ring dumps in `artifacts/rng_trace/` were all taken in-game.

Corollary: **don't start a build on this box while you are reproducing.** The
contention is what makes XBDM look dead.

**"Which guest is actually in a game?"** Ask it, rather than guessing from the
xemu window:

```
xbeinfo running   ->  402- file not found              = at the dashboard
xbeinfo running   ->  202- multiline response follows  = a title is running
```

**"Is the running XBE really my patched build?"** `debug.txt` persists on disk
and *appends* across launches, so an old `DECOMP BUILD` line proves nothing
about what is running now. Check the XBE header at `0x10104` instead:

| Build | Image size at `0x10104` |
|---|---|
| pristine `cachebeta.xbe` | `0x6315e0` |
| our patched build | `0x8fa000` |

## Related

- `docs/system-link-rng-desync.md` — the investigation this workflow serves.
- `docs/rng-trace.md` — ring format and probe kinds.
- Skill `debug-xemu` — xemu configuration (System Memory **must** be 128 MiB for
  debug build 2276) and the standalone-ISO recipe.
