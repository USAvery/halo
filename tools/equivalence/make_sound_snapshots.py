#!/usr/bin/env python3
"""Write constrained sound-manager equivalence snapshots.

These cases keep the sound helpers on deterministic, finite paths while still
driving their stateful branches.  They are intentionally standalone: the
batch manifest can opt into any case after a reviewed run, but generating or
running them never changes a shared baseline.
"""

import argparse
import json
import struct
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = HERE / "regression_snapshots"


def _write(output_dir, name, snapshot):
    path = output_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(path)


def _attenuation_entry(start, end, now, shape=0, current=0.25, target=1.0):
    entry = bytearray(0xb0)
    struct.pack_into("<H", entry, 0x00, 1)       # valid datum salt
    struct.pack_into("<h", entry, 0x92, shape)
    struct.pack_into("<f", entry, 0x9c, current)
    struct.pack_into("<f", entry, 0xa0, target)
    struct.pack_into("<i", entry, 0xa4, start)
    struct.pack_into("<i", entry, 0xa8, end)
    data = bytearray(0x40)
    struct.pack_into("<H", data, 0x2e, 1)       # current_count
    struct.pack_into("<H", data, 0x22, 0x100)  # element_size
    struct.pack_into("<I", data, 0x34, 0x00700600)
    return {
        "0x00700600": entry.hex(),
        "0x00700500": data.hex(),
        # The extra bytes satisfy the DIR32/global seed window and make the
        # slot value visible to the stub's argument trace.
        "0x004fdba4": struct.pack("<II", 0x00700500, 0).hex(),
        "0x004eaf4c": struct.pack("<II", now, 0).hex(),
    }


def _attenuation_snapshot(start, end, now, shape=0, current=0.25,
                          target=1.0, description=""):
    return {
        "description": description,
        "build_label": "halo-debug-2276-synthetic",
        "verified": True,
        "regions": _attenuation_entry(start, end, now, shape, current, target),
        "arg_overrides": {"sound_handle": [0, 0]},
        # The lifted object calls the named import while the raw-XBE oracle
        # calls the same routine by its FUN_ address.  Pin both spellings so
        # the two sides receive the synthetic entry pointer.
        "stub_returns": {
            "datum_get": 0x00700000,
            "FUN_00119320": 0x00700000,
        },
    }


def _sound_entry(start_tick, channel_index=0, tag_index=0):
    entry = bytearray(0xb0)
    struct.pack_into("<h", entry, 0x06, channel_index)
    struct.pack_into("<i", entry, 0x08, tag_index)
    struct.pack_into("<i", entry, 0x84, start_tick)
    return entry


def _oldest_snapshot(candidate_starts, distances, description):
    target = _sound_entry(0, 0, 0)
    first = _sound_entry(candidate_starts[0], 0, 0)
    second = _sound_entry(candidate_starts[1], 0, 0)
    struct.pack_into("<H", target, 0x00, 1)
    struct.pack_into("<H", first, 0x00, 2)
    struct.pack_into("<H", second, 0x00, 3)

    data = bytearray(0x40)
    struct.pack_into("<H", data, 0x2e, 3)       # current_count
    struct.pack_into("<H", data, 0x22, 0x100)  # element_size
    struct.pack_into("<I", data, 0x34, 0x00700000)

    channels = bytearray(0x30)
    struct.pack_into("<I", channels, 0x00, 0x00000200)
    struct.pack_into("<I", channels, 0x18, 0x00000300)

    tag = bytearray(8)
    struct.pack_into("<H", tag, 0x04, 0)
    class_def = bytearray(8)
    struct.pack_into("<i", class_def, 0x04, 10)

    return {
        "description": description,
        "build_label": "halo-debug-2276-synthetic",
        "verified": True,
        "regions": {
            "0x00700000": bytes(target).hex(),
            "0x00700100": bytes(first).hex(),
            "0x00700200": bytes(second).hex(),
            "0x00700500": bytes(data).hex(),
            "0x00700300": bytes(tag).hex(),
            "0x00700400": bytes(class_def).hex(),
            "0x004fdba4": struct.pack("<II", 0x00700500, 0).hex(),
            "0x004eb0b4": struct.pack("<hhI", 2, 0, 0).hex(),
            "0x004fc3a0": bytes(channels).hex(),
            "0x004eaf4c": struct.pack("<II", 100, 0).hex(),
        },
        "arg_overrides": {
            "sound_handle": [0, 0],
            "channels": bytes((0, 1)).hex(),
            "count": [1, 2],
        },
        "stub_returns": {
            "datum_get": [0x00700000, 0x00700100, 0x00700200],
            "FUN_00119320": [0x00700000, 0x00700100, 0x00700200],
            "tag_get": 0x00700300,
            "FUN_001ba140": 0x00700300,
            "sound_class_get_definition": 0x00700400,
            "FUN_001c88c0": 0x00700400,
            "FUN_001ccbe0": distances,
        },
    }


def build_snapshots():
    snapshots = {}
    snapshots["sound_update_channel_attenuation_linear_before.json"] = _attenuation_snapshot(
        10, 30, 0,
        description="attenuation: linear envelope before start clamps to current",
    )
    snapshots["sound_update_channel_attenuation_linear_mid.json"] = _attenuation_snapshot(
        10, 30, 20,
        description="attenuation: linear envelope midpoint returns the lerp",
    )
    snapshots["sound_update_channel_attenuation_linear_complete.json"] = _attenuation_snapshot(
        10, 30, 40,
        description="attenuation: completed linear envelope clears tick fields",
    )
    snapshots["sound_find_oldest_channel_elapsed_filter.json"] = _oldest_snapshot(
        (95, 80), [5.0, 5.0, 5.0],
        "oldest channel: first candidate too young, second qualifies",
    )
    snapshots["sound_find_oldest_channel_distance_filter.json"] = _oldest_snapshot(
        (80, 80), [5.0, 2.0, 5.0],
        "oldest channel: first candidate too far by the distance gate, second qualifies",
    )
    return snapshots


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    for name, snapshot in build_snapshots().items():
        _write(args.output_dir, name, snapshot)


if __name__ == "__main__":
    main()
