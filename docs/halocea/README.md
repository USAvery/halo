# halocea corpus — usability assessment for the 2276 lift

Assessment date: 2026-09-03. Source: `surreptitiousresearch/halocea`, local copy at
`G:\H1 Performance Build 2.0 Beta 1 LOCAL 2v2\release-package-local\...\src`.

## What the corpus actually is

Not a Bungie source leak and not a PDB dump. It is a reverse-engineering corpus derived from a
24 June 2011 Halo: Combat Evolved Anniversary prototype `HCEX_Release.xex` (Xbox 360, PowerPC),
which shipped with verbose symbols and minimal optimization. Symbol, type and static-library data
were extracted from that binary and rebuilt into readable C. Its own README names this repository's
upstream (`stianeklund`) as one of the sibling projects the corpus is meant to feed.

Consequences for us:

- Provenance is the same category as our own work — RE output, not proprietary source. There is no
  clean-room problem to resolve, but every borrowed identifier must still be marked as borrowed.
- Byte-perfect recompilation was explicitly a non-goal for that project. Its function bodies are
  reconstructions, not Bungie's compiler output.

## Version gap

| | ours | upstream halocea | this local tree |
|---|---|---|---|
| binary | `cachebeta.xbe`, Halo Xbox debug `01.10.12.2276` (Oct 2001) | `HCEX_Release.xex` | (built from the corpus) |
| Blam! engine | 2276 | `01.00.01.0563` (Halo PC 1.00 lineage, 2003) | same |
| architecture | x86, MSVC | PowerPC, Xbox 360 toolchain | x86, MSVC |

Roughly two years apart. Names, enums and algorithm structure are expected to transfer; exact codegen
and some layout details are not.

**The local tree is a port, not the upstream corpus.** `README.txt` describes it as "a native Windows
port of the Blam engine"; `actor_action_change.c` uses `__fastcall` and `actor_datum.h` uses
`__int16`. So the files future work will actually open are MSVC/x86-targeted, one step removed from
the `.xex` ground truth their comments cite. Two consequences: offsets here may have been
x86-normalised by the port author (convenient for us, but no longer the raw PPC evidence), and the
PPC-vs-x86 divergence hazard below is *reduced* in this tree rather than live. Where a layout
question is load-bearing, prefer the upstream repository over this port.

## Measured overlap

Counts taken 2026-09-03 against `kb.json` (9133 functions, 5051 still carrying `FUN_xxxxxxxx`
declarations, 5366 ported) and the corpus's 7318 flat `src/engine/blam/*.c` files (one function per
file, named) plus 2981 headers.

### Cross-build stability evidence

Two independent checks, both positive:

1. `actor_datum` is `0x724` (1828 bytes) in halocea. Our binary-proven `actor_t` in `src/types.h` is
   `cs(actor_t, 0x724)`. Four of our `co()` offsets — derived from 2276 disassembly with no knowledge
   of this corpus — land exactly on halocea sub-struct boundaries: `target` `0x268`,
   `danger_zone` `0x280`, `firing_positions.current_position_index` `0x3b8`, and
   `state.action` `0x6c` (their `actor_state_data.h` states `action` at `0x0C`, and their `state`
   member sits at `0x60`).
2. `NUMBER_OF_*` enum-bound identifiers, split by how ours were obtained. 97 of our 112 appear
   *inside string literals* — that is, in assert text stamped by the 2276 binary itself, so they are
   binary evidence rather than an earlier agent's guess at Bungie convention. **40 of those 97
   assert-proven names appear verbatim in halocea** (`enum_bounds_shared_assert_proven.txt`). Only one
   further name matches from the bare-identifier-only set, so the overlap is cross-build confirmation,
   not convention convergence. Our 2276 assert strings are therefore a native verification channel for
   borrowed enum names — the name can be confirmed against our own binary rather than assumed.
