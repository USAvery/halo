#!/usr/bin/env python3
"""Propose CEA names for unnamed NTSC (FUN_) functions by exact string
reference matching -- an independent lane from cea_propagate_names.py's
call-graph propagation.

Core idea: a decoded string literal that occurs in exactly one CEA corpus
function is that function's fingerprint. If the same exact string also shows
up in exactly one NTSC FUN_ function's string list, that FUN_ almost
certainly IS the CEA function, because two independent compilers producing
the same literal byte-for-byte in the same spot is not a coincidence at
string lengths >= 6. This module never compares source text or does fuzzy
matching -- only exact decoded string equality.

Filters applied before a string is allowed to serve as evidence:
  * shorter than --min-string-len (default 6) -- too likely to recur by luck.
  * "format-only" -- stripped of printf-style %-conversions, fewer than 3
    alnum characters remain (e.g. "%s: %d", "%s/%s\n").
  * path-separator/filename-shaped (contains '/' or '\\', or is a bare
    "name.ext" token) -- these usually recur across every function compiled
    from the same source file, so they are dropped UNLESS the literal string
    is unique end-to-end: exactly one CEA function AND exactly one NTSC FUN_
    both carry it (--allow-unique-paths, default on).
  * boilerplate (currently: contains "assert" case-insensitively) -- same
    unique-end-to-end exception as paths (--allow-unique-boilerplate).
  * bare subsystem-noun strings -- a single-word string whose only relation
    to the CEA candidate name is "that word plus a generic verb suffix"
    (e.g. "widget" -> widgets_initialize, "encounter" -> encounters_initialize)
    is naming the subsystem, not the specific function; every sibling
    function in that family likely shares the literal, so corpus-side
    uniqueness there is an extraction artifact rather than a real
    fingerprint (--drop-subsystem-noun-strings, default on). A multi-word
    phrase (e.g. "first person weapons") is specific enough and is kept.

Primary rule: for a target FUN_, collect every qualifying CEA-unique string
in its callgraph string list; look up which CEA function owns each one. If
that yields more than one distinct CEA name, it's a conflict -- reject,
never guess between them. Exactly one distinct name is a candidate.

Secondary acceptance (all required regardless of string count):
  * the candidate CEA function is not owner_divergence-flagged.
  * the candidate CEA name is not already in use as a real (non-FUN_) kb.json
    function name.

Corroboration (seeded callee containment + addr360/kb-object neighborhood,
both borrowed from cea_propagate_names.py's seed graph):
  * candidate has >= 2 qualifying unique strings -> corroboration is
    recorded but NOT required (two independent exact-string hits already
    make a very strong case).
  * candidate has exactly 1 qualifying unique string -> corroboration IS
    required: either >=1 anchored seeded-callee/-caller hit, or strict
    addr360-window compatibility (the CEA candidate's neighborhood, from
    seeds on BOTH sides, agrees with the NTSC target's kb object).

Usage:
    tools/analysis/cea_string_match.py holdout [options]
    tools/analysis/cea_string_match.py propose [options]

Reads the same three corpora as cea_propagate_names.py (paths overridable):
    artifacts/ntsc_callgraph/callgraph.json
    artifacts/cea_corpus/index.json
    artifacts/ghidra_groom/rename_mapping.json
and kb.json via tools/analysis/knowledge.py, plus (propose only) any prior
callgraph/window proposal files, to avoid re-proposing an address another
lane already covered.
"""

import argparse
import json
import os
import random
import re
import sys
from collections import defaultdict

_ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.abspath(os.path.join(_ANALYSIS_DIR, '..', '..'))
if _ANALYSIS_DIR not in sys.path:
    sys.path.insert(0, _ANALYSIS_DIR)

import knowledge  # noqa: E402
import cea_propagate_names as cpn  # noqa: E402 -- reuse KbView/seed/graph/window conventions

DEFAULT_CALLGRAPH = os.path.join(_ROOT_DIR, 'artifacts', 'ntsc_callgraph', 'callgraph.json')
DEFAULT_CEA_INDEX = os.path.join(_ROOT_DIR, 'artifacts', 'cea_corpus', 'index.json')
DEFAULT_RENAME_MAPPING = os.path.join(_ROOT_DIR, 'artifacts', 'ghidra_groom', 'rename_mapping.json')
DEFAULT_CALLGRAPH_PROPOSALS = os.path.join(_ROOT_DIR, 'artifacts', 'cea_propagation', 'proposals.json')
DEFAULT_WINDOW_PROPOSALS = os.path.join(_ROOT_DIR, 'artifacts', 'cea_propagation', 'window_proposals.json')
DEFAULT_OUT = os.path.join(_ROOT_DIR, 'artifacts', 'cea_propagation', 'string_proposals.json')


# ---------------------------------------------------------------------------
# String eligibility filters
# ---------------------------------------------------------------------------

