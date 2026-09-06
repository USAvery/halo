#!/usr/bin/env python3
"""Report turn-in-place cosine behaviour per unit from an rng_trace dump.

Segments the capture at every map-init marker before it counts anything.  That
segmentation is not optional.  The trace ring wraps (capacity 65536) and the
tick counter restarts at each `game_initialize_for_new_map`, so grouping records
by `tick` alone merges several map instances into one, and a wrapped ring can
retain the SAME instance twice.  Both mistakes were made on
artifacts/rng_trace/cos_h.json and both produced a confident wrong answer: a
phantom "host enters the fork twice per tick" that disappeared once the capture
was split at the markers.  See docs/system-link-rng-desync.md.

    python3 tools/xbox/rng_cosine_report.py CAPTURE.json [CAPTURE2.json ...]
"""
import argparse
import collections
import json
import struct
import sys

MARKERS = ("game_initialize_for_new_map", "network_game_set_random_seed")
FORK = "FUN_001a4c50"          # the probe site inside the unported turn fork
MIN_SEGMENT = 50               # records; below this a segment carries no signal


def as_float(bits):
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def segments(records):
    """Yield (start, stop) for each map instance, skipping trivial ones."""
    cuts = [0] + [i for i, r in enumerate(records) if r["kind"] in MARKERS]
    cuts.append(len(records))
    for lo, hi in zip(cuts, cuts[1:]):
        if hi - lo > MIN_SEGMENT:
            yield lo, hi


def report(path):
    raw = json.load(open(path))
    records = raw["records"]
    print("=== %s  records=%d write_index=%s wrapped=%s"
          % (path, len(records), raw.get("write_index"), raw.get("wrapped")))
    for lo, hi in segments(records):
        seg = records[lo:hi]
        ticks = [r["tick"] for r in seg]
        # Count ONLY the binary fork probe.  A patched build also emits kind 19
        # from the source-level probe in the ported caller; counting both makes
        # the fork look like it runs twice as often as it does.
        entries = collections.defaultdict(collections.Counter)
        cosines = collections.defaultdict(collections.Counter)
        for r in seg:
            if r["kind"] == "probe:turn_gates" and r["caller"] == FORK:
                entries[r["caller2_addr"]][r["tick"]] += 1
            elif r["kind"] == "probe:turn_cosine":
                cosines[r["caller2_addr"]]["%.6f" % as_float(r["seed_before"])] += 1
        if not entries:
            continue
        print("  segment [%d:%d]  ticks %d..%d" % (lo, hi, min(ticks), max(ticks)))
        for unit in sorted(entries, key=lambda u: -sum(entries[u].values())):
            rate = collections.Counter(entries[unit].values())
            hist = cosines[unit]
            pinned = "PINNED" if len(hist) == 1 else "varies"
            print("    0x%08x  ticks=%-4d entries/tick=%-14s cos: %s %-3d %s"
                  % (unit, len(entries[unit]), dict(rate), pinned, len(hist),
                     hist.most_common(3)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("captures", nargs="+")
    args = ap.parse_args()
    for path in args.captures:
        report(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