3. The same channel reaches enum **members**, not only bounds. 89 of halocea's 4128 enum member
   identifiers appear inside our own string literals
   (`enum_members_shared_assert_proven.txt`) — for example `_unit_speech_none`, which
   `unit_dialogue.c:0x16e` asserts verbatim in this binary. Those are T1 on our own evidence.

This is evidence that Bungie identifiers are stable across the gap. It is not evidence that layout
is stable: treat every struct as a hypothesis to confirm with `cs()`/`co()` against 2276.

### Translation-unit join

Our `.obj` names in `kb.json` are real Bungie TU names; halocea filenames are real Bungie function
names. Bungie's convention (`actor_action.c` holds `actor_action_*`) makes longest-prefix bucketing
the join. Result: 105 of our 190 real TUs get a non-empty bucket, covering 2488 of 7318 halocea
functions. Per-TU table in `tu_overlap.json`; top rows by `min(our ported FUN_ count, bucket size)`:

| TU | fns | ported | still `FUN_` | halocea bucket |
|---|---|---|---|---|
| `hs.obj` | 257 | 257 | 229 | 225 |
| `game_engine.obj` | 238 | 237 | 76 | 134 |
| `rasterizer.obj` | 89 | 83 | 57 | 379 |
| `scenario.obj` | 62 | 62 | 49 | 52 |
| `players.obj` | 248 | 245 | 184 | 26 |

Bucketing sizes the candidate pool per TU. It does **not** say which `FUN_` is which name — that
still needs a per-function signal (shared literal strings, call-graph shape, or for `hs.obj` the
script-function opcode table, which carries name strings in both binaries).

A second caveat: a short TU name unions over its siblings, so a bucket much larger than its TU is not
a per-TU pool. `rasterizer.obj` has 89 functions against a 379-entry bucket, while
`rasterizer_sprites.obj` (82 still `FUN_`) draws an empty bucket because its candidates sit inside
`rasterizer`'s. `hs.obj` is unaffected — longest-prefix moved `hs_compile` and `hs_runtime` out,
242 → 225 — so the pilot choice stands.

## Lanes, ranked by value against risk

1. **Enums and named constants.** Best first move. The *value* is proven by our binary; only the
   *name* is borrowed, so it fits the byte-identical gate cleanly. Our `types.h` has 3 enums against
   halocea's 754 enum-bearing headers; we currently carry bare `#define OBJECT_TYPE_BIPED 0` and
   magic literals such as the `0x25`/`0x2b` in `src/halo/hs/hs_runtime.c`. 439 `NUMBER_OF_*` families
   named in halocea have no name on our side yet (`enum_bounds_halocea_only.txt`).
2. **Function naming.** 2599 ported functions still carry `FUN_` names. Gated on establishing a
   per-function signal inside one TU first. `hs.obj` is the natural pilot: fully ported, 229 `FUN_`,
   225 candidates, and the opcode table gives a near-exact join.
3. **Struct and field naming, offset-to-field rewrites.** High value, real hazards (below).
4. **Function bodies.** Hypothesis source only — never transcribe. A `0563`-era body that reads
   correctly will still score structurally low against 2276 and will burn optimizer and permuter
   cycles on what is really a version difference. Read halocea when a lift is already stuck and its
   diff looks structural, to form a control-flow hypothesis the binary then confirms.

## Pilot result — lane 1, `src/halo/math/real_math.c` (2026-09-03)

Two enums recovered end to end. Chosen because the TU carries two of the 40 assert-proven
bounds, both target functions sat at a clean 100.0% baseline, and the file uses no `__LINE__`
(so a top-of-file insertion carries no assert-immediate hazard).

Bounds independently proven from 2276 before consulting the corpus, and matching it exactly:

| enum | our binary | halocea |
|---|---|---|
| `NUMBER_OF_PERIODIC_FUNCTIONS` | `FUN_0010a5e0` rejects `>= 0xc`; `periodic_functions_dispose` frees 12 tables | `0xC` |
| `NUMBER_OF_TRANSITION_FUNCTIONS` | `transition_function_evaluate` rejects `>= 6`; dispose frees 6 tables | `6` |

