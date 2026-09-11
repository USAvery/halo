#!/usr/bin/env python3
"""Pause two lockstep xemu guests on the first stable biped-position mismatch."""
from __future__ import annotations

import argparse
from pathlib import Path
import struct
import subprocess
import sys
import time

from lockstep_datum_diff import (
    CLIENT_HMP, DATA_T_HDR_LEN, DATA_T_MAGIC, GAME_TIME_GLOBALS_PTR,
    HEAP_HI, HEAP_LO, HOST_HMP, HMP, OBJECT_BODY_PTR_OFF, OBJECT_TABLE_PTR,
    u32,
)


def resolve_bipeds(hmp):
    table = u32(hmp.read_mem(OBJECT_TABLE_PTR, 4))
    if not (HEAP_LO <= table < HEAP_HI):
        return {}
    header = hmp.read_mem(table, DATA_T_HDR_LEN)
    if u32(header, 0x28) != DATA_T_MAGIC:
        return {}
    maximum, element_size = struct.unpack_from("<hh", header, 0x20)
    current = struct.unpack_from("<h", header, 0x2E)[0]
    data = u32(header, 0x34)
    if element_size <= 0 or not (HEAP_LO <= data < HEAP_HI):
        return {}
    count = current if 0 < current <= maximum else maximum
    count = min(count, 512)
    records = hmp.read_mem(data, count * element_size)
    result = {}
    for slot in range(count):
        record = records[slot * element_size:(slot + 1) * element_size]
        salt = struct.unpack_from("<H", record, 0)[0]
        kind = record[3] if len(record) > 3 else 0xFF
        body = u32(record, OBJECT_BODY_PTR_OFF) if len(record) >= 12 else 0
        if salt != 0 and kind == 0 and HEAP_LO <= body < HEAP_HI:
            result[(salt << 16) | slot] = body
    return result


def windows_path(path):
    value = str(path.resolve())
    if value.startswith("/mnt/") and len(value) > 6:
        return value[5].upper() + ":\\" + value[7:].replace("/", "\\")
    return value


def snapshot_positions(hmp, tick_pointer, bodies, snapshot_path):
    addresses = [body + 0x50 for body in bodies.values()]
    base = min(addresses)
    end = max(addresses) + 12
    tick = u32(hmp.read_mem(tick_pointer + 0x0C, 4))
    hmp.cmd("memsave 0x%x %d %s" %
            (base, end - base, windows_path(snapshot_path)))
    raw = snapshot_path.read_bytes()
    if len(raw) != end - base:
        return None
    positions = {}
    for handle, body in sorted(bodies.items()):
        positions[handle] = struct.unpack_from("<3I", raw, body + 0x50 - base)
    return tick, positions


