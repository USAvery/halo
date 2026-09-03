#!/usr/bin/env python3
"""Propose CEA names for unnamed NTSC (FUN_) functions by anchored
source-order window matching -- step 3c, an independent lane from
cea_propagate_names.py's call-graph propagation (step 3b).

Background: step 3b's call-graph propagation anchors on functions we already
trust by name on both sides ("seeds": an NTSC address whose kb.json name
equals a CEA corpus function name, either because the names always matched
or because a rename_mapping.json row already applied that name) and asks
which unmatched CEA function shares the most seed-named call-graph
neighbors with an unnamed target. That only works when the target has
enough seeded callees/callers to score -- most FUN_ functions don't, so 3b
yielded just 24 names.

This module uses a different, complementary signal: link order. Within one
translation unit both the NTSC (Xbox) and CEA (PC) compilers emit functions
in source order. So between two anchored (seed) functions that sit in the
*same* NTSC object and whose CEA source locations fall in the *same* CEA
source file, the unmatched NTSC functions strictly between them (by
address) correspond, in order, to the CEA functions whose PDB line ranges
fall strictly between the anchors' line ranges in that file. A prior
campaign (see skill `naming-confidence`, `apply_punpckhdq_renames.py`)
already proved anchor-bounded windows work for this kind of order
correspondence and that unanchored whole-file size matching does not (it
scored 0/74 before being rewritten to anchor-bound); this module applies
the same discipline to CEA PDB line ranges instead of TU function lists.

Key design points:

* Anchors need a resolved CEA source location (src_file, line_start), not
  just a name. rename_mapping.json rows carry this directly in
  evidence.cea_src / evidence.cea_lines. Plain name-equality seeds (kb name
  == a cea_procs.json name) don't carry a location, and cea_procs.json is
  not name-unique (8,679 records, 7,877 unique names) -- some names recur
  across files (typically LPROC statics). Those are resolved in two rounds:
  first every seed whose name is unique across cea_procs.json, then every
  remaining seed whose candidate records can be narrowed to exactly one by
  the CEA source files already known (from round 1 and from
  rename_mapping's direct evidence) to feed that seed's kb object. A seed
  that stays ambiguous after both rounds is still a seed (its name is still
  claimed, so it can never be proposed for a different address) but is
  never used as a window anchor.

* Windows are walked per kb object, over *consecutive* anchors in address
  order (not all pairs) -- exactly the punpckhdq precedent's anchor-bounded
  discipline. A kb object can draw seeds from more than one CEA source file
  (many-to-one is expected -- e.g. the "<common>" bucket, or a kb object
  that aggregates more than one compiland); when two consecutive anchors
  disagree on CEA src_file, that pair straddles a TU boundary we can't see
  and is skipped rather than guessed at.

* A window is only trustworthy if every NTSC slot inside it is actually
  unnamed. If some other, already fully-named (non-FUN_) function happens
  to sit between the two anchors -- almost always a seed that turned out
  ambiguous and so was never itself promoted to an anchor -- the position
  correspondence the whole method relies on is no longer provable, so the
  window is dropped rather than silently skipping that slot and drifting
  the alignment. (In the holdout evaluation, a held-out seed's real name is
  hidden from this check, exactly as if it were unnamed, because that is
  the scenario the holdout is meant to measure.)

* Equal-count windows are paired strictly by position -- no size involved,
  per the punpckhdq precedent. Unequal counts are either skipped
  (--strict, the "equal-count-only" holdout setting) or resolved, if
  possible, as an order-preserving subsequence match of the smaller side
  into the larger side gated by NTSC-size / CEA-len ratio (--relaxed, the
  "relaxed alignment" holdout setting); if no such subsequence covers every
  element of the smaller side, the whole window is left ambiguous.

* Secondary evidence, recorded on every proposal regardless of tier:
  callee containment (does the candidate's CEA callee list -- from
  artifacts/cea_corpus/index.json -- contain the CEA names of the target's
  own seed callees), NTSC-size-vs-CEA-len ratio, and CEA/NTSC string
  overlap. tier is 'high_confidence' only when the window was an
  equal-count match, no seed callee is missing from the candidate's CEA
  callee list, and the size ratio is within [0.4, 2.5]; otherwise
  'probable'. Note this containment check is "vacuously fine" when the
  target has zero seed callees to check (there is nothing to contradict),
  unlike cea_propagate_names.py's stricter containment>=1.0 gate -- here
  window position, not callee containment, is what does the accepting.

Usage:
    tools/analysis/cea_window_match.py holdout [options]
    tools/analysis/cea_window_match.py propose [options]

Reuses cea_propagate_names.py's KbView, rename_mapping/callgraph/CEA-index
loaders, and CEA call-graph inversion directly (import, not reimplementation
-- see build_seeds() below for the one helper that needed a different seed
definition, and why).
"""

