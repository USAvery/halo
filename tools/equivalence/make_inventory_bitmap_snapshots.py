#!/usr/bin/env python3
"""Generate deterministic equivalence snapshots for bitmap mip helpers.

The snapshots pin only the fields consumed by the mip helpers and make
``bitmap_verify`` succeed symmetrically. Inventory is intentionally excluded:
``unit_inventory_next_weapon`` is exported, but the raw-XBE lane currently
classifies the oracle as non-leaf and the candidate as leaf, so interception
ends in ``external_relocations`` before a meaningful comparison.
"""

import argparse
import json
import struct
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = HERE / "regression_snapshots"


def _bitmap_header(width, height, flags, last_mip):
    data = bytearray(0x16)
    struct.pack_into("<H", data, 0x04, width)
    struct.pack_into("<H", data, 0x06, height)
    data[0x0E] = flags
    struct.pack_into("<h", data, 0x14, last_mip)
    return bytes(data).hex()


def _bitmap_snapshot(kind, compressed):
    name = "compressed" if compressed else "uncompressed"
    # Five mip levels cover level 0, an interior level, and the level-5
    # one-pixel clamp.  The range override deliberately varies across seeds;
    # a fixed mip level is a useful value check but is classified as a
    # vacuous early-exit path by the batch harness.
    header = _bitmap_header(32, 16, 2 if compressed else 0, 5)
    return {
        "description": (
            f"{kind} bitmap mip levels 0..5 ({name}); "
            "valid synthetic bitmap header"
        ),
        "build_label": "halo-debug-2276-synthetic",
        "arg_overrides": {
            "bitmap": header,
            "mipmap_index": [0, 5],
        },
        "stub_returns": {
            "bitmap_verify": 1,
        },
    }


def build_snapshots():
    snapshots = {}

    for kind in ("width", "height"):
        for compressed in (False, True):
            suffix = "compressed" if compressed else "plain"
            filename = f"bitmap_mipmap_{kind}_{suffix}_range.json"
            snapshots[filename] = _bitmap_snapshot(
                "bitmap_mipmap_get_height"
                if kind == "height" else "bitmap_mipmap_width",
                compressed,
            )
    return snapshots


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for filename, snapshot in build_snapshots().items():
        path = args.output_dir / filename
        path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
