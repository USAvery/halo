#!/usr/bin/env python3
"""Find the first tick where two rng_trace captures disagree.

`rng_trace_dump.py --diff` aligns the two rings at record 0, which is wrong
whenever the rings hold different numbers of map instances: it reports a
divergence at index 0 and prints two unrelated windows.  This tool splits both
captures into map instances first, picks the segment pair to compare, and then
walks them tick by tick.

Two comparisons are made per tick:

  draws   the seed-advancing records only.  A difference here is the desync
          itself, because the lockstep check hashes the shared seed.
  states  `probe:unit_state` (kind 16) per unit handle.  A difference here says
          WHICH unit took a different animation state, which is what a draw
          difference usually follows from.

Probe kinds that exist on only one build (the source-level ones) are ignored,
so a patched build can be compared against a probed original.

    python3 tools/xbox/rng_first_divergence.py client.json host.json
"""
import argparse
import collections
import json

MARKERS = ("game_initialize_for_new_map", "network_game_set_random_seed")
MIN_SEGMENT = 50
SEED_KINDS = frozenset((
    "random_math_real", "random_real_range", "random_range",
    "random_direction3d", "random_seed_get_direction3d",
    "seed_random_orientation", "random_seed_step",
))


def load(path):
    with open(path) as handle:
        return json.load(handle)["records"]


def segments(records):
    cuts = [0] + [i for i, r in enumerate(records) if r["kind"] in MARKERS]
    cuts.append(len(records))
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b - a > MIN_SEGMENT]


def last_segment(records):
    """The newest map instance.  A ring keeps the oldest instance truncated,
    so the last complete segment is the one both machines still agree on."""
    segs = segments(records)
    if not segs:
        raise SystemExit("no map-instance segment found")
    return segs[-1]


def by_tick(records, keep):
    out = collections.defaultdict(list)
    for r in records:
        if keep(r):
            out[r["tick"]].append(r)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", help="capture A (usually our client)")
    ap.add_argument("b", help="capture B (usually the pristine host)")
    ap.add_argument("--segment-a", type=int, nargs=2, metavar=("LO", "HI"))
    ap.add_argument("--segment-b", type=int, nargs=2, metavar=("LO", "HI"))
    ap.add_argument("--context", type=int, default=2,
                    help="ticks of context to print after the divergence")
    args = ap.parse_args()

    ra, rb = load(args.a), load(args.b)
    la, ha = args.segment_a or last_segment(ra)
    lb, hb = args.segment_b or last_segment(rb)
    sa, sb = ra[la:ha], rb[lb:hb]
    print("A %s segment [%d:%d]" % (args.a, la, ha))
    print("B %s segment [%d:%d]" % (args.b, lb, hb))

    da = by_tick(sa, lambda r: r["kind"] in SEED_KINDS)
    db = by_tick(sb, lambda r: r["kind"] in SEED_KINDS)
    ua = by_tick(sa, lambda r: r["kind"] == "probe:unit_state")
    ub = by_tick(sb, lambda r: r["kind"] == "probe:unit_state")

    def draw_key(r):
        return (r["kind"], r["caller"], r["seed_before"])

    def state_map(rows):
        return {r.get("caller2_addr", 0): r["seed_before"] for r in rows}

    first = None
    for tick in sorted(set(da) | set(db) | set(ua) | set(ub)):
        if [draw_key(r) for r in da.get(tick, [])] != \
           [draw_key(r) for r in db.get(tick, [])]:
            first = tick
            break
    if first is None:
        print("\nno draw divergence in the compared segments")
        return 0

    print("\nfirst diverging tick: %d" % first)
    for tick in range(first, first + 1 + args.context):
        ma, mb = state_map(ua.get(tick, [])), state_map(ub.get(tick, []))
        print("\n  tick %d" % tick)
        for unit in sorted(set(ma) | set(mb)):
            va, vb = ma.get(unit), mb.get(unit)
            flag = "  " if va == vb else "!!"
            print("    %s unit 0x%08x  A state %-8s B state %s" % (
                flag, unit,
                "0x%04x" % va if va is not None else "-",
                "0x%04x" % vb if vb is not None else "-"))
        rows_a = [draw_key(r) for r in da.get(tick, [])]
        rows_b = [draw_key(r) for r in db.get(tick, [])]
        for i in range(max(len(rows_a), len(rows_b))):
            x = rows_a[i] if i < len(rows_a) else None
            y = rows_b[i] if i < len(rows_b) else None
            flag = "  " if x == y else "!!"
            fmt = lambda v: "%s %s 0x%08x" % v if v else "-"
            print("    %s draw  %-46s | %s" % (flag, fmt(x), fmt(y)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