import argparse
import bisect
import json
import os
import random
import sys
from collections import defaultdict

_ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.abspath(os.path.join(_ANALYSIS_DIR, '..', '..'))
if _ANALYSIS_DIR not in sys.path:
    sys.path.insert(0, _ANALYSIS_DIR)

import cea_propagate_names as cpn  # noqa: E402

DEFAULT_CEA_PROCS = os.path.join(
    _ROOT_DIR, 'artifacts', 'ghidra_groom', 'cea_corpus', 'cea_procs.json')
DEFAULT_PRIOR_PROPOSALS = os.path.join(
    _ROOT_DIR, 'artifacts', 'cea_propagation', 'proposals.json')
DEFAULT_OUT = os.path.join(
    _ROOT_DIR, 'artifacts', 'cea_propagation', 'window_proposals.json')

FUNCTION_KINDS = {'GPROC', 'LPROC'}  # both are real procedures in this PDB
DEFAULT_SIZE_LO = 0.4
DEFAULT_SIZE_HI = 2.5


# ---------------------------------------------------------------------------
# cea_procs.json loading/indexing
# ---------------------------------------------------------------------------

def load_cea_procs(path):
    with open(path) as f:
        procs = json.load(f)
    for p in procs:
        try:
            p['len_int'] = int(p['len'], 16) if p.get('len') else None
        except ValueError:
            p['len_int'] = None
    return procs


def index_cea_procs(procs):
    """Returns (by_name, by_src_file, src_line_starts, non_function_kinds).

    by_name: name -> list of proc records (not unique -- 8,679 records,
    7,877 unique names).
    by_src_file: src_file -> list of proc records sorted by line_start.
    src_line_starts: src_file -> parallel list of line_start ints, for
    bisecting a (line_lo, line_hi) window.
    """
    by_name = defaultdict(list)
    by_src_file = defaultdict(list)
    non_function_kinds = 0
    for p in procs:
        kind = p.get('kind')
        if kind and kind not in FUNCTION_KINDS:
            non_function_kinds += 1
            continue
        by_name[p['name']].append(p)
        by_src_file[p['src_file']].append(p)
    for lst in by_src_file.values():
        lst.sort(key=lambda r: r['line_start'])
    src_line_starts = {src: [r['line_start'] for r in lst] for src, lst in by_src_file.items()}
    return dict(by_name), dict(by_src_file), src_line_starts, non_function_kinds


def cea_gap(by_src_file, src_line_starts, src_file, line_lo, line_hi, excluded_names):
    """Procs in src_file with line_start strictly between line_lo and
    line_hi, ordered by line, excluding any name already claimed."""
    lst = by_src_file.get(src_file, [])
    starts = src_line_starts.get(src_file, [])
    lo = bisect.bisect_right(starts, line_lo)
    hi = bisect.bisect_left(starts, line_hi)
    return [p for p in lst[lo:hi] if p['name'] not in excluded_names]


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------

def build_seeds(kb, procs_by_name, rename_rows):
    """Seed definition for this module: kb functions whose name equals a
    cea_procs.json name exactly, plus every *applied* rename_mapping row
    (checked empirically against kb's live name at that address, same as
    cea_propagate_names.py) -- regardless of whether that row's name also
    happens to appear in cea_procs.json (a handful, e.g. libtiff names
    matched via punpckhdq_only, never will).

    This can't just call cea_propagate_names.build_seeds(): that function's
    clause 2 only adds a rename_mapping row as a seed when the applied name
    is *also* found in its cea_by_name argument -- correct for the CEA
    *corpus* (index.json) it was written against, but wrong here, where an
    applied rename is a seed in its own right (its identity is already
    trusted) even when cea_procs.json's PDB dump doesn't happen to carry a
    record with that name.
    """
    seeds = {}
    clause1 = 0
    for addr, name in kb.name_by_addr.items():
        if name in procs_by_name:
            seeds[addr] = name
            clause1 += 1

    applied = 0
    stale = 0
    clause2_new = 0
    for row in rename_rows:
        addr = int(row['addr'], 16)
        cur_name = kb.name_by_addr.get(addr)
        if cur_name is not None and cur_name == row['new_name']:
            applied += 1
            if addr not in seeds:
                seeds[addr] = cur_name
                clause2_new += 1
        else:
            stale += 1

    stats = {
        'clause1_name_equals_cea_procs': clause1,
        'rename_mapping_rows': len(rename_rows),
        'rename_mapping_applied': applied,
        'rename_mapping_stale': stale,
        'clause2_net_new_seeds': clause2_new,
        'total_seeds': len(seeds),
    }
    return seeds, stats