def dump_rings(label, client_pe, host_pe):
    subprocess.check_call([
        sys.executable, "tools/xbox/rng_trace_dump.py",
        "--hmp-port", "4444", "--pe", client_pe,
        "--out", "artifacts/rng_trace/%s_client.json" % label,
        "--timeout", "20",
    ])
    subprocess.check_call([
        sys.executable, "tools/xbox/rng_trace_dump.py",
        "--hmp-port", "4446", "--pe", host_pe, "--runtime-base", "0x642000",
        "--out", "artifacts/rng_trace/%s_host.json" % label,
        "--timeout", "20",
    ])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument(
        "--client-pe", default="artifacts/rng_trace/ds95_biped_branch_symbols.pe")
    parser.add_argument(
        "--host-pe", default="artifacts/rng_trace/session_symbols.pe")
    parser.add_argument("--timeout", type=float, default=1200.0)
    parser.add_argument("--interval", type=float, default=0.01)
    args = parser.parse_args()
    if not args.label.replace("_", "").replace("-", "").isalnum():
        parser.error("--label must contain only letters, digits, underscore, or hyphen")

    client = HMP(*CLIENT_HMP, timeout=15.0)
    host = HMP(*HOST_HMP, timeout=15.0)
    deadline = time.monotonic() + args.timeout
    try:
        client_bipeds = {}
        host_bipeds = {}
        print("waiting for four shared bipeds")
        while time.monotonic() < deadline:
            if len(client_bipeds) < 4:
                client_bipeds = resolve_bipeds(client)
            if len(host_bipeds) < 4:
                host_bipeds = resolve_bipeds(host)
            shared = sorted(set(client_bipeds) & set(host_bipeds))
            if len(shared) >= 4:
                client_bipeds = {handle: client_bipeds[handle]
                                 for handle in shared}
                host_bipeds = {handle: host_bipeds[handle]
                               for handle in shared}
                print("armed: %s" %
                      ",".join("%08x" % handle for handle in shared))
                print("client bodies: %s" % ",".join(
                    "%08x=%08x" % (handle, client_bipeds[handle])
                    for handle in shared))
                print("host bodies: %s" % ",".join(
                    "%08x=%08x" % (handle, host_bipeds[handle])
                    for handle in shared))
                break
            time.sleep(0.05)
        else:
            print("FAILED timeout resolving shared bipeds")
            return 1

        client_pending = None
        host_pending = None
        client_history = {}
        host_history = {}
        checked_ticks = set()
        last_report = 0.0
        client_tick_pointer = u32(client.read_mem(GAME_TIME_GLOBALS_PTR, 4))
        host_tick_pointer = u32(host.read_mem(GAME_TIME_GLOBALS_PTR, 4))
        client_snapshot = Path(
            "artifacts/rng_trace/.biped_watch_client.bin").resolve()
        host_snapshot = Path(
            "artifacts/rng_trace/.biped_watch_host.bin").resolve()
        while time.monotonic() < deadline:
            client.cmd("stop")
            host.cmd("stop")
            try:
                client_sample = snapshot_positions(
                    client, client_tick_pointer, client_bipeds, client_snapshot)
                host_sample = snapshot_positions(
                    host, host_tick_pointer, host_bipeds, host_snapshot)
            finally:
                client.cmd("cont")
                host.cmd("cont")
            if client_sample is None or host_sample is None:
                time.sleep(args.interval)
                continue

            if client_pending is not None and client_sample[0] != client_pending[0]:
                client_history[client_pending[0]] = client_pending[1]
            if host_pending is not None and host_sample[0] != host_pending[0]:
                host_history[host_pending[0]] = host_pending[1]
            client_pending = client_sample
            host_pending = host_sample

            now = time.monotonic()
            if now - last_report >= 10.0:
                print("status client_tick=%d host_tick=%d client_history=%d "
                      "host_history=%d common=%d" % (
                          client_sample[0], host_sample[0],
                          len(client_history), len(host_history),
                          len(set(client_history) & set(host_history))))
                last_report = now

            common_ticks = sorted((set(client_history) & set(host_history)) -
                                  checked_ticks)
            if not common_ticks:
                time.sleep(args.interval)
                continue
            client_tick = common_ticks[0]
            checked_ticks.add(client_tick)
            client_positions = client_history[client_tick]
            host_positions = host_history[client_tick]
            differences = [handle for handle in shared
                           if client_positions[handle] != host_positions[handle]]
            if not differences:
                floor = client_tick - 128
                client_history = {tick: value for tick, value in client_history.items()
                                  if tick >= floor}
                host_history = {tick: value for tick, value in host_history.items()
                                if tick >= floor}
                checked_ticks = {tick for tick in checked_ticks if tick >= floor}
                time.sleep(args.interval)
                continue

            print("BIPED POSITION MISMATCH tick=%d handles=%s" % (
                client_tick,
                ",".join("%08x" % handle for handle in differences)))
            for handle in differences:
                print("  %08x client=%08x/%08x/%08x host=%08x/%08x/%08x" % (
                    (handle,) + client_positions[handle] + host_positions[handle]))
            client.cmd("stop")
            host.cmd("stop")
            with open("artifacts/rng_trace/%s_hmp_done.txt" % args.label,
                      "w") as output:
                output.write("biped position mismatch tick=%d handles=%s\n" % (
                    client_tick,
                    ",".join("%08x" % handle for handle in differences)))
            client.close()
            host.close()
            client = None
            host = None
            dump_rings(args.label, args.client_pe, args.host_pe)
            print("DONE paused; rings captured")
            return 0
        print("FAILED timeout waiting for biped-position mismatch")
        return 1
    finally:
        if client is not None:
            client.close()
        if host is not None:
            host.close()


if __name__ == "__main__":
    raise SystemExit(main())
