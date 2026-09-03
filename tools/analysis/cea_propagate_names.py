#!/usr/bin/env python3
"""Propose CEA names for unnamed NTSC (FUN_) functions by call-graph neighborhood.

This does not compare source text. It anchors on functions we already trust
on both sides -- "seeds", NTSC addresses whose current kb.json name is also
a CEA corpus function name, either because the two names were always
identical or because a rename_mapping.json row already applied that name --
and asks, for each unnamed FUN_, which unmatched CEA function shares the
most seed-named neighbors with it in the call graph.

Design notes (see the step-3b scoping note this implements):

* Object hard filter. cea_corpus/index.json's per-function "file" field
  turns out to be exactly "blam/" + name + ".c" for all 7,318 functions
  (verified empirically; also documented in cea_corpus_index.py's own
  docstring: "blam/ one function per .c file"). It carries zero grouping
  information beyond the function's own name, so a literal "CEA file maps
  to this kb object" lookup built from seeds could never match a
  not-yet-matched candidate's file (a file that, by definition, has never
  been observed as a seed). Instead this script builds the grouping from
  each CEA function's addr360: linked objects are laid out contiguously in
  the Xbox 360 binary, so sorting seeds by addr360 and looking at which kb
  object each seed's neighbors fall into is a genuine, empirically-checked
  many-to-one CEA-address-range -> NTSC-object mapping. A quick check
  (see docs; not shipped as code) found 88% of addr360-adjacent seed pairs
  share a kb object, and the 12% that disagree cluster tightly at real
  object-transition boundaries -- exactly what you would expect from a
  contiguous per-object layout, not from scattered noise. A candidate is
  placed in kb object O's window only when both its addr360 neighbors
  (the nearest seed below and above it) agree on O, or when it sits at the
  very edge of the seeded range and has only one neighbor. Disagreements
  are left unplaced (windowed pools only, never used as a hard filter by
  themselves). CEA functions with addr360 == null (~1,043, mostly the
  "_0".."_22" inline-duplicate families) can never be windowed and so only
  ever compete for kb objects that currently have zero seeds (see below).

* When a kb object has zero seeds, there is nothing to window against, so
  every not-yet-matched CEA function (windowed or not) is a legal
  candidate for every FUN_ in that object, per the design note's "or to no
  object yet if the object has no seeds" clause. Scoring precision +
  margin carries the load here, not the object filter.

* Anchored containment, not Jaccard. CEA callee lists include inlined and
  macro calls that the NTSC binary only partly emits as direct E8 calls,
  so raw-set Jaccard over full callee lists is not comparable across the
  two sides. Instead: of the NTSC target's callees (and, symmetrically,
  callers) that are themselves seeds, what fraction of their CEA names
  show up in the candidate's own callee (or inverted-caller) list. Anchors
  a candidate does not touch at all are cheap to enumerate: candidate X is
  reachable as a callee-side anchor hit for seed S exactly when S is in
  cea_callers[S']-style inversion, i.e. X in cea_callers[S] (X calls S);
  and reachable as a caller-side anchor hit for seed S exactly when
  S in cea_callees[X] (S is called by X), i.e. X in cea_callees[S]. So the
  full candidate set worth scoring for a target is the union, over the
  target's seed-callees and seed-callers, of cea_callers[seed] and
  cea_callees[seed] -- no need to score the whole corpus per target.

Usage:
    tools/analysis/cea_propagate_names.py holdout [options]
    tools/analysis/cea_propagate_names.py propose [options]

Both subcommands read the same three inputs (paths are overridable):
    artifacts/ntsc_callgraph/callgraph.json
    artifacts/cea_corpus/index.json
    artifacts/ghidra_groom/rename_mapping.json
and kb.json, via tools/analysis/knowledge.py's KnowledgeBase loader.
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

import knowledge  # noqa: E402

DEFAULT_CALLGRAPH = os.path.join(_ROOT_DIR, 'artifacts', 'ntsc_callgraph', 'callgraph.json')
DEFAULT_CEA_INDEX = os.path.join(_ROOT_DIR, 'artifacts', 'cea_corpus', 'index.json')
DEFAULT_RENAME_MAPPING = os.path.join(_ROOT_DIR, 'artifacts', 'ghidra_groom', 'rename_mapping.json')
DEFAULT_PROPOSALS_OUT = os.path.join(_ROOT_DIR, 'artifacts', 'cea_propagation', 'proposals.json')


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

class KbView:
    """Flattened views of kb.json needed by the matcher."""

    def __init__(self):
        kb = knowledge.KnowledgeBase.deserialize()
        self.name_by_addr = {}
        self.object_by_addr = {}
        self.data_name_by_addr = {}
        self.all_function_names = set()
        for s in kb.symbols:
            if not s.addr:
                continue
            obj = kb.symbol_to_object.get(s)
            if isinstance(s, knowledge.Function):
                name = s.name
                if name:
                    self.name_by_addr[s.addr] = name
                    self.object_by_addr[s.addr] = obj
                    self.all_function_names.add(name)
            elif isinstance(s, knowledge.Data):
                name = s.name
                if name:
                    self.data_name_by_addr[s.addr] = name
        self.fun_addrs = sorted(
            addr for addr, name in self.name_by_addr.items() if name.startswith('FUN_')
        )


def load_cea_index(path):
    with open(path) as f:
        data = json.load(f)
    by_name = {}
    for fn in data['functions']:
        by_name[fn['name']] = fn
    return by_name


def load_rename_mapping(path):
    with open(path) as f:
        return json.load(f)


def load_callgraph(path):
    with open(path) as f:
        data = json.load(f)
    functions = {}
    for hexaddr, rec in data['functions'].items():
        addr = int(hexaddr, 16)
        functions[addr] = {
            'name': rec.get('name'),
            'object': rec.get('object'),
            'callees': set(int(c, 16) for c in rec.get('callees', [])),
            'strings': set(rec.get('strings', [])),
            'data_refs': set(int(d, 16) for d in rec.get('data_refs', [])),
        }
    callers = {}
    for hexaddr, arr in data.get('callers', {}).items():
        callers[int(hexaddr, 16)] = set(int(c, 16) for c in arr)
    return functions, callers


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------

def build_seeds(kb, cea_by_name, rename_rows):
    """Return (seed_map, stats). seed_map: ntsc_addr(int) -> cea_name.

    Clause 1: kb's current name at an address equals a CEA function name
    exactly. Clause 2: a rename_mapping.json row whose new_name is the kb's
    *current* name at that address (i.e. actually applied -- old_name/
    new_name in the row itself can be stale relative to kb.json's live
    state, so "applied" is checked empirically, not trusted from the row).
    """
    seeds = {}
    clause1 = 0
    for addr, name in kb.name_by_addr.items():
        if name in cea_by_name:
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
            if cur_name in cea_by_name and addr not in seeds:
                seeds[addr] = cur_name
                clause2_new += 1
        else:
            stale += 1

    stats = {
        'clause1_seeds': clause1,
        'rename_mapping_rows': len(rename_rows),
        'rename_mapping_applied': applied,
        'rename_mapping_stale': stale,
        'clause2_net_new_seeds': clause2_new,
        'total_seeds': len(seeds),
    }
    return seeds, stats


# ---------------------------------------------------------------------------
# CEA call-graph inversion
# ---------------------------------------------------------------------------

def build_cea_graph(cea_by_name):
    """callees[name] = set(names it calls); callers[name] = set(names that call it)."""
    callees = {}
    callers = defaultdict(set)
    for name, rec in cea_by_name.items():
        c = set(rec.get('callees', []))
        callees[name] = c
        for callee in c:
            callers[callee].add(name)
    return callees, dict(callers)


# ---------------------------------------------------------------------------
# addr360 windowing
# ---------------------------------------------------------------------------

def build_addr360_window_index(seeds, cea_by_name, kb):
    """Sorted (addr360, kb_object) for seeds that have an addr360, for
    binary-searching which kb object a not-yet-matched CEA function's
    addr360 neighborhood belongs to."""
    rows = []
    for addr, name in seeds.items():
        rec = cea_by_name.get(name)
        if rec is None:
            continue
        a360 = rec.get('addr360')
        if a360 is None:
            continue
        obj = kb.object_by_addr.get(addr)
        if obj is None:
            continue
        rows.append((a360, obj))
    rows.sort(key=lambda r: r[0])
    addr360_list = [r[0] for r in rows]
    obj_list = [r[1] for r in rows]
    return addr360_list, obj_list


def window_object_for(addr360, addr360_list, obj_list):
    """kb object inferred for a CEA addr360 from its nearest seed neighbors,
    or None if the two neighbors disagree or there are no seeds at all."""
    if not addr360_list:
        return None
    i = bisect.bisect_left(addr360_list, addr360)
    floor_obj = obj_list[i - 1] if i > 0 else None
    ceil_obj = obj_list[i] if i < len(obj_list) else None
    if floor_obj is not None and ceil_obj is not None:
        return floor_obj if floor_obj == ceil_obj else None
    return floor_obj if floor_obj is not None else ceil_obj


def build_candidate_pools(seeds, cea_by_name, kb, excluded_names):
    """Returns (obj_pool, full_pool, objects_with_seeds).

    obj_pool[kb_object] = set of unmatched CEA names windowed to that object.
    full_pool = set of ALL unmatched CEA names (fallback for zero-seed objects).
    Both exclude seeds, owner_divergence-flagged functions, and names in
    excluded_names (already-taken kb function names)."""
    seed_names = set(seeds.values())
    unmatched = set()
    for name, rec in cea_by_name.items():
        if name in seed_names:
            continue
        if rec.get('owner_divergence'):
            continue
        if name in excluded_names:
            continue
        unmatched.add(name)

    addr360_list, obj_list = build_addr360_window_index(seeds, cea_by_name, kb)

    obj_pool = defaultdict(set)
    for name in unmatched:
        rec = cea_by_name[name]
        a360 = rec.get('addr360')
        if a360 is None:
            continue
        wobj = window_object_for(a360, addr360_list, obj_list)
        if wobj is not None:
            obj_pool[wobj].add(name)

    objects_with_seeds = set()
    for addr, name in seeds.items():
        obj = kb.object_by_addr.get(addr)
        if obj is not None:
            objects_with_seeds.add(obj)

    return dict(obj_pool), unmatched, objects_with_seeds


def candidate_pool_for_object(kb_object, obj_pool, full_pool, objects_with_seeds):
    if kb_object is not None and kb_object in objects_with_seeds:
        return obj_pool.get(kb_object, set())
    return full_pool


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def anchor_candidates(target_addr, ntsc_func, ntsc_callers, seeds, cea_callees, cea_callers, pool):
    """Union of CEA names reachable as a nonzero-anchor candidate for this
    target, intersected with pool. See module docstring for the identity
    this relies on (X is a callee-anchor hit for seed S iff X in
    cea_callers[S]; X is a caller-anchor hit for seed S iff X in
    cea_callees[S])."""
    f = ntsc_func.get(target_addr)
    if f is None:
        return set(), [], []
    callee_seed_names = [seeds[c] for c in f['callees'] if c in seeds]
    caller_addrs = ntsc_callers.get(target_addr, set())
    caller_seed_names = [seeds[c] for c in caller_addrs if c in seeds]

    candidates = set()
    for name in callee_seed_names:
        candidates |= cea_callers.get(name, set())
    for name in caller_seed_names:
        candidates |= cea_callees.get(name, set())
    return candidates & pool, callee_seed_names, caller_seed_names


def score_candidate(candidate, callee_seed_names, caller_seed_names,
                     cea_callees, cea_callers, f, kb, cea_by_name):
    cand_callees = cea_callees.get(candidate, set())
    cand_callers = cea_callers.get(candidate, set())

    callee_total = len(callee_seed_names)
    callee_hit = sum(1 for n in callee_seed_names if n in cand_callees)
    caller_total = len(caller_seed_names)
    caller_hit = sum(1 for n in caller_seed_names if n in cand_callers)

    anchors = callee_hit + caller_hit
    denom = callee_total + caller_total
    containment = (anchors / denom) if denom else 0.0

    cand_rec = cea_by_name.get(candidate, {})
    shared_strings = sorted(f['strings'] & set(cand_rec.get('strings', [])))
    cand_globals = set(cand_rec.get('globals', []))
    shared_globals = sorted(
        n for addr in f['data_refs']
        if (n := kb.data_name_by_addr.get(addr)) is not None and n in cand_globals
    )

    return {
        'candidate': candidate,
        'anchors': anchors,
        'containment': containment,
        'callee_anchor_hit': callee_hit,
        'callee_anchor_total': callee_total,
        'caller_anchor_hit': caller_hit,
        'caller_anchor_total': caller_total,
        'shared_strings': shared_strings,
        'shared_globals': shared_globals,
    }


def sort_key(result):
    return (result['containment'], result['anchors'],
            len(result['shared_strings']), len(result['shared_globals']))


def match_target(target_addr, ntsc_func, ntsc_callers, seeds, cea_callees, cea_callers,
                  cea_by_name, kb, obj_pool, full_pool, objects_with_seeds):
    """Return (best_result_or_None, runner_up_result_or_None)."""
    f = ntsc_func.get(target_addr)
    if f is None:
        return None, None
    kb_object = kb.object_by_addr.get(target_addr)
    pool = candidate_pool_for_object(kb_object, obj_pool, full_pool, objects_with_seeds)
    candidates, callee_seed_names, caller_seed_names = anchor_candidates(
        target_addr, ntsc_func, ntsc_callers, seeds, cea_callees, cea_callers, pool)
    if not candidates:
        return None, None
    results = [
        score_candidate(c, callee_seed_names, caller_seed_names, cea_callees, cea_callers, f, kb, cea_by_name)
        for c in candidates
    ]
    results.sort(key=sort_key, reverse=True)
    best = results[0]
    runner_up = results[1] if len(results) > 1 else None
    return best, runner_up


def accept(best, runner_up, min_anchors, min_containment, margin):
    if best is None:
        return False
    if best['anchors'] < min_anchors:
        return False
    if best['containment'] < min_containment:
        return False
    if runner_up is None or runner_up['anchors'] == 0:
        return True
    if runner_up['containment'] <= 0:
        return True  # runner-up is no-contest, same as zero anchors
    return best['containment'] >= margin * runner_up['containment']


# ---------------------------------------------------------------------------
# holdout
# ---------------------------------------------------------------------------

def run_holdout_once(kb, cea_by_name, all_seeds, ntsc_func, ntsc_callers,
                      holdout_frac, rng_seed, min_anchors, min_containment, margin,
                      confusion_n=10, verbose=False):
    rng = random.Random(rng_seed)
    addrs = sorted(all_seeds.keys())
    rng.shuffle(addrs)
    n_holdout = int(len(addrs) * holdout_frac)
    holdout_addrs = set(addrs[:n_holdout])
    train_seeds = {a: n for a, n in all_seeds.items() if a not in holdout_addrs}
    holdout_truth = {a: all_seeds[a] for a in holdout_addrs}

    held_out_names = set(holdout_truth.values())
    excluded_names = (kb.all_function_names - held_out_names)

    cea_callees, cea_callers = build_cea_graph(cea_by_name)
    obj_pool, full_pool, objects_with_seeds = build_candidate_pools(
        train_seeds, cea_by_name, kb, excluded_names)

    tp = 0
    fp = 0
    no_call = 0
    confusions = []
    for addr, true_name in holdout_truth.items():
        best, runner_up = match_target(
            addr, ntsc_func, ntsc_callers, train_seeds, cea_callees, cea_callers,
            cea_by_name, kb, obj_pool, full_pool, objects_with_seeds)
        if not accept(best, runner_up, min_anchors, min_containment, margin):
            no_call += 1
            continue
        predicted = best['candidate']
        if predicted == true_name:
            tp += 1
        else:
            fp += 1
            confusions.append({
                'addr': hex(addr),
                'true_name': true_name,
                'predicted_name': predicted,
                'containment': best['containment'],
                'anchors': best['anchors'],
                'runner_up': runner_up['candidate'] if runner_up else None,
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
        'no_call': no_call,
        'precision': precision,
        'recall': recall,
        'confusions': confusions[:confusion_n],
    }


def cmd_holdout(args):
    kb = KbView()
    cea_by_name = load_cea_index(args.cea_index)
    rename_rows = load_rename_mapping(args.rename_mapping)
    ntsc_func, ntsc_callers = load_callgraph(args.callgraph)
    all_seeds, seed_stats = build_seeds(kb, cea_by_name, rename_rows)

    print('=== seeds ===')
    for k, v in seed_stats.items():
        print(f'  {k}: {v}')

    grid = []
    if args.grid:
        for ma in (2, 3):
            for mc in (0.5, 0.6, 0.7, 0.8):
                for mg in (1.2, 1.5, 2.0):
                    grid.append((ma, mc, mg))
    else:
        grid = [(args.min_anchors, args.min_containment, args.margin)]

    best_setting = None
    for ma, mc, mg in grid:
        result = run_holdout_once(
            kb, cea_by_name, all_seeds, ntsc_func, ntsc_callers,
            args.holdout_frac, args.rng_seed, ma, mc, mg,
            confusion_n=args.confusion_sample)
        prec = result['precision']
        rec = result['recall']
        prec_s = f'{prec:.4f}' if prec is not None else 'n/a'
        rec_s = f'{rec:.4f}' if rec is not None else 'n/a'
        print(f'--- min_anchors={ma} min_containment={mc} margin={mg} ---')
        print(f'  holdout={result["holdout_size"]} predictions={result["predictions_made"]} '
              f'tp={result["true_positives"]} fp={result["false_positives"]} no_call={result["no_call"]}')
        print(f'  precision={prec_s} recall={rec_s}')
        if prec is not None and (best_setting is None or prec > best_setting[0]):
            best_setting = (prec, rec, ma, mc, mg, result)

    if best_setting:
        prec, rec, ma, mc, mg, result = best_setting
        print()
        print(f'=== best setting: min_anchors={ma} min_containment={mc} margin={mg} '
              f'-> precision={prec:.4f} recall={rec if rec is None else round(rec,4)} ===')
        if prec >= 0.97:
            print('meets 0.97 precision target')
        else:
            print(f'does NOT meet 0.97 precision target; best achievable is {prec:.4f}')
        print(f'confusion sample ({len(result["confusions"])} of {result["false_positives"]} false positives):')
        for c in result['confusions']:
            print(f'  {c["addr"]}: predicted {c["predicted_name"]!r} but truth is {c["true_name"]!r} '
                  f'(containment={c["containment"]:.3f}, anchors={c["anchors"]}, runner_up={c["runner_up"]!r})')


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------

def cmd_propose(args):
    kb = KbView()
    cea_by_name = load_cea_index(args.cea_index)
    rename_rows = load_rename_mapping(args.rename_mapping)
    ntsc_func, ntsc_callers = load_callgraph(args.callgraph)
    seeds, seed_stats = build_seeds(kb, cea_by_name, rename_rows)

    print('=== seeds ===')
    for k, v in seed_stats.items():
        print(f'  {k}: {v}')

    excluded_names = set(kb.all_function_names)
    cea_callees, cea_callers = build_cea_graph(cea_by_name)
    obj_pool, full_pool, objects_with_seeds = build_candidate_pools(
        seeds, cea_by_name, kb, excluded_names)

    existing_mapping_addrs = set(int(r['addr'], 16) for r in rename_rows)

    # Score every target first, then resolve same-name collisions by score
    # (best sort_key wins), not by address order. Two FUN_s can legitimately
    # anchor-match the same CEA name; the weaker one should lose the name,
    # not whichever happens to sit at a lower address.
    accepted = []
    for addr in kb.fun_addrs:
        best, runner_up = match_target(
            addr, ntsc_func, ntsc_callers, seeds, cea_callees, cea_callers,
            cea_by_name, kb, obj_pool, full_pool, objects_with_seeds)
        if not accept(best, runner_up, args.min_anchors, args.min_containment, args.margin):
            continue
        accepted.append((addr, best, runner_up))
    accepted.sort(key=lambda t: sort_key(t[1]), reverse=True)

    proposals = []
    used_names = set()
    for addr, best, runner_up in accepted:
        candidate = best['candidate']
        if candidate in used_names:
            # A better-scoring FUN_ already claimed this CEA name; never
            # emit the same new_name twice.
            continue
        used_names.add(candidate)
        tier = 'high_confidence' if (best['containment'] >= 1.0 and best['anchors'] >= 3) else 'probable'
        row = {
            'addr': f'{addr:08x}',
            'old_name': kb.name_by_addr[addr],
            'new_name': candidate,
            'tier': tier,
            'method': 'callgraph_propagation',
            'full_containment': best['containment'] >= 1.0,
            'evidence': {
                'kb_object': kb.object_by_addr.get(addr),
                'anchors': best['anchors'],
                'containment': best['containment'],
                'callee_anchor_hit': best['callee_anchor_hit'],
                'callee_anchor_total': best['callee_anchor_total'],
                'caller_anchor_hit': best['caller_anchor_hit'],
                'caller_anchor_total': best['caller_anchor_total'],
                'runner_up_name': runner_up['candidate'] if runner_up else None,
                'runner_up_score': runner_up['containment'] if runner_up else None,
                'runner_up_anchors': runner_up['anchors'] if runner_up else None,
                'shared_strings': best['shared_strings'],
                'shared_globals': best['shared_globals'],
            },
            'dup_in_mapping': addr in existing_mapping_addrs,
        }
        proposals.append(row)

    proposals.sort(key=lambda r: (r['evidence']['containment'], r['evidence']['anchors']), reverse=True)

    by_tier = defaultdict(int)
    for r in proposals:
        by_tier[r['tier']] += 1
    print('=== proposals ===')
    print(f'  total: {len(proposals)}')
    for tier, count in sorted(by_tier.items()):
        print(f'  {tier}: {count}')

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w') as f:
        json.dump(proposals, f, indent=2)
    print(f'wrote {args.out}')

    if args.second_round_estimate:
        round2_new_seeds = dict(seeds)
        for r in proposals:
            if r['tier'] == 'high_confidence':
                round2_new_seeds[int(r['addr'], 16)] = r['new_name']
        already_proposed_high = set(
            int(r['addr'], 16) for r in proposals if r['tier'] == 'high_confidence')
        excluded_names_2 = set(kb.all_function_names) | {
            r['new_name'] for r in proposals if r['tier'] == 'high_confidence'}
        obj_pool2, full_pool2, objects_with_seeds2 = build_candidate_pools(
            round2_new_seeds, cea_by_name, kb, excluded_names_2)
        round2_targets = [a for a in kb.fun_addrs if a not in already_proposed_high]
        round1_accepted = set(int(r['addr'], 16) for r in proposals)
        round2_new_accept = 0
        for addr in round2_targets:
            best, runner_up = match_target(
                addr, ntsc_func, ntsc_callers, round2_new_seeds, cea_callees, cea_callers,
                cea_by_name, kb, obj_pool2, full_pool2, objects_with_seeds2)
            if accept(best, runner_up, args.min_anchors, args.min_containment, args.margin):
                if addr not in round1_accepted:
                    round2_new_accept += 1
        print(f'=== second-round estimate (not written) ===')
        print(f'  new seeds added from round 1 high_confidence: {len(already_proposed_high)}')
        print(f'  additional accepted rows a round 2 would add: {round2_new_accept}')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_common_args(p):
    p.add_argument('--callgraph', default=DEFAULT_CALLGRAPH)
    p.add_argument('--cea-index', default=DEFAULT_CEA_INDEX)
    p.add_argument('--rename-mapping', default=DEFAULT_RENAME_MAPPING)
    p.add_argument('--min-anchors', type=int, default=3)
    p.add_argument('--min-containment', type=float, default=1.0)
    p.add_argument('--margin', type=float, default=1.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    p_holdout = sub.add_parser('holdout', help='Evaluate precision/recall on a held-out slice of seeds.')
    add_common_args(p_holdout)
    p_holdout.add_argument('--holdout-frac', type=float, default=0.2)
    p_holdout.add_argument('--rng-seed', type=int, default=42)
    p_holdout.add_argument('--confusion-sample', type=int, default=10)
    p_holdout.add_argument('--grid', action='store_true',
                            help='Sweep a small threshold grid and report each; ignores '
                                 '--min-anchors/--min-containment/--margin.')
    p_holdout.set_defaults(func=cmd_holdout)

    p_propose = sub.add_parser('propose', help='Run with all seeds and emit proposals.json.')
    add_common_args(p_propose)
    p_propose.add_argument('--out', default=DEFAULT_PROPOSALS_OUT)
    p_propose.add_argument('--second-round-estimate', action='store_true', default=True)
    p_propose.add_argument('--no-second-round-estimate', dest='second_round_estimate', action='store_false')
    p_propose.set_defaults(func=cmd_propose)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