def build_anchor_info(kb, seeds, procs_by_name, rename_rows):
    """Resolve a (src_file, line_start, line_end) for as many seeds as
    possible. Returns (anchor_info, obj_src_files, stats).

    anchor_info: addr -> {'src_file', 'line_start', 'line_end', 'source'}
    for seeds that got a definite location -- these, and only these, can
    serve as window anchors.
    obj_src_files: kb_object -> set of CEA src_files its resolved seeds
    come from (many-to-one is expected).
    """
    # Round 0: rename_mapping evidence gives an exact location directly,
    # no ambiguity possible -- but only for rows that are actually applied.
    # A stale row (kb's live name at that address no longer matches the
    # row's new_name) describes a *different* CEA function than the one
    # now sitting at that address; grounding on it would silently misplace
    # the anchor by a function or two, sliding every gap boundary it
    # touches. build_seeds already computed applied-ness the same way; we
    # redo the same check here rather than trust the row on its own.
    #
    # Line convention: procs_by_name (line_start/line_end from the PDB
    # dump) is what round 1 and round 2 anchors use below, so grounded
    # anchors adopt the same convention -- use evidence.cea_src to pick
    # the matching cea_procs.json record and take its line_start/line_end,
    # rather than evidence.cea_lines (a possibly different line-numbering
    # convention recorded by whatever earlier tool produced rename_mapping
    # rows). Fall back to cea_lines only when no matching record exists.
    grounded = {}
    for row in rename_rows:
        addr = int(row['addr'], 16)
        if addr not in seeds:
            continue
        cur_name = kb.name_by_addr.get(addr)
        if cur_name is None or cur_name != row['new_name']:
            continue  # stale row: not actually applied at this address
        ev = row.get('evidence') or {}
        cea_src = ev.get('cea_src')
        cea_lines = ev.get('cea_lines')
        if not cea_src:
            continue
        rec = None
        for p in procs_by_name.get(cur_name, ()):
            if p['src_file'] == cea_src:
                rec = p
                break
        if rec is not None:
            grounded[addr] = {
                'src_file': rec['src_file'],
                'line_start': rec['line_start'],
                'line_end': rec['line_end'],
                'source': 'rename_mapping',
            }
        elif cea_lines:
            grounded[addr] = {
                'src_file': cea_src,
                'line_start': cea_lines[0],
                'line_end': cea_lines[-1],
                'source': 'rename_mapping_fallback',
            }

    obj_src_files = defaultdict(set)
    for addr, info in grounded.items():
        obj = kb.object_by_addr.get(addr)
        if obj is not None:
            obj_src_files[obj].add(info['src_file'])

    anchor_info = dict(grounded)
    no_cea_record = 0
    resolved_unique = 0

    # Round 1: seeds not yet located, whose name is unique across
    # cea_procs.json.
    remaining = {}
    for addr, name in seeds.items():
        if addr in anchor_info:
            continue
        cands = procs_by_name.get(name)
        if not cands:
            no_cea_record += 1
            continue
        if len(cands) == 1:
            p = cands[0]
            anchor_info[addr] = {
                'src_file': p['src_file'], 'line_start': p['line_start'],
                'line_end': p['line_end'], 'source': 'name_unique',
            }
            resolved_unique += 1
            obj = kb.object_by_addr.get(addr)
            if obj is not None:
                obj_src_files[obj].add(p['src_file'])
        else:
            remaining[addr] = (name, cands)

    # Round 2: narrow the still-ambiguous seeds using the CEA src_files
    # already known (from round 0 + round 1) to feed their kb object.
    resolved_by_object = 0
    unresolved_ambiguous = 0
    for addr, (name, cands) in remaining.items():
        obj = kb.object_by_addr.get(addr)
        known = obj_src_files.get(obj, set())
        filtered = [p for p in cands if p['src_file'] in known]
        if len(filtered) == 1:
            p = filtered[0]
            anchor_info[addr] = {
                'src_file': p['src_file'], 'line_start': p['line_start'],
                'line_end': p['line_end'], 'source': 'name_disambiguated',
            }
            resolved_by_object += 1
        else:
            unresolved_ambiguous += 1

    grounded_fallback = sum(
        1 for info in grounded.values()
        if info['source'] == 'rename_mapping_fallback'
    )
    stats = {
        'total_seeds': len(seeds),
        'grounded_from_rename_mapping': len(grounded),
        'grounded_from_rename_mapping_fallback': grounded_fallback,
        'resolved_unique_name': resolved_unique,
        'resolved_by_object_src_file': resolved_by_object,
        'unresolved_ambiguous': unresolved_ambiguous,
        'no_cea_procs_record': no_cea_record,
        'total_anchors_resolved': len(anchor_info),
    }
    return anchor_info, dict(obj_src_files), stats


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