The two headers land in **different tiers**, which is why this pair was worth piloting:
`transition_function.h` is cited to a compiled enum in the 0563 binary (`name_source: halocea`,
T2), while `periodic_function.h` is marked by the corpus itself as a reconciliation with no
ground-truth symbol (`name_source: halocea-guess`, T3). Reading their provenance line is
therefore per-header work, not a corpus-wide assumption.

Three slots got independent 2276 corroboration, promoting those alone:

- `_periodic_function_one` — `FUN_0010a5e0` short-circuits type 0 to the `1.0f` constant at
  `0x2533c8`.
- `_periodic_function_slide` and `_periodic_function_slide_with_random_period` — the `0xc0`
  mask selects exactly types 6 and 7 for the sawtooth wraparound fixup (`v0` high, `v1` low
  across the discontinuity, add 1.0), which no non-sawtooth curve needs.
- `_transition_function_linear` — type 0 returns the clamped `t` unchanged.

Edit: enum definitions plus `PERIODIC_FUNCTION_SLIDE_MASK` at the top of the TU (TU-local, per
`name-cleanup` rule 4 — promote to a shared header when a second consumer appears), and four
call sites converted from raw literals.

**Gate: all 168 VC71 scores in the TU byte-identical to baseline, zero `IMM-WARN`.** Both target
functions hold 100.0% (93/93 and 87/87 instructions). The computed mask form
`((1 << _periodic_function_slide) | (1 << ..._with_random_period))` folds to the original `0xc0`
immediate with no codegen movement.

Cost: two enums, four call sites, one TU. The mechanism is what generalises — 439 further
`NUMBER_OF_*` families are named in halocea with no counterpart here, and 39 of the 40
assert-proven bounds remain unconverted.

## Batch 2 — `src/halo/units/units.c` (2026-09-03)

208 scored functions, no `__LINE__`. Six bounds proven from 2276 before the corpus was
consulted; **all six agree exactly**, so the 0563 build added no unit selector in this group:

| enum | proven from 2276 | value |
|---|---|---|
| `NUMBER_OF_UNIT_SPEECH_PRIORITIES` | `unit_dialogue.c:0x82` rejects `priority > 10` | 11 |
| `NUMBER_OF_UNIT_SCREAM_TYPES` | rejects `>= 6` | 6 |
| `NUMBER_OF_UNIT_CONTROL_FLAGS` | rejects `& 0xffff8000` | 15 |
| `NUMBER_OF_UNIT_GRENADE_TYPES` | rejects `>= 2` | 2 |
| `NUMBER_OF_UNIT_STATES` | rejects `>= 0x2c` | 44 |
| `NUMBER_OF_VOCALIZATION_TYPES` | `unit_dialogue.c:0x90` rejects `> 0xd0` | 209 |

The strongest single result is a member name, not a bound. Our own binary asserts verbatim at
`units.c:1705`:

```c
display_assert("unit->unit.speech.current.priority > _unit_speech_none",
               "c:\\halo\\SOURCE\\units\\unit_dialogue.c", 0x16e, 1);
```

That is a T1 confirmation of a halocea enum **member** — 2276 stamps the identifier itself into a
string. It prompted a repo-wide member-name sweep: **89 enum member names** appear both in our
2276 assert/format strings and in halocea (`enum_members_shared_assert_proven.txt`), a much larger
T1 surface than the 40 shared bounds alone.

Two further slots got behavioural corroboration on our side: priorities 2/7/10 are exactly the set
allowed to interrupt a line already playing, and priority 6 is the single slot exempted from the
priority cap in `FUN_001a6b60` — what a designer-authored scripted line needs and no conversational
priority does.

Deliberately left as literals: the `result = 2` / `result = 3` return codes near line 1813 overlap
the priority value space but were not proven to *be* priorities; and the vocalization bound site
keeps `> 0xd0`, because spelling it `>= NUMBER_OF_VOCALIZATION_TYPES` would emit `CMP 0xd1`.