_FORMAT_SPEC_RE = re.compile(r'%[-+ 0#]*\d*(\.\d+)?[hlLqjzt]{0,2}[diouxXeEfFgGaAcspn%]')
_ALNUM_RE = re.compile(r'[A-Za-z0-9]')
_FILENAME_RE = re.compile(r'^[\w.\- ]+\.[A-Za-z0-9]{1,4}$')


def is_format_only(s):
    stripped = _FORMAT_SPEC_RE.sub('', s)
    return len(_ALNUM_RE.findall(stripped)) < 3


def is_path_or_filename(s):
    if '\\' in s or '/' in s:
        return True
    return bool(_FILENAME_RE.match(s))


def is_boilerplate(s):
    return 'assert' in s.lower()


def eligible_string(s, min_len, end_to_end_unique,
                     allow_unique_paths, allow_unique_boilerplate):
    """end_to_end_unique: True if exactly one CEA function AND exactly one
    NTSC FUN_ function carry this exact string."""
    if len(s) < min_len:
        return False
    if is_format_only(s):
        return False
    if is_path_or_filename(s):
        if not (allow_unique_paths and end_to_end_unique):
            return False
    if is_boilerplate(s):
        if not (allow_unique_boilerplate and end_to_end_unique):
            return False
    return True


def _normalize_ident(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def is_naming_string(s, cea_name):
    """True when the string literal itself IS the function's name (rare --
    e.g. a debug/RTTI name table entry), which alone earns tier 'confirmed'.
    An exact cross-build text match that merely happens to occur in both
    corpora (the common case) is NOT this -- it stays 'high_confidence'."""
    return _normalize_ident(s) == _normalize_ident(cea_name)


_GENERIC_FUNCTION_SUFFIXES = (
    'initialize', 'initializefornewmap', 'dispose', 'disposefromoldmap',
    'new', 'shutdown', 'startup', 'uninitialize', 'create', 'delete',
    'destroy', 'construct', 'update', 'reset', 'free',
)


def is_subsystem_noun_string(s, cea_name):
    """True when a single-word string is just the SUBSYSTEM's name and the
    CEA candidate name is that same word (optionally pluralized) plus a
    generic verb suffix -- e.g. 'widget' -> widgets_initialize, 'encounter'
    -> encounters_initialize. Every function in that subsystem's family is
    likely to share the literal (it names the subsystem, not the specific
    function); corpus-uniqueness there is an extraction artifact, not real
    fingerprint evidence. A multi-word phrase (e.g. 'first person weapons',
    'jet thrusters') is specific enough to keep -- this filter only fires on
    a bare, single-token noun."""
    if not s or any(ch.isspace() for ch in s.strip()):
        return False
    s_norm = _normalize_ident(s)
    name_norm = _normalize_ident(cea_name)
    if not s_norm or not name_norm.startswith(s_norm):
        return False
    remainder = name_norm[len(s_norm):]
    if not remainder:
        return False  # exact match is is_naming_string's territory, not this filter's
    if remainder.startswith('s'):
        remainder = remainder[1:]
    return any(remainder == suf or remainder.startswith(suf) for suf in _GENERIC_FUNCTION_SUFFIXES)


# ---------------------------------------------------------------------------
# Corpus-wide string-ownership tables
# ---------------------------------------------------------------------------

def build_cea_string_owners(cea_by_name):
    """string -> set(cea_name) over the WHOLE corpus (uniqueness is a
    corpus-wide fact, independent of any holdout split)."""
    owners = defaultdict(set)
    for name, rec in cea_by_name.items():
        for s in rec.get('strings') or []:
            owners[s].add(name)
    return dict(owners)


def build_ntsc_fun_string_owners(ntsc_func):
    """string -> set(addr), restricted to functions currently named FUN_ in
    the callgraph snapshot itself. This is ONLY the raw corpus fact used to
    recompute/verify the prior quick measurement (1,427 FUN_ with strings,
    87 shared strings) -- it is NOT what matching uses operationally, because
    callgraph.json is a live snapshot that concurrent rename work keeps
    moving: most seed addresses (functions we already trust the name of) are
    no longer literally spelled 'FUN_...' in today's callgraph, even though
    for holdout/propose purposes we still need their string content."""
    owners = defaultdict(set)
    for addr, rec in ntsc_func.items():
        name = rec.get('name') or ''
        if not name.startswith('FUN_'):
            continue
        for s in rec.get('strings') or []:
            owners[s].add(addr)
    return dict(owners)


def build_ntsc_all_string_owners(ntsc_func):
    """string -> set(addr) over EVERY NTSC function regardless of current
    name. This is the table actually used for matching: string-to-location
    uniqueness is a fact about the binary, independent of whether kb.json
    has named that location yet. Any specific evaluation still only fires
    for the address being asked about (a live FUN_ in propose, a
    fictionally-blinded seed in holdout)."""
    owners = defaultdict(set)
    for addr, rec in ntsc_func.items():
        for s in rec.get('strings') or []:
            owners[s].add(addr)
    return dict(owners)


def print_corpus_stats(cea_by_name, ntsc_func, min_len, allow_unique_paths, allow_unique_boilerplate):
    cea_owners = build_cea_string_owners(cea_by_name)
    ntsc_fun_owners = build_ntsc_fun_string_owners(ntsc_func)
    ntsc_all_owners = build_ntsc_all_string_owners(ntsc_func)
    fun_with_strings = sum(1 for addr, rec in ntsc_func.items()
                            if (rec.get('name') or '').startswith('FUN_') and rec.get('strings'))
    cea_unique = {s for s, owners in cea_owners.items() if len(owners) == 1}
    ntsc_fun_strings = set(ntsc_fun_owners.keys())
    shared_raw = ntsc_fun_strings & cea_unique

    eligible = set()
    dropped = []
    for s in shared_raw:
        end_to_end = len(ntsc_fun_owners[s]) == 1  # cea side already ==1 by construction
        if eligible_string(s, min_len, end_to_end, allow_unique_paths, allow_unique_boilerplate):
            eligible.add(s)
        else:
            if len(s) < min_len:
                reason = f'too_short(<{min_len})'
            elif is_format_only(s):
                reason = 'format_only'
            elif is_path_or_filename(s):
                reason = 'path_or_filename(not_end_to_end_unique)' if not end_to_end else 'path_or_filename(disallowed)'
            elif is_boilerplate(s):
                reason = 'boilerplate(not_end_to_end_unique)' if not end_to_end else 'boilerplate(disallowed)'
            else:
                reason = 'unknown'
            dropped.append((s, reason))

    print('=== corpus stats (recompute of quick measurement) ===')
    print(f'  NTSC FUN_ functions with >=1 string: {fun_with_strings}')
    print(f'  CEA strings occurring in exactly one CEA function: {len(cea_unique)}')
    print(f'  shared (NTSC FUN_ strings) ^ (CEA unique strings), raw, FUN_-only snapshot: {len(shared_raw)}')
    print(f'  of those, eligible after filters (len/format/path/boilerplate): {len(eligible)}')
    if dropped:
        print(f'  dropped ({len(dropped)}), with reason:')
        for s, reason in sorted(dropped):
            print(f'    {reason}: {s!r}')
    print('  (operational matching below uses ALL NTSC functions\' strings, not just FUN_-named '
          'ones, since callgraph.json is a live snapshot and most seeds are already renamed in it; '
          'the FUN_-only numbers above are the point-in-time recompute of the prior measurement.)')
    return cea_owners, ntsc_all_owners


def check_confirmed_tier_reachability(unique_string_index):
    """Diagnostic: does any string in the operational index literally equal
    (mod normalization) the name of the CEA function it fingerprints? If
    this is empty, tier 'confirmed' never fires on this corpus snapshot --
    verified here rather than merely inferred from an empty propose run."""
    hits = [(s, name) for s, name in unique_string_index.items() if is_naming_string(s, name)]
    print(f'  confirmed-tier reachability check: {len(hits)} string(s) in the operational index '
          f'literally name their owning function')
    for s, name in hits:
        print(f'    {s!r} -> {name}')
    return hits


# ---------------------------------------------------------------------------
# Matching core
# ---------------------------------------------------------------------------

def build_unique_string_index(cea_owners, ntsc_owners, min_len,
                               allow_unique_paths, allow_unique_boilerplate,
                               drop_subsystem_noun=True):
    """Return {string: cea_name} for every string usable as primary
    evidence: CEA-unique, and passing eligibility (with the path/boilerplate
    end-to-end-unique exception evaluated against NTSC-side occurrence
    count too). Also returns the list of (string, cea_name) pairs dropped
    for being a bare subsystem-noun string, for diagnostic reporting."""
    index = {}
    dropped_subsystem_noun = []
    for s, owners in cea_owners.items():
        if len(owners) != 1:
            continue
        ntsc_count = len(ntsc_owners.get(s, ()))
        if ntsc_count == 0:
            continue
        end_to_end = ntsc_count == 1
        if not eligible_string(s, min_len, end_to_end, allow_unique_paths, allow_unique_boilerplate):
            continue
        (cea_name,) = owners
        if drop_subsystem_noun and is_subsystem_noun_string(s, cea_name):
            dropped_subsystem_noun.append((s, cea_name))
            continue
        index[s] = cea_name
    return index, dropped_subsystem_noun


def strict_window_object_for(addr360, addr360_list, obj_list):
    """Like cea_propagate_names.window_object_for but WITHOUT the
    single-neighbor edge tolerance: both neighbors must exist and agree."""
    import bisect
    if not addr360_list:
        return None
    i = bisect.bisect_left(addr360_list, addr360)
    if i == 0 or i >= len(addr360_list):
        return None
    floor_obj = obj_list[i - 1]
    ceil_obj = obj_list[i]
    return floor_obj if floor_obj == ceil_obj else None


def count_seed_anchors(addr, candidate, ntsc_func, ntsc_callers, seeds, cea_callees, cea_callers):
    f = ntsc_func.get(addr)
    if f is None:
        return 0
    callee_seed_names = [seeds[c] for c in f['callees'] if c in seeds]
    caller_addrs = ntsc_callers.get(addr, set())
    caller_seed_names = [seeds[c] for c in caller_addrs if c in seeds]
    cand_callees = cea_callees.get(candidate, set())
    cand_callers = cea_callers.get(candidate, set())
    hit = sum(1 for n in callee_seed_names if n in cand_callees)
    hit += sum(1 for n in caller_seed_names if n in cand_callers)
    return hit


def match_target(addr, ntsc_func, unique_string_index):
    """Return dict describing what the target's strings say, or None if the
    target has no qualifying string at all.

    {'conflict': True, 'names': {name: [strings]}}   -- >1 distinct name
    {'conflict': False, 'name': name, 'strings': [s, ...]}
    """
    f = ntsc_func.get(addr)
    if f is None:
        return None
    matched = defaultdict(list)
    for s in sorted(f.get('strings') or []):
        owner = unique_string_index.get(s)
        if owner is not None:
            matched[owner].append(s)
    if not matched:
        return None
    if len(matched) > 1:
        return {'conflict': True, 'names': dict(matched)}
    (name, strings), = matched.items()
    return {'conflict': False, 'name': name, 'strings': strings}


def evaluate_candidate(addr, match, ntsc_func, ntsc_callers, cea_by_name,
                        excluded_names, seeds, cea_callees, cea_callers,
                        addr360_list, obj_list, min_anchor_hits):
    """Apply secondary acceptance + corroboration lookup to a non-conflict
    match. Returns a result dict with 'ok', 'reason', evidence fields."""
    name = match['name']
    strings = match['strings']
    n_unique = len(strings)
    cea_rec = cea_by_name.get(name, {})

    if cea_rec.get('owner_divergence'):
        return {'ok': False, 'reason': 'owner_divergence', 'name': name, 'strings': strings,
                'n_unique': n_unique}
    if name in excluded_names:
        return {'ok': False, 'reason': 'name_in_use', 'name': name, 'strings': strings,
                'n_unique': n_unique}

    anchors = count_seed_anchors(addr, name, ntsc_func, ntsc_callers, seeds, cea_callees, cea_callers)
    f = ntsc_func.get(addr, {})
    target_object = f.get('object')
    a360 = cea_rec.get('addr360')
    window_obj = strict_window_object_for(a360, addr360_list, obj_list) if a360 is not None else None
    window_ok = window_obj is not None and target_object is not None and window_obj == target_object
    corroborated = anchors >= min_anchor_hits or window_ok

    return {
        'ok': True, 'name': name, 'strings': strings, 'n_unique': n_unique,
        'anchors': anchors, 'window_ok': window_ok, 'window_object': window_obj,
        'target_object': target_object, 'corroborated': corroborated,
    }


# ---------------------------------------------------------------------------
# Loading (reuse cea_propagate_names loaders directly)
# ---------------------------------------------------------------------------

load_cea_index = cpn.load_cea_index
load_rename_mapping = cpn.load_rename_mapping
load_callgraph = cpn.load_callgraph
build_seeds = cpn.build_seeds
build_cea_graph = cpn.build_cea_graph
build_addr360_window_index = cpn.build_addr360_window_index
KbView = cpn.KbView


def load_existing_proposals(path):
    """addr(int) -> new_name, tolerant of a not-yet-existing file (the
    window-match lane may not have produced its output yet)."""
    if not path or not os.path.exists(path):
        return {}
    with open(path) as f:
        rows = json.load(f)
    out = {}
    for r in rows:
        try:
            out[int(r['addr'], 16)] = r['new_name']
        except (KeyError, ValueError, TypeError):
            continue
    return out


# ---------------------------------------------------------------------------
# holdout
# ---------------------------------------------------------------------------

def split_holdout(all_seeds, holdout_frac, rng_seed):
    """Same split convention as cea_propagate_names.run_holdout_once."""
    rng = random.Random(rng_seed)
    addrs = sorted(all_seeds.keys())
    rng.shuffle(addrs)
    n_holdout = int(len(addrs) * holdout_frac)
    holdout_addrs = set(addrs[:n_holdout])
    train_seeds = {a: n for a, n in all_seeds.items() if a not in holdout_addrs}
    holdout_truth = {a: all_seeds[a] for a in holdout_addrs}
    return train_seeds, holdout_truth


def _evaluate_seed_set(kb, cea_by_name, ntsc_func, ntsc_callers, graph_seeds,
                        truth_seeds, excluded_names, unique_string_index,
                        min_anchor_hits, confusion_n):
    """Shared core: predict on every addr in truth_seeds, using graph_seeds
    to build the seeded-anchor/addr360-window corroboration graph, and
    excluded_names to gate the name-already-in-use secondary check."""
    holdout_truth = truth_seeds
    cea_callees, cea_callers = build_cea_graph(cea_by_name)
    addr360_list, obj_list = build_addr360_window_index(graph_seeds, cea_by_name, kb)

    variants = {
        'a_single_alone': {'tp': 0, 'fp': 0, 'no_call': 0, 'confusions': []},
        'b_single_corroborated': {'tp': 0, 'fp': 0, 'no_call': 0, 'confusions': []},
        'c_double_regardless': {'tp': 0, 'fp': 0, 'no_call': 0, 'confusions': []},
        'combined_b_or_c': {'tp': 0, 'fp': 0, 'no_call': 0, 'confusions': []},
    }
    conflicts = 0

    for addr, true_name in holdout_truth.items():
        match = match_target(addr, ntsc_func, unique_string_index)
        if match is None:
            for v in variants.values():
                v['no_call'] += 1
            continue
        if match['conflict']:
            conflicts += 1
            for v in variants.values():
                v['no_call'] += 1
            continue

        result = evaluate_candidate(
            addr, match, ntsc_func, ntsc_callers, cea_by_name, excluded_names,
            graph_seeds, cea_callees, cea_callers, addr360_list, obj_list, min_anchor_hits)

        n_unique = result['n_unique']
        predicted = result['name'] if result['ok'] else None

        def record(vname, fires):
            v = variants[vname]
            if not fires:
                v['no_call'] += 1
                return
            if predicted == true_name:
                v['tp'] += 1
            else:
                v['fp'] += 1
                if len(v['confusions']) < confusion_n:
                    v['confusions'].append({
                        'addr': hex(addr), 'true_name': true_name, 'predicted_name': predicted,
                        'n_unique': n_unique, 'reason': None if result['ok'] else result['reason'],
                        'anchors': result.get('anchors'), 'window_ok': result.get('window_ok'),
                        'strings': result.get('strings'),
                    })

        fires_a = result['ok'] and n_unique == 1
        fires_b = result['ok'] and n_unique == 1 and result['corroborated']
        fires_c = result['ok'] and n_unique >= 2
        fires_combined = fires_b or fires_c
        record('a_single_alone', fires_a)
        record('b_single_corroborated', fires_b)
        record('c_double_regardless', fires_c)
        record('combined_b_or_c', fires_combined)

    out = {'truth_size': len(holdout_truth), 'graph_seeds': len(graph_seeds), 'conflicts': conflicts}
    for vname, v in variants.items():
        total = v['tp'] + v['fp']
        precision = (v['tp'] / total) if total else None
        recall = v['tp'] / len(holdout_truth) if holdout_truth else None
        out[vname] = {
            'predictions_made': total, 'true_positives': v['tp'], 'false_positives': v['fp'],
            'no_call': v['no_call'], 'precision': precision, 'recall': recall,
            'confusions': v['confusions'],
        }
    return out


def run_holdout_once(kb, cea_by_name, ntsc_func, ntsc_callers, all_seeds,
                      unique_string_index, holdout_frac, rng_seed, min_anchor_hits,
                      confusion_n):
    """Blind 20% holdout: graph/window corroboration is built from the 80%
    training seeds only; excluded_names omits ONLY the held-out truth names
    (so a correct held-out prediction is never blocked by its own name being
    'already in use' -- that would be circular). This is the headline,
    fully-blind result the task asked for; small holdout counts mean its
    per-variant precision has wide error bars -- see run_full_seed_check for
    a larger-sample secondary check."""
    train_seeds, holdout_truth = split_holdout(all_seeds, holdout_frac, rng_seed)
    held_out_names = set(holdout_truth.values())
    excluded_names = kb.all_function_names - held_out_names
    result = _evaluate_seed_set(kb, cea_by_name, ntsc_func, ntsc_callers, train_seeds,
                                 holdout_truth, excluded_names, unique_string_index,
                                 min_anchor_hits, confusion_n)
    result['holdout_size'] = result.pop('truth_size')
    result['train_seeds'] = result.pop('graph_seeds')
    return result


def run_full_seed_check(kb, cea_by_name, ntsc_func, ntsc_callers, all_seeds,
                         unique_string_index, min_anchor_hits, confusion_n):
    """Secondary, larger-sample check: evaluate every one of the ~2,400
    seeds (not a 20% slice), for variants (a) and (c) whose acceptance rule
    has no seed dependence (string uniqueness is a corpus fact; neither
    requires anchors/window). NOT blind -- the seeded corroboration graph is
    built from these same seeds, and excluded_names is relaxed to
    kb.all_function_names - set(all_seeds.values()) so a seed's own current
    name never blocks its own correct re-prediction. Variants (b)/(combined)
    are reported too but read them as upper bounds, not blind estimates,
    since their corroboration signal is trained on the same seeds being
    scored. Use this only to widen the sample behind the precision claim for
    (a)/(c); the holdout run above remains the blind headline number."""
    excluded_names = kb.all_function_names - set(all_seeds.values())
    return _evaluate_seed_set(kb, cea_by_name, ntsc_func, ntsc_callers, all_seeds,
                               all_seeds, excluded_names, unique_string_index,
                               min_anchor_hits, confusion_n)


def cmd_holdout(args):
    kb = KbView()
    cea_by_name = load_cea_index(args.cea_index)
    rename_rows = load_rename_mapping(args.rename_mapping)
    ntsc_func, ntsc_callers = load_callgraph(args.callgraph)
    all_seeds, seed_stats = build_seeds(kb, cea_by_name, rename_rows)

    print('=== seeds ===')
    for k, v in seed_stats.items():
        print(f'  {k}: {v}')

    cea_owners, ntsc_owners = print_corpus_stats(cea_by_name, ntsc_func, args.min_string_len,
                                                  args.allow_unique_paths, args.allow_unique_boilerplate)

    unique_string_index, dropped_subsystem_noun = build_unique_string_index(
        cea_owners, ntsc_owners, args.min_string_len, args.allow_unique_paths,
        args.allow_unique_boilerplate, args.drop_subsystem_noun)
    print(f'  usable (string -> single CEA name) index size: {len(unique_string_index)}')
    if dropped_subsystem_noun:
        print(f'  additionally dropped as bare subsystem-noun strings ({len(dropped_subsystem_noun)}):')
        for s, name in sorted(dropped_subsystem_noun):
            print(f'    {s!r} -> {name}')
    check_confirmed_tier_reachability(unique_string_index)

    result = run_holdout_once(
        kb, cea_by_name, ntsc_func, ntsc_callers, all_seeds, unique_string_index,
        args.holdout_frac, args.rng_seed, args.min_anchor_hits, args.confusion_sample)

    print()
    print(f'=== holdout (frac={args.holdout_frac}, rng_seed={args.rng_seed}) -- BLIND, headline result ===')
    print(f'  holdout_size={result["holdout_size"]} train_seeds={result["train_seeds"]} '
          f'conflicts_rejected={result["conflicts"]}')
    _print_variant_results(result)

    full_result = run_full_seed_check(
        kb, cea_by_name, ntsc_func, ntsc_callers, all_seeds, unique_string_index,
        args.min_anchor_hits, args.confusion_sample)
    print()
    print('=== secondary check: ALL seeds, NOT blind (larger sample for (a)/(c); '
          '(b)/(combined) are an upper bound since their corroboration graph is trained '
          'on the same seeds being scored) ===')
    print(f'  truth_size={full_result["truth_size"]} graph_seeds={full_result["graph_seeds"]} '
          f'conflicts_rejected={full_result["conflicts"]}')
    _print_variant_results(full_result)


def _print_variant_results(result):
    labels = {
        'a_single_alone': '(a) one unique string alone (no corroboration required)',
        'b_single_corroborated': '(b) one unique string + corroboration required',
        'c_double_regardless': '(c) two+ unique strings, regardless of corroboration',
        'combined_b_or_c': '(shipped rule) b OR c',
    }
    for key, label in labels.items():
        r = result[key]
        prec = r['precision']
        rec = r['recall']
        prec_s = f'{prec:.4f}' if prec is not None else 'n/a'
        rec_s = f'{rec:.4f}' if rec is not None else 'n/a'
        meets = ' (meets 0.97 target)' if (prec is not None and prec >= 0.97) else ''
        print(f'--- {label} ---')
        print(f'  predictions={r["predictions_made"]} tp={r["true_positives"]} '
              f'fp={r["false_positives"]} no_call={r["no_call"]}')
        print(f'  precision={prec_s} recall={rec_s}{meets}')
        for c in r['confusions']:
            print(f'    {c["addr"]}: predicted {c["predicted_name"]!r} but truth is {c["true_name"]!r} '
                  f'(n_unique={c["n_unique"]}, reason={c["reason"]}, anchors={c.get("anchors")}, '
                  f'window_ok={c.get("window_ok")}, strings={c.get("strings")})')


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

    cea_owners, ntsc_owners = print_corpus_stats(
        cea_by_name, ntsc_func, args.min_string_len, args.allow_unique_paths, args.allow_unique_boilerplate)
    unique_string_index, dropped_subsystem_noun = build_unique_string_index(
        cea_owners, ntsc_owners, args.min_string_len, args.allow_unique_paths,
        args.allow_unique_boilerplate, args.drop_subsystem_noun)
    print(f'  usable (string -> single CEA name) index size: {len(unique_string_index)}')
    if dropped_subsystem_noun:
        print(f'  additionally dropped as bare subsystem-noun strings ({len(dropped_subsystem_noun)}):')
        for s, name in sorted(dropped_subsystem_noun):
            print(f'    {s!r} -> {name}')
    check_confirmed_tier_reachability(unique_string_index)

    excluded_names = set(kb.all_function_names)
    cea_callees, cea_callers = build_cea_graph(cea_by_name)
    addr360_list, obj_list = build_addr360_window_index(seeds, cea_by_name, kb)

    existing_mapping_addrs = set(int(r['addr'], 16) for r in rename_rows)
    prior_proposals = {}
    prior_proposals.update(load_existing_proposals(args.callgraph_proposals))
    prior_proposals.update(load_existing_proposals(args.window_proposals))

    conflicts_rejected = 0
    skipped_prior_diff = 0
    rejected_owner_divergence = 0
    rejected_name_in_use = 0
    rejected_uncorroborated_single = 0
    rejected_uncorroborated_details = []
    rejected_below_min_unique = 0

    candidates = []  # (addr, result) before same-name collision resolution
    for addr in kb.fun_addrs:
        match = match_target(addr, ntsc_func, unique_string_index)
        if match is None:
            continue
        if match['conflict']:
            conflicts_rejected += 1
            continue

        prior = prior_proposals.get(addr)
        if prior is not None and prior != match['name']:
            skipped_prior_diff += 1
            continue

        result = evaluate_candidate(
            addr, match, ntsc_func, ntsc_callers, cea_by_name, excluded_names,
            seeds, cea_callees, cea_callers, addr360_list, obj_list, args.min_anchor_hits)

        if not result['ok']:
            if result['reason'] == 'owner_divergence':
                rejected_owner_divergence += 1
            elif result['reason'] == 'name_in_use':
                rejected_name_in_use += 1
            continue

        n_unique = result['n_unique']
        if n_unique < args.min_unique_strings:
            rejected_below_min_unique += 1
            continue
        if n_unique == 1 and not result['corroborated']:
            rejected_uncorroborated_single += 1
            rejected_uncorroborated_details.append({
                'addr': f'{addr:08x}', 'candidate_name': result['name'], 'string': result['strings'][0],
            })
            continue

        candidates.append((addr, result))

    # Resolve same-CEA-name collisions by evidence strength, strongest first,
    # exactly as cea_propagate_names.py does for its own candidate name.
    def strength(r):
        return (r['n_unique'], r['corroborated'], r['anchors'])

    candidates.sort(key=lambda t: strength(t[1]), reverse=True)

    proposals = []
    used_names = set()
    for addr, result in candidates:
        name = result['name']
        if name in used_names:
            continue
        used_names.add(name)
        n_unique = result['n_unique']
        naming_strings = [s for s in result['strings'] if is_naming_string(s, name)]
        tier = 'confirmed' if naming_strings else 'high_confidence'
        row = {
            'addr': f'{addr:08x}',
            'old_name': kb.name_by_addr[addr],
            'new_name': name,
            'tier': tier,
            'method': 'exact_string',
            'evidence': {
                'exact_strings': result['strings'],
                'unique_string_count': n_unique,
                'naming_strings': naming_strings,
                'kb_object': kb.object_by_addr.get(addr),
                'target_object': result['target_object'],
                'seed_anchor_hits': result['anchors'],
                'window_object': result['window_object'],
                'window_compatible': result['window_ok'],
                'corroborated': result['corroborated'],
                'corroboration_required': n_unique == 1,
                'owner_divergence': bool(cea_by_name.get(name, {}).get('owner_divergence')),
                'naming_confidence_tier': 'T1' if naming_strings else 'T2',
                'naming_confidence_reason': (
                    'string literal is the function\'s own name' if naming_strings else
                    'cross-build exact string match is strong structural evidence but not '
                    'binary-native self-evidence, so it stays T2 per naming-confidence.md '
                    'regardless of exact-text confidence'
                ),
                'weak_single_string_evidence': n_unique == 1,
            },
            'dup_in_mapping': addr in existing_mapping_addrs,
        }
        proposals.append(row)

    proposals.sort(key=lambda r: (r['evidence']['unique_string_count'],
                                   r['evidence']['corroborated'],
                                   r['evidence']['seed_anchor_hits']), reverse=True)

    by_tier = defaultdict(int)
    for r in proposals:
        by_tier[r['tier']] += 1

    print()
    print('=== proposals ===')
    print(f'  total: {len(proposals)}')
    for tier, count in sorted(by_tier.items()):
        print(f'  {tier}: {count}')
    print(f'  conflicts_rejected: {conflicts_rejected}')
    print(f'  rejected_owner_divergence: {rejected_owner_divergence}')
    print(f'  rejected_name_in_use: {rejected_name_in_use}')
    print(f'  rejected_below_min_unique_strings(<{args.min_unique_strings}): {rejected_below_min_unique}')
    print(f'  rejected_uncorroborated_single: {rejected_uncorroborated_single}')
    for d in rejected_uncorroborated_details:
        print(f'    {d["addr"]}: candidate {d["candidate_name"]!r} on string {d["string"]!r} '
              f'-- no anchor, no strict window agreement')
    print(f'  skipped_prior_proposal_disagreement: {skipped_prior_diff}')

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w') as f:
        json.dump(proposals, f, indent=2)
    print(f'wrote {args.out}')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_common_args(p):
    p.add_argument('--callgraph', default=DEFAULT_CALLGRAPH)
    p.add_argument('--cea-index', default=DEFAULT_CEA_INDEX)
    p.add_argument('--rename-mapping', default=DEFAULT_RENAME_MAPPING)
    p.add_argument('--min-string-len', type=int, default=6,
                    help='Ignore strings shorter than this (default 6).')
    p.add_argument('--allow-unique-paths', dest='allow_unique_paths', action='store_true', default=True,
                    help='Allow a path/filename-shaped string as evidence when it is unique '
                         'end-to-end (one CEA function, one NTSC FUN_).')
    p.add_argument('--no-allow-unique-paths', dest='allow_unique_paths', action='store_false')
    p.add_argument('--allow-unique-boilerplate', dest='allow_unique_boilerplate', action='store_true',
                    default=True, help='Same end-to-end-unique exception for boilerplate (assert) strings.')
    p.add_argument('--no-allow-unique-boilerplate', dest='allow_unique_boilerplate', action='store_false')
    p.add_argument('--min-anchor-hits', type=int, default=1,
                    help='Minimum seeded callee/caller anchor hits to count as corroborated '
                         '(default 1).')
    p.add_argument('--drop-subsystem-noun-strings', dest='drop_subsystem_noun', action='store_true',
                    default=True,
                    help="Drop a bare single-word string from the evidence index when it is just "
                         "the subsystem's name and the CEA candidate is that word plus a generic "
                         "verb suffix (e.g. 'widget' -> widgets_initialize) -- every function in "
                         "that family likely shares the literal, so corpus-uniqueness there is an "
                         "extraction artifact, not real fingerprint evidence (default on).")
    p.add_argument('--no-drop-subsystem-noun-strings', dest='drop_subsystem_noun', action='store_false')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    p_holdout = sub.add_parser('holdout', help='Evaluate precision/recall on a held-out slice of seeds.')
    add_common_args(p_holdout)
    p_holdout.add_argument('--holdout-frac', type=float, default=0.2)
    p_holdout.add_argument('--rng-seed', type=int, default=42)
    p_holdout.add_argument('--confusion-sample', type=int, default=10)
    p_holdout.set_defaults(func=cmd_holdout)

    p_propose = sub.add_parser('propose', help='Run with all seeds and emit string_proposals.json.')
    add_common_args(p_propose)
    p_propose.add_argument('--callgraph-proposals', default=DEFAULT_CALLGRAPH_PROPOSALS,
                            help='Prior callgraph-propagation proposals; addresses already covered '
                                 'there are skipped unless this lane proposes the identical name.')
    p_propose.add_argument('--window-proposals', default=DEFAULT_WINDOW_PROPOSALS,
                            help='Prior address-window-match proposals (may not exist yet); same '
                                 'skip-unless-identical rule.')
    p_propose.add_argument('--out', default=DEFAULT_OUT)
    p_propose.add_argument('--min-unique-strings', type=int, default=2,
                            help="Minimum qualifying unique strings required to accept a proposal "
                                 "(default 2). The holdout command measures rule (b) (single string + "
                                 "corroboration) separately, but the full-seed check shows it and the "
                                 "combined 'b OR c' rule both land below the required 0.97 precision "
                                 "in production (0.9524 and 0.9677) because of two graph-indistinguishable "
                                 "sibling-function confusions that no corroboration threshold can fix "
                                 "(object_new vs. object_new_with_datum_role_control; "
                                 "rasterizer_window_set_fog vs. rasterizer_initialize). Only rule (c), "
                                 "n_unique>=2, measured 1.0000 precision at both blind-holdout and "
                                 "full-seed scale, so it is the default here. Pass --min-unique-strings 1 "
                                 "to opt into single-string+corroboration proposals anyway, at that "
                                 "measured lower precision.")
    p_propose.set_defaults(func=cmd_propose)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