def build_object_function_lists(kb):
    obj_funcs = defaultdict(list)
    for addr in kb.name_by_addr:
        obj_funcs[kb.object_by_addr.get(addr)].append(addr)
    for lst in obj_funcs.values():
        lst.sort()
    return dict(obj_funcs)


def build_windows(kb, obj_funcs, anchor_info, held_out_addrs=frozenset()):
    """Walk consecutive resolved anchors per kb object; emit a window for
    each adjacent pair that (a) shares a CEA src_file, (b) is in increasing
    source-line order (the core source-order assumption, checked rather
    than assumed), and (c) has no already-really-named function sitting in
    its NTSC gap (an unresolved-ambiguous seed, most likely -- see module
    docstring). held_out_addrs are treated as unnamed for check (c), which
    is what the holdout evaluation needs."""
    windows = []
    diff_src_skipped = 0
    non_monotonic_skipped = 0
    stray_named_skipped = 0

    def is_unnamed(a):
        return a in held_out_addrs or kb.name_by_addr[a].startswith('FUN_')

    for obj, addrs in obj_funcs.items():
        anchor_positions = [(i, a) for i, a in enumerate(addrs) if a in anchor_info]
        for k in range(len(anchor_positions) - 1):
            ia, addr_a = anchor_positions[k]
            ib, addr_b = anchor_positions[k + 1]
            info_a = anchor_info[addr_a]
            info_b = anchor_info[addr_b]
            if info_a['src_file'] != info_b['src_file']:
                diff_src_skipped += 1
                continue
            if info_b['line_start'] <= info_a['line_start']:
                non_monotonic_skipped += 1
                continue
            between = addrs[ia + 1:ib]
            if not between:
                continue
            if any(not is_unnamed(a) for a in between):
                stray_named_skipped += 1
                continue
            windows.append({
                'kb_object': obj,
                'src_file': info_a['src_file'],
                'addr_a': addr_a, 'addr_b': addr_b,
                'line_a': info_a['line_start'], 'line_b': info_b['line_start'],
                'ntsc_gap_addrs': between,
            })

    return windows, {
        'skipped_diff_src_file': diff_src_skipped,
        'skipped_non_monotonic_lines': non_monotonic_skipped,
        'skipped_stray_named_in_gap': stray_named_skipped,
    }


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------

def load_callgraph_with_ends(path):
    """cea_propagate_names.load_callgraph() intentionally strips every field
    it doesn't need, including 'end' -- this module needs it for NTSC size
    (end - addr), so read the raw file once more and merge it in."""
    ntsc_func, ntsc_callers = cpn.load_callgraph(path)
    with open(path) as f:
        raw = json.load(f)
    for hexaddr, rec in raw['functions'].items():
        addr = int(hexaddr, 16)
        end = rec.get('end')
        if addr in ntsc_func and end:
            ntsc_func[addr]['end'] = end
    return ntsc_func, ntsc_callers


def ntsc_size(ntsc_func, addr):
    f = ntsc_func.get(addr)
    if not f or not f.get('end'):
        return None
    return int(f['end'], 16) - addr


def align_subsequence(ntsc_gap, cea_gap_list, ntsc_size_of, size_lo, size_hi):
    """Order-preserving subsequence match of the smaller side into the
    larger side, gated by NTSC-size/CEA-len ratio. Returns a list of
    (addr, proc) pairs covering every element of the smaller side, or None
    if no such covering exists (window stays ambiguous)."""
    if not ntsc_gap or not cea_gap_list:
        return None
    ntsc_is_bigger = len(ntsc_gap) >= len(cea_gap_list)
    bigger = ntsc_gap if ntsc_is_bigger else cea_gap_list
    smaller = cea_gap_list if ntsc_is_bigger else ntsc_gap

    pairs = []
    bi = 0
    for s_item in smaller:
        matched = None
        while bi < len(bigger):
            b_item = bigger[bi]
            bi += 1
            addr, proc = (b_item, s_item) if ntsc_is_bigger else (s_item, b_item)
            sz = ntsc_size_of(addr)
            cea_len = proc.get('len_int')
            ratio = (sz / cea_len) if (sz and cea_len) else None
            if ratio is not None and size_lo <= ratio <= size_hi:
                matched = (addr, proc)
                break
        if matched is None:
            return None
        pairs.append(matched)
    return pairs


