#!/usr/bin/env python3
"""Build bounded synthetic snapshots for the antenna equivalence cases.

The snapshots intentionally keep the pool/tag-block records small and
deterministic.  They are consumed by tools/equivalence/unicorn_diff.py and
are also useful when reproducing a batch-verify result locally.
"""

import argparse
import json
import struct
from pathlib import Path


def _blob(size):
    return bytearray(size)


def _put_i16(buf, offset, value):
    struct.pack_into("<h", buf, offset, value)


def _put_i32(buf, offset, value):
    struct.pack_into("<i", buf, offset, value)


def _put_f32(buf, offset, value):
    struct.pack_into("<f", buf, offset, value)


def _hex(buf):
    return bytes(buf).hex()


def _record():
    """Return one 0x2bc-byte antenna record with two bounded markers."""
    record = _blob(0x2BC)

    # The record is live, references object datum 1, and has not advanced its
    # update counter yet.  The marker entries are 0x20 bytes each.
    record[5] = 0
    _put_i32(record, 0x08, 0)
    _put_i32(record, 0x0C, 1)
    _put_f32(record, 0x1C, 1.0)
    _put_f32(record, 0x20, 0.0)
    _put_f32(record, 0x24, 0.0)
    _put_f32(record, 0x34, 1.0)
    _put_i16(record, 0x38, 0)
    _put_f32(record, 0x3C, 2.0)
    _put_f32(record, 0x40, 0.0)
    _put_f32(record, 0x44, 0.0)
    _put_f32(record, 0x54, 1.0)
    _put_i16(record, 0x58, 0)
    return record


def _tag_definition():
    """Return the prefix of a tag definition used by simulate_rope."""
    tag_def = _blob(0x100)
    _put_i32(tag_def, 0x3C, 0)
    _put_f32(tag_def, 0x90, 1.0)
    # tag_block is {count, address}; the address is also pinned by the stub
    # return below so either path stays inside the synthetic map.
    _put_i32(tag_def, 0xC4, 1)
    _put_i32(tag_def, 0xC8, 0x700300)
    return tag_def


def _update_snapshot():
    pool = _blob(0x38)
    record = _record()

    # Exercise one live pool entry and the terminating iterator edge without
    # descending into simulate_rope.  That sibling is defined in the same TU,
    # so the lifted object executes it directly while the raw-XBE oracle uses
    # a call interception; testing that path requires a runtime dual oracle.
    _put_i32(record, 0x0C, -1)
    pool[0:32] = b"antenna" + b"\0" * 25
    _put_i16(pool, 0x20, 12)
    _put_i16(pool, 0x22, 0x2BC)
    pool[0x24] = 1
    pool[0x25] = 0
    _put_i32(pool, 0x28, 0x64407440)
    _put_i16(pool, 0x2E, 1)
    _put_i32(pool, 0x34, 0x700600)

    return {
        "description": "bounded antenna pool with a terminating data_next_index sequence",
        "build_label": "halo-debug-2276",
        "regions": {
            # g_antenna_data is a pointer to this valid data_t header.
            "0x005A90D4": (struct.pack("<I", 0x700500) + b"\0" * 4).hex(),
            "0x700500": _hex(pool),
            "0x700600": _hex(record),
            "0x700700": _hex(_tag_definition()),
        },
        "arg_overrides": {
            "delta_time": 0.1,
        },
        "stub_returns": {
            # One valid handle followed by the sentinel prevents the original
            # and lifted loops from spinning on a default zero return.
            "data_next_index": [0, -1],
            "datum_get": 0x700600,
            "tag_get": 0x700700,
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tools/equivalence/regression_snapshots"),
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    snapshots = {
        "antenna_debug_data_update_all_bounded.json": _update_snapshot(),
    }
    for name, snapshot in snapshots.items():
        path = args.output_dir / name
        path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
