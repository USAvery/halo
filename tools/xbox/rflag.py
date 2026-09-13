#!/usr/bin/env python3
"""Read/write the live rasterizer render-pass feature flags over XBDM.

The flags are a contiguous byte block at 0x3256c2..0x3256dd, registered by name
in the XBE table at 0x2f23d8 (stride 12: {byte *flag, char *name, int type}).
rasterizer_frame_begin latches 0x3256c8 to 2 after the first frame, so writes
made here stick for the rest of the run.

XBDM getmem/setmem only -- they run as a guest service and never halt the CPU.
Do NOT reach for the gdbstub on this box: connecting to :1234 halts the emulated
CPU (which freezes XBDM with it) and re-issuing the `gdbserver` HMP command has
crashed xemu outright.  See agent memory reference_xemu_gdbstub_halts_cpu_landmine
and reference_gdb_via_qmp.

Usage:
  rflag.py                       # list every flag and its live value
  rflag.py shadows 0             # substring-match one flag name, set it
  rflag.py --all 1               # restore every flag to 1
  rflag.py --host 10.0.0.21 ...  # target a bridged Xbox instead of localhost
"""
import argparse
import os
import re
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RDCP = os.path.join(HERE, "xbdm_rdcp.py")

FLAGS = [
    (0x3256C2, "rasterizer_debug_meter_shader"),
    (0x3256C3, "rasterizer_models"),
    (0x3256C4, "rasterizer_model_transparents"),
    (0x3256C5, "rasterizer_draw_first_person_weapon_first"),
    (0x3256C6, "rasterizer_stencil_mask"),
    (0x3256C7, "rasterizer_environment"),
    (0x3256C8, "rasterizer_environment_lightmaps"),
    (0x3256C9, "rasterizer_environment_shadows"),
    (0x3256CA, "rasterizer_environment_diffuse_lights"),
    (0x3256CB, "rasterizer_environment_diffuse_textures"),
    (0x3256CC, "rasterizer_environment_decals"),
    (0x3256CD, "rasterizer_environment_specular_lights"),
    (0x3256CE, "rasterizer_environment_specular_lightmaps"),
    (0x3256CF, "rasterizer_environment_reflection_lightmap_mask"),
    (0x3256D0, "rasterizer_environment_reflection_mirrors"),
    (0x3256D1, "rasterizer_environment_reflections"),
    (0x3256D2, "rasterizer_environment_transparents"),
    (0x3256D3, "rasterizer_environment_fog"),
    (0x3256D4, "rasterizer_environment_fog_screen"),
    (0x3256D5, "rasterizer_water"),
    (0x3256D6, "rasterizer_lens_flares"),
    (0x3256D7, "rasterizer_dynamic_unlit_geometry"),
    (0x3256D8, "rasterizer_dynamic_lit_geometry"),
    (0x3256D9, "rasterizer_dynamic_screen_geometry"),
    (0x3256DA, "rasterizer_hud_motion_sensor"),
    (0x3256DB, "rasterizer_detail_objects"),
    (0x3256DC, "rasterizer_debug_geometry"),
    (0x3256DD, "rasterizer_debug_geometry_multipass"),
    (0x3256DE, "rasterizer_fog_atmosphere"),
    (0x3256DF, "rasterizer_fog_plane"),
]

BASE = FLAGS[0][0]
COUNT = FLAGS[-1][0] - BASE + 1


def rdcp(args, command):
    """Minimal RDCP round-trip.

    xbdm_rdcp.py's own connect times out against the bridged targets from WSL,
    while a plain socket to the same host:port answers instantly -- so talk the
    protocol directly rather than shelling out to it.
    """
    s = socket.create_connection((args.host, args.port), timeout=args.timeout)
    try:
        s.settimeout(args.timeout)
        f = s.makefile("rwb", buffering=0)
        f.readline()  # 201- connected
        f.write(command.encode("ascii") + b"\r\n")
        lines = []
        first = f.readline().decode("ascii", "replace").strip()
        if not first.startswith("2"):
            raise SystemExit("xbdm: %s -> %s" % (command, first))
        if first.startswith("202"):
            while True:
                line = f.readline().decode("ascii", "replace").strip()
                if line == "." or line == "":
                    break
                lines.append(line)
        return lines
    finally:
        s.close()


def read_all(args):
    hexes = [l for l in rdcp(args, "getmem addr=0x%x length=0x%x" % (BASE, COUNT))
             if re.fullmatch(r"(?:[0-9a-fA-F]{2})+", l)]
    blob = bytes.fromhex("".join(hexes))
    return {BASE + i: blob[i] for i in range(min(len(blob), COUNT))}


def write_byte(args, addr, val):
    """XBDM setmem takes data=<hexstring>.

    `value=...` is accepted and answers `200- set 0 bytes` -- a whole toggle
    sweep can read as "no effect" purely because of that silent no-op.
    """
    resp = rdcp(args, "setmem addr=0x%x data=%02x" % (addr, val))
    return resp


def main():
    p = argparse.ArgumentParser()
    p.add_argument("name", nargs="?", help="substring of a flag name")
    p.add_argument("value", nargs="?", type=int, help="0 or 1")
    p.add_argument("--all", type=int, metavar="VALUE",
                   help="set every flag to VALUE")
    p.add_argument("--host", default=os.environ.get("XBDM_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int,
                   default=int(os.environ.get("XBDM_PORT", "731")))
    p.add_argument("--timeout", type=float, default=10.0)
    args = p.parse_args()

    if args.all is not None:
        for addr, _ in FLAGS:
            write_byte(args, addr, args.all)
    elif args.name is not None:
        if args.value is None:
            raise SystemExit("give a value: rflag.py <name> 0|1")
        hits = [(a, n) for a, n in FLAGS if args.name == n]
        if not hits:
            hits = [(a, n) for a, n in FLAGS if args.name in n]
        if len(hits) != 1:
            raise SystemExit("ambiguous/none: %s" % [n for _, n in hits])
        write_byte(args, hits[0][0], args.value)
        print("set %s = %d" % (hits[0][1], args.value))

    vals = read_all(args)
    for addr, name in FLAGS:
        print("0x%06x  %s  %s" % (addr, vals.get(addr, "?"), name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