def process_window_pairs(win, cgap, ntsc_func, strict, size_lo, size_hi):
    """Returns (pairs, status) where pairs is a list of (addr, proc,
    alignment) and status is one of 'ok', 'empty', 'ambiguous_count_mismatch',
    'ambiguous_unresolved_subsequence'."""
    ngap = win['ntsc_gap_addrs']
    if not ngap or not cgap:
        return [], 'empty'
    if len(ngap) == len(cgap):
        return [(a, p, 'equal') for a, p in zip(ngap, cgap)], 'ok'
    if strict:
        return [], 'ambiguous_count_mismatch'
    aligned = align_subsequence(ngap, cgap, lambda a: ntsc_size(ntsc_func, a), size_lo, size_hi)
    if aligned is None:
        return [], 'ambiguous_unresolved_subsequence'
    return [(a, p, 'subsequence') for a, p in aligned], 'ok'


# ---------------------------------------------------------------------------
# Scoring / evidence
# ---------------------------------------------------------------------------

def compute_containment(addr, candidate_name, ntsc_func, seeds, cea_callees):
    """callee containment as in step 3b: of the target's callees that are
    themselves seeds, how many of their CEA names show up in the
    candidate's own CEA callee list. contradicts is True only when there is
    at least one seed callee AND the candidate is missing at least one --
    zero seed callees is not a contradiction, just no evidence."""
    f = ntsc_func.get(addr)
    if f is None:
        return 0, 0, False
    seed_callee_names = [seeds[c] for c in f['callees'] if c in seeds]
    total = len(seed_callee_names)
    cand_callees = cea_callees.get(candidate_name, set())
    hit = sum(1 for n in seed_callee_names if n in cand_callees)
    contradicts = total > 0 and hit < total
    return hit, total, contradicts


def build_evidence_row(addr, proc, win, alignment, window_cea_count, kb, ntsc_func,
                        cea_index_by_name, cea_callees, seeds, rename_addrs):
    f = ntsc_func.get(addr, {})
    sz = ntsc_size(ntsc_func, addr)
    cea_len = proc.get('len_int')
    ratio = (sz / cea_len) if (sz and cea_len) else None
    hit, total, contradicts = compute_containment(addr, proc['name'], ntsc_func, seeds, cea_callees)
    cand_idx = cea_index_by_name.get(proc['name'], {})
    shared_strings = sorted(set(f.get('strings', [])) & set(cand_idx.get('strings', [])))

    ratio_ok = ratio is not None and DEFAULT_SIZE_LO <= ratio <= DEFAULT_SIZE_HI
    tier = 'high_confidence' if (alignment == 'equal' and not contradicts and ratio_ok) else 'probable'

    return {
        'addr': f'{addr:08x}',
        'old_name': kb.name_by_addr.get(addr, f'FUN_{addr:08x}'),
        'new_name': proc['name'],
        'tier': tier,
        'method': 'anchored_window',
        'full_containment': not contradicts,
        'evidence': {
            'kb_object': kb.object_by_addr.get(addr),
            'src_file': win['src_file'],
            'window_anchor_a': f"{win['addr_a']:08x}",
            'window_anchor_b': f"{win['addr_b']:08x}",
            'window_ntsc_count': len(win['ntsc_gap_addrs']),
            'window_cea_count': window_cea_count,
            'alignment': alignment,
            'cea_line_start': proc['line_start'],
            'cea_line_end': proc['line_end'],
            'cea_len': cea_len,
            'ntsc_size': sz,
            'size_ratio': ratio,
            'callee_anchor_hit': hit,
            'callee_anchor_total': total,
            'contradicts_callee_containment': contradicts,
            'shared_strings': shared_strings,
        },
        'dup_in_mapping': addr in rename_addrs,
    }