**Gate: all 208 VC71 scores byte-identical to baseline (re-run with `--no-cache`), `IMM-WARN` 2 =
baseline 2.** 22 call sites converted.

## Hazards

- **Line-counting a halocea enum is not reading it.** The first pass counted
  `unit_control_flags` at 17 members and reported an apparent divergence from our binary-proven 15.
  The two extra lines were trailing `UNIT_CONTROL_DRIVER_MASK` / `UNIT_CONTROL_GUNNER_MASK`
  convenience masks; the header's own `NUMBER_OF_UNIT_CONTROL_FLAGS = 0xF` agreed all along. Read
  the header before believing either an agreement or a divergence.
- **A byte-identical gate cannot catch a wrong name.** Substituting a same-valued but
  wrongly-named constant is byte-identical by construction. The gate proves the edit was
  codegen-neutral, nothing more — tier discipline is the only check on the name itself.
- **`__LINE__` shift.** Adding `#include` lines shifts `__LINE__` for every `assert_halt` below,
  changing immediates and moving VC71 across the whole TU. The `header-recovery` skill documents this
  and carries the byte-identical gate; follow it before adding headers to `src/`.
- **PPC vs x86 layout divergence.** Live against the upstream PPC corpus, reduced in the local
  Windows port (see the version table). Word-aligned scalars should transfer. Bitfields (allocation
  order) and 8-byte members (`double`, `int64` alignment) genuinely differ between the two ABIs.
  Confirm both classes independently, per struct.
- **VC71 bounds gate misfires on name-only changes.** Expect it during the pilot; a matching
  `ref_sha` is the proof the reference did not move.
- **Halocea names are not infallible.** Their own `actor_datum.h` records two usage-derived names a
  later ground-truth dump corrected (`flee_desire` → `forced_to_charge`,
  `path_unavailable`/`arrived` → `current_position_found_outside_range` /
  `moved_away_from_firing_position`). Where our 2276 access pattern disagrees with a halocea name,
  our binary wins, and the disagreement is itself worth recording.

## Naming-provenance policy (adopt before the first borrowed name lands)

Our `naming-confidence` tiers have no slot for "symbol evidence from a *different* binary". A name
lifted from halocea is neither `__FILE__`/assert evidence from 2276 nor behaviour evidence from our
own analysis, and once a few thousand of them are in the tree the two are impossible to separate.

Proposal: a `name_source` field on the `kb.json` function/field entry, or an explicit
`naming-confidence` tier, with at least these values:

- `binary` — proven from 2276 (assert string, `__FILE__`, PDB-equivalent evidence in our own binary).
- `halocea` — borrowed from the `0563` corpus, plausible but unconfirmed against 2276.
- `halocea+assert` — borrowed, then independently confirmed against a 2276 assert string or offset.
  The 41 shared `NUMBER_OF_*` identifiers are already in this class.

Compare the existing precedent: `known_globals.json` is cross-build and carries pool *names* only.
Same shape, same reasoning. The cost of adding the marker is zero at n=0 and very high to retrofit.

## Files here

- `tu_overlap.json` — per-TU counts: total functions, ported, still `FUN_`, halocea bucket size.
- `enum_bounds_shared_assert_proven.txt` — the 40 `NUMBER_OF_*` identifiers that appear both inside a
  2276 assert string on our side and in halocea. This is the cross-build-confirmed set.
- `enum_bounds_shared.txt` — 41 shared identifiers counting bare-identifier matches as well. Use the
  assert-proven file for evidence claims; this one only for coverage counts.
- `enum_bounds_halocea_only.txt` — 439 named in halocea with no counterpart on our side yet.

Regenerate the enum files with, from the repo root:

```bash
grep -rhoE '"[^"]*NUMBER_OF_[A-Z_0-9]+[^"]*"' src/ --include='*.c' \
  | grep -oE 'NUMBER_OF_[A-Z_0-9]+' | sort -u
```

against `grep -rhoE 'NUMBER_OF_[A-Z_0-9]+'` over the corpus's `src/engine`.
