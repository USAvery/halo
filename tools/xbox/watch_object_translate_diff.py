#!/usr/bin/env python3
"""Pause both lockstep xemu guests on the first object-translation mismatch."""
from __future__ import annotations

import argparse
import collections
import struct
import subprocess
import sys
import time

from lockstep_datum_diff import CLIENT_HMP, HOST_HMP, HMP


RING_MAGIC = 0x54474E52
RING_CAPACITY = 65536
RECORD_SIZE = 16
KIND_POSITION_XY = 83
KIND_POSITION_Z_HANDLE = 84


class RingReader:
    def __init__(self, hmp, base):
        self.hmp = hmp
        self.base = base
        header = hmp.read_mem(base, 16)
        magic, version, capacity, write_index = struct.unpack("<4I", header)
        if magic != RING_MAGIC or version != 1 or capacity != RING_CAPACITY:
            raise RuntimeError("invalid trace ring at 0x%x" % base)
        self.index = write_index

    def resync(self):
        header = self.hmp.read_mem(self.base, 16)
        magic, version, capacity, write_index = struct.unpack("<4I", header)
        if magic != RING_MAGIC or version != 1 or capacity != RING_CAPACITY:
            raise RuntimeError("trace ring became invalid at 0x%x" % self.base)
        self.index = write_index

    def poll(self):
        header = self.hmp.read_mem(self.base, 16)
        magic, version, capacity, write_index = struct.unpack("<4I", header)
        if magic != RING_MAGIC or version != 1 or capacity != RING_CAPACITY:
            raise RuntimeError("trace ring became invalid at 0x%x" % self.base)
        count = write_index - self.index
        if count <= 0:
            return []
        if count > RING_CAPACITY:
            raise RuntimeError("trace reader fell behind by %d records" % count)

        records = []
        while self.index < write_index:
            slot = self.index & (RING_CAPACITY - 1)
            chunk_records = min(write_index - self.index,
                                RING_CAPACITY - slot, 512)
            address = self.base + 16 + slot * RECORD_SIZE
            raw = self.hmp.read_mem(address, chunk_records * RECORD_SIZE)
            for offset in range(0, len(raw), RECORD_SIZE):
                records.append(struct.unpack_from("<4I", raw, offset))
            self.index += chunk_records
        return records


class TranslationReader:
    def __init__(self):
        self.pending = None
        self.occurrences = collections.defaultdict(int)

    def feed(self, records):
        calls = []
        for packed_tick, value, caller, extra in records:
            kind = packed_tick >> 24
            tick = packed_tick & 0x00FFFFFF
            if kind == KIND_POSITION_XY:
                self.pending = (tick, value, extra, caller)
            elif kind == KIND_POSITION_Z_HANDLE:
                if self.pending is None or self.pending[0] != tick:
                    self.pending = None
                    continue
                unused_tick, x_bits, y_bits, xy_caller = self.pending
                handle = extra
                occurrence = self.occurrences[(tick, handle)]
                self.occurrences[(tick, handle)] += 1
                key = (tick, handle, occurrence)
                calls.append((key, (x_bits, y_bits, value), xy_caller))
                self.pending = None
        return calls


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
    parser.add_argument("--client-ring", type=lambda value: int(value, 0),
                        default=0x80ED54)
    parser.add_argument("--host-ring", type=lambda value: int(value, 0),
                        default=0x7FF900)
    parser.add_argument(
        "--client-pe",
        default="artifacts/rng_trace/ds94_biped_body_original_symbols.pe")
    parser.add_argument(
        "--host-pe", default="artifacts/rng_trace/session_symbols.pe")
    parser.add_argument("--timeout", type=float, default=1200.0)
    parser.add_argument("--interval", type=float, default=0.02)
    parser.add_argument("--min-tick", type=int, default=8)
    args = parser.parse_args()
    if not args.label.replace("_", "").replace("-", "").isalnum():
        parser.error("--label must contain only letters, digits, underscore, or hyphen")

    client_hmp = HMP(*CLIENT_HMP, timeout=15.0)
    host_hmp = HMP(*HOST_HMP, timeout=15.0)
    client_ring = RingReader(client_hmp, args.client_ring)
    host_ring = RingReader(host_hmp, args.host_ring)
    client_translations = TranslationReader()
    host_translations = TranslationReader()
    client_calls = {}
    host_calls = {}
    # Connecting both monitors is slow enough for the high-volume client ring
    # to wrap.  Establish the arm point only after both are ready.
    host_ring.resync()
    client_ring.resync()
    deadline = time.monotonic() + args.timeout
    try:
        print("armed: streaming object_translate records; client stops first")
        while time.monotonic() < deadline:
            new_client = client_translations.feed(client_ring.poll())
            new_host = host_translations.feed(host_ring.poll())
            for key, xyz, caller in new_client:
                client_calls[key] = (xyz, caller)
            for key, xyz, caller in new_host:
                host_calls[key] = (xyz, caller)

            shared = set(key for key, unused_xyz, unused_caller in new_client
                         if key in host_calls)
            shared.update(key for key, unused_xyz, unused_caller in new_host
                          if key in client_calls)
            for key in sorted(shared):
                tick, handle, occurrence = key
                if tick < args.min_tick:
                    continue
                client_xyz, client_caller = client_calls[key]
                host_xyz, host_caller = host_calls[key]
                if client_xyz == host_xyz:
                    continue
                print("OBJECT_TRANSLATE MISMATCH tick=%d handle=%08x occurrence=%d" %
                      (tick, handle, occurrence))
                print("  client=%08x/%08x/%08x caller=%08x" %
                      (client_xyz + (client_caller,)))
                print("  host  =%08x/%08x/%08x caller=%08x" %
                      (host_xyz + (host_caller,)))
                client_hmp.cmd("stop")
                host_hmp.cmd("stop")
                with open("artifacts/rng_trace/%s_hmp_done.txt" % args.label,
                          "w") as output:
                    output.write(
                        "object_translate mismatch tick=%d handle=%08x occurrence=%d\n" %
                        (tick, handle, occurrence))
                client_hmp.close()
                host_hmp.close()
                client_hmp = None
                host_hmp = None
                dump_rings(args.label, args.client_pe, args.host_pe)
                print("DONE paused; rings captured")
                return 0

            if len(client_calls) > 20000:
                cutoff = max(key[0] for key in client_calls) - 300
                client_calls = {key: value for key, value in client_calls.items()
                                if key[0] >= cutoff}
                host_calls = {key: value for key, value in host_calls.items()
                              if key[0] >= cutoff}
            time.sleep(args.interval)
        print("FAILED timeout waiting for object-translation mismatch")
        return 1
    finally:
        if client_hmp is not None:
            client_hmp.close()
        if host_hmp is not None:
            host_hmp.close()


if __name__ == "__main__":
    raise SystemExit(main())