def strength_key(row):
    ev = row['evidence']
    ratio = ev['size_ratio']
    return (
        row['tier'] == 'high_confidence',
        ev['alignment'] == 'equal',
        ev['callee_anchor_hit'] - (ev['callee_anchor_total'] - ev['callee_anchor_hit']),
        -abs((ratio - 1.0)) if ratio else -999.0,
        len(ev['shared_strings']),
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_match(kb, seeds, procs_by_name, by_src_file, src_line_starts, ntsc_func,
              cea_index_by_name, cea_callees, rename_rows, strict, size_lo, size_hi,
              held_out=None, base_excluded_names=None):
    held_out = held_out or {}
    held_out_addrs = set(held_out.keys())
    held_out_names = set(held_out.values())

    anchor_info, obj_src_files, anchor_stats = build_anchor_info(kb, seeds, procs_by_name, rename_rows)
    obj_funcs = build_object_function_lists(kb)
    windows, window_skip_stats = build_windows(kb, obj_funcs, anchor_info, held_out_addrs)
    windows.sort(key=lambda w: (w['kb_object'] or '', w['addr_a']))

    claimed_names = set(kb.all_function_names) - held_out_names
    claimed_names |= set(seeds.values())
    if base_excluded_names:
        claimed_names |= set(base_excluded_names)

    rename_addrs = set(int(r['addr'], 16) for r in rename_rows)

    window_stats = defaultdict(int)
    window_stats.update(window_skip_stats)
    window_stats['total_candidate_windows'] = len(windows)

    proposals = []
    for win in windows:
        cgap = cea_gap(by_src_file, src_line_starts, win['src_file'],
                        win['line_a'], win['line_b'], claimed_names)
        pairs, status = process_window_pairs(win, cgap, ntsc_func, strict, size_lo, size_hi)
        window_stats[status] += 1
        if status != 'ok':
            continue
        for addr, proc, alignment in pairs:
            if proc['name'] in claimed_names:
                continue  # already claimed by an earlier window this run
            claimed_names.add(proc['name'])
            proposals.append(build_evidence_row(
                addr, proc, win, alignment, len(cgap), kb, ntsc_func,
                cea_index_by_name, cea_callees, seeds, rename_addrs))

    return proposals, anchor_stats, dict(window_stats)


# ---------------------------------------------------------------------------
# holdout
# ---------------------------------------------------------------------------

def run_holdout_once(kb, all_seeds, procs_by_name, by_src_file, src_line_starts, ntsc_func,
                      cea_index_by_name, cea_callees, rename_rows,
                      holdout_frac, rng_seed, strict, size_lo, size_hi, confusion_n=10):
    rng = random.Random(rng_seed)
    addrs = sorted(all_seeds.keys())
    rng.shuffle(addrs)
    n_holdout = int(len(addrs) * holdout_frac)
    holdout_addrs = set(addrs[:n_holdout])
    train_seeds = {a: n for a, n in all_seeds.items() if a not in holdout_addrs}
    holdout_truth = {a: all_seeds[a] for a in holdout_addrs}

    # Held-out rows must not leak their ground-truth location either.
    train_rename_rows = [r for r in rename_rows if int(r['addr'], 16) not in holdout_addrs]

    proposals, anchor_stats, window_stats = run_match(
        kb, train_seeds, procs_by_name, by_src_file, src_line_starts, ntsc_func,
        cea_index_by_name, cea_callees, train_rename_rows, strict, size_lo, size_hi,
        held_out=holdout_truth)

    proposal_by_addr = {int(r['addr'], 16): r for r in proposals}
    tp = fp = 0
    confusions = []
    for addr, true_name in holdout_truth.items():
        p = proposal_by_addr.get(addr)
        if p is None:
            continue
        if p['new_name'] == true_name:
            tp += 1
        else:
            fp += 1
            confusions.append({
                'addr': f'{addr:08x}', 'true_name': true_name,
                'predicted_name': p['new_name'], 'tier': p['tier'],
            })

    total_predictions = tp + fp
    precision = (tp / total_predictions) if total_predictions else None
    recall = tp / len(holdout_truth) if holdout_truth else None
    return {
        'holdout_size': len(holdout_truth),
        'train_seeds': len(train_seeds),
        'predictions_made': total_predictions,
        'true_positives': tp,
        'false_positives': fp,
        'precision': precision,
        'recall': recall,
        'confusions': confusions[:confusion_n],
        'anchor_stats': anchor_stats,
        'window_stats': window_stats,
    }


def cmd_holdout(args):
    kb = cpn.KbView()
    procs = load_cea_procs(args.cea_procs)
    procs_by_name, by_src_file, src_line_starts, non_func_kinds = index_cea_procs(procs)
    cea_index_by_name = cpn.load_cea_index(args.cea_index)
    cea_callees, cea_callers = cpn.build_cea_graph(cea_index_by_name)
    rename_rows = cpn.load_rename_mapping(args.rename_mapping)
    ntsc_func, ntsc_callers = load_callgraph_with_ends(args.callgraph)

    seeds, seed_stats = build_seeds(kb, procs_by_name, rename_rows)
    print('=== seeds ===')
    for k, v in seed_stats.items():
        print(f'  {k}: {v}')
    print(f'  non_function_kind_procs_excluded: {non_func_kinds}')

    settings = [('equal_count_only', True), ('relaxed_subsequence', False)]
    results = {}
    for label, strict in settings:
        result = run_holdout_once(
            kb, seeds, procs_by_name, by_src_file, src_line_starts, ntsc_func,
            cea_index_by_name, cea_callees, rename_rows,
            args.holdout_frac, args.rng_seed, strict, args.size_lo, args.size_hi,
            confusion_n=args.confusion_sample)
        results[label] = result
        prec, rec = result['precision'], result['recall']
        prec_s = f'{prec:.4f}' if prec is not None else 'n/a'
        rec_s = f'{rec:.4f}' if rec is not None else 'n/a'
        print(f'--- {label} ---')
        print(f'  anchor_stats: {result["anchor_stats"]}')
        print(f'  window_stats: {result["window_stats"]}')
        print(f'  holdout={result["holdout_size"]} train_seeds={result["train_seeds"]} '
              f'predictions={result["predictions_made"]} tp={result["true_positives"]} '
              f'fp={result["false_positives"]}')
        print(f'  precision={prec_s} recall={rec_s}')
        if result['confusions']:
            print(f'  wrong matches ({len(result["confusions"])} of {result["false_positives"]}):')
            for c in result['confusions']:
                print(f'    {c["addr"]}: predicted {c["predicted_name"]!r} but truth is '
                      f'{c["true_name"]!r} (tier={c["tier"]})')

    strict_prec = results['equal_count_only']['precision'] or 0.0
    relaxed_prec = results['relaxed_subsequence']['precision'] or 0.0
    print()
    if results['equal_count_only']['precision'] is not None and strict_prec >= 0.97:
        chosen = 'equal_count_only'
    elif results['relaxed_subsequence']['precision'] is not None and relaxed_prec >= 0.97:
        chosen = 'relaxed_subsequence'
    else:
        chosen = None
    if chosen:
        r = results[chosen]
        print(f'=== chosen setting: {chosen} (precision={r["precision"]:.4f}, '
              f'recall={r["recall"] if r["recall"] is None else round(r["recall"], 4)}) ===')
    else:
        best = 'equal_count_only' if strict_prec >= relaxed_prec else 'relaxed_subsequence'
        print(f'=== neither setting reaches 0.97 precision; best is {best} at '
              f'{results[best]["precision"]}; do not use for propose without revisiting ===')


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------

def second_round_estimate(kb, seeds, all_round1_proposals, procs_by_name, by_src_file,
                           src_line_starts, ntsc_func, cea_index_by_name, cea_callees,
                           rename_rows, strict, size_lo, size_hi):
    high = [r for r in all_round1_proposals if r['tier'] == 'high_confidence']
    round2_seeds = dict(seeds)
    for r in high:
        round2_seeds[int(r['addr'], 16)] = r['new_name']
    base_excluded = {r['new_name'] for r in all_round1_proposals}
    round1_addrs = {int(r['addr'], 16) for r in all_round1_proposals}

    round2_proposals, _, _ = run_match(
        kb, round2_seeds, procs_by_name, by_src_file, src_line_starts, ntsc_func,
        cea_index_by_name, cea_callees, rename_rows, strict, size_lo, size_hi,
        base_excluded_names=base_excluded)
    new_count = sum(1 for r in round2_proposals if int(r['addr'], 16) not in round1_addrs)
    return len(high), new_count


def cmd_propose(args):
    kb = cpn.KbView()
    procs = load_cea_procs(args.cea_procs)
    procs_by_name, by_src_file, src_line_starts, non_func_kinds = index_cea_procs(procs)
    cea_index_by_name = cpn.load_cea_index(args.cea_index)
    cea_callees, cea_callers = cpn.build_cea_graph(cea_index_by_name)
    rename_rows = cpn.load_rename_mapping(args.rename_mapping)
    ntsc_func, ntsc_callers = load_callgraph_with_ends(args.callgraph)

    seeds, seed_stats = build_seeds(kb, procs_by_name, rename_rows)
    print('=== seeds ===')
    for k, v in seed_stats.items():
        print(f'  {k}: {v}')
    print(f'  non_function_kind_procs_excluded: {non_func_kinds}')

    proposals, anchor_stats, window_stats = run_match(
        kb, seeds, procs_by_name, by_src_file, src_line_starts, ntsc_func,
        cea_index_by_name, cea_callees, rename_rows, args.strict, args.size_lo, args.size_hi)

    print('=== anchors ===')
    for k, v in anchor_stats.items():
        print(f'  {k}: {v}')
    print('=== windows ===')
    for k, v in window_stats.items():
        print(f'  {k}: {v}')

    prior = []
    if os.path.exists(args.prior_proposals):
        with open(args.prior_proposals) as f:
            prior = json.load(f)
    prior_by_addr = {int(r['addr'], 16): r['new_name'] for r in prior}
    prior_names = set(prior_by_addr.values())

    final = []
    skipped_kb_collision = 0
    skipped_prior_conflict = 0
    for r in proposals:
        addr = int(r['addr'], 16)
        name = r['new_name']
        if name in kb.all_function_names:
            skipped_kb_collision += 1
            continue
        if name in prior_names and prior_by_addr.get(addr) != name:
            skipped_prior_conflict += 1
            continue
        final.append(r)

    final.sort(key=strength_key, reverse=True)

    by_tier = defaultdict(int)
    for r in final:
        by_tier[r['tier']] += 1
    print('=== proposals ===')
    print(f'  raw_candidates: {len(proposals)}')
    print(f'  skipped_kb_collision: {skipped_kb_collision}')
    print(f'  skipped_prior_conflict_with_step3b: {skipped_prior_conflict}')
    print(f'  total: {len(final)}')
    for tier, count in sorted(by_tier.items()):
        print(f'  {tier}: {count}')

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w') as f:
        json.dump(final, f, indent=2)
    print(f'wrote {args.out}')

    def fmt(r):
        ev = r['evidence']
        ratio = f"{ev['size_ratio']:.2f}" if ev['size_ratio'] is not None else 'n/a'
        return (f'  {r["addr"]} {r["old_name"]} -> {r["new_name"]} [{r["tier"]}] '
                f'alignment={ev["alignment"]} window={ev["window_ntsc_count"]}/{ev["window_cea_count"]} '
                f'callee={ev["callee_anchor_hit"]}/{ev["callee_anchor_total"]} size_ratio={ratio} '
                f'strings={len(ev["shared_strings"])} src={ev["src_file"]}')

    print('=== ten strongest ===')
    for r in final[:10]:
        print(fmt(r))
    print('=== five weakest ===')
    for r in final[-5:]:
        print(fmt(r))

    if args.second_round_estimate:
        n_high, new_count = second_round_estimate(
            kb, seeds, final, procs_by_name, by_src_file, src_line_starts, ntsc_func,
            cea_index_by_name, cea_callees, rename_rows, args.strict, args.size_lo, args.size_hi)
        print('=== second-round estimate (not written) ===')
        print(f'  high_confidence rows used as new anchors: {n_high}')
        print(f'  additional rows a round 2 would add: {new_count}')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_common_args(p):
    p.add_argument('--cea-procs', default=DEFAULT_CEA_PROCS)
    p.add_argument('--cea-index', default=cpn.DEFAULT_CEA_INDEX)
    p.add_argument('--rename-mapping', default=cpn.DEFAULT_RENAME_MAPPING)
    p.add_argument('--callgraph', default=cpn.DEFAULT_CALLGRAPH)
    p.add_argument('--size-lo', type=float, default=DEFAULT_SIZE_LO)
    p.add_argument('--size-hi', type=float, default=DEFAULT_SIZE_HI)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    p_holdout = sub.add_parser('holdout', help='Evaluate precision/recall on a held-out slice of seeds, both alignment settings.')
    add_common_args(p_holdout)
    p_holdout.add_argument('--holdout-frac', type=float, default=0.2)
    p_holdout.add_argument('--rng-seed', type=int, default=42)
    p_holdout.add_argument('--confusion-sample', type=int, default=10)
    p_holdout.set_defaults(func=cmd_holdout)

    p_propose = sub.add_parser('propose', help='Run with all seeds and emit window_proposals.json.')
    add_common_args(p_propose)
    p_propose.add_argument('--prior-proposals', default=DEFAULT_PRIOR_PROPOSALS)
    p_propose.add_argument('--out', default=DEFAULT_OUT)
    p_propose.add_argument('--strict', dest='strict', action='store_true', default=True,
                            help='equal-count windows only (default).')
    p_propose.add_argument('--relaxed', dest='strict', action='store_false',
                            help='also resolve unequal-count windows via size-gated subsequence alignment.')
    p_propose.add_argument('--second-round-estimate', dest='second_round_estimate', action='store_true', default=True)
    p_propose.add_argument('--no-second-round-estimate', dest='second_round_estimate', action='store_false')
    p_propose.set_defaults(func=cmd_propose)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
