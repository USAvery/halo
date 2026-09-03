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

## Batch 3 — `src/halo/tag_files/files.c` (2026-09-03)

28 scored functions, no `__LINE__`. Four flag families, all four bounds proven from 2276 before
the corpus was consulted, **all four agreeing**: reference-info 1 (`& 0xfffe`), name 4
(`& 0xfff0`), find-files 2 (`& ~3`), permission 3 (`& ~7`).

This TU is the best case for the lane so far, because most **member** names are T1 — 2276 stamps
the identifiers into assert strings *and* those asserts pin the bit values:

| member | 2276 evidence | bit |
|---|---|---|
| `_has_filename_bit` | `!TEST_FLAG(info->flags, _has_filename_bit)` at `files.c:0x8a`, guarding `& 1` | 0 |
| `_name_directory_bit` / `_name_extension_bit` | `flags!=(FLAG(_name_directory_bit)\|FLAG(_name_extension_bit))` at `0xbc`, guarding `== 9` | {0,3} |
| `_name_parent_directory_bit` | `_name_directory_bit` vs `_name_parent_directory_bit` at `0xbd`, guarding `(flags&1)&&(flags&2)` — fixes directory=0, hence extension=3 | 1 |
| `_permission_read_bit` / `_permission_write_bit` | `flags & (FLAG(_permission_read_bit)\|FLAG(_permission_write_bit))` at `files_windows.c:0x135`, guarding `& 3` | {0,1} |
| `_permission_append_bit` | `_permission_write_bit` vs `_permission_append_bit` at `0x136`, guarding `(flags&2)==0 && (flags&4)!=0` — fixes write=1, hence read=0 and append=2 | 2 |

`_name_filename_bit` is **T1 by exhaustion**, and the mechanism generalises: bits 0, 1 and 3 are
each named verbatim above, and `NUMBER_OF_NAME_FLAGS` is exactly 4 (proven by the `0xfff0`
reject), so bit 2 is forced. Both halves are load-bearing — the partial member names alone would
not pin it without the bound, and the bound alone would not name it. Where a family has T1 bounds
plus T1 members for all but one slot, the remainder is T1, not a borrowed name.

Behavioural corroboration on our side: permission bit 0 maps to `GENERIC_READ` (0x80000000), bit 1
to `GENERIC_WRITE` (0x40000000), and bit 2 to a seek-to-end — exactly read/write/append.

`find_files` members carry no 2276 string and stay `name_source: halocea`, T2.

**Gate: all 28 VC71 scores byte-identical to baseline (`--no-cache`), `IMM-WARN` 1 = baseline 1.**
The `flags == 9` site was the one composite-value substitution and it folded cleanly.

Deliberately not done here: the existing `FIND_FILES_RECURSIVE_BIT` / `FIND_FILES_DIRECTORIES_BIT`
defines hold *masks* under a `_BIT` name. Correcting that is a rename across 6 sites, not a
constants change; mixing it in would have made any gate movement unattributable. Follow-up commit.
Likewise the `_has_filename_bit` conversion covers only the two assert-adjacent sites — the other
five read `ref->unk_4[0]`, an unnamed byte array, where naming the bit while the container stays
anonymous would read as more recovery than was done. Those want a `structize` field split first.

## Batch 4 — `src/halo/game/players.c` (2026-09-03)

Gate: 245 of 245 scored functions byte-identical to the pre-edit baseline, `IMM-WARN` 0
(`vc71_verify.py --no-cache` both sides).

One family borrowed, three bounds already present and now used by name:

| Family | Bound | Where the bound is proven | Tier |
|---|---|---|---|
| `player_powerup` | `NUMBER_OF_PLAYER_POWERUPS = 2` | assert at `players.c` 0xaea reads `powerup_type<NUMBER_OF_PLAYER_POWERUPS` against a `cmp 2`; `player_update_weapon_timers` walks exactly two `int16_t` from `player+0x68` | T2 (`name_source: halocea`) |
| `NUMBER_OF_UNIT_GRENADE_TYPES` | 2, already in `types.h:270` | assert text `desired_grenade_index <= NUMBER_OF_UNIT_GRENADE_TYPES` | T1 |
| `MAXIMUM_WEAPONS_PER_UNIT` | 4, already in `types.h:269` | assert text `desired_weapon_index <= MAXIMUM_WEAPONS_PER_UNIT` | T1 |

13 sites converted: the powerup guard and its slot-0 branches in `player_handle_powerup`,
`player_set_respawn_timer`, `player_update_weapon_timers`, the two camo flag helpers
(`FUN_000bb1c0` / `FUN_000bb1f0`), the equipment-powerup slot mapping at 0xac7, and the four
grenade/weapon range guards.

Two findings worth carrying forward:

- **Display-string-to-slot-offset pairing is a real corroboration class, and it is T2, not T1.**
  `hud.c` formats `"ACTIVE-CAMOUFLAGE "` from the `int16_t` at `player+0x68` and
  `"FULL-SPECTRUM VISION "` from the one at `player+0x6a`, which fixes slot 0 = active camouflage
  and slot 1 = full-spectrum vision *from our own binary*. But display text is not an identifier:
  the spellings `_player_powerup_active_camouflage` / `_player_powerup_full_spectrum_vision` are
  still borrowed, so `name_source: halocea` (T2) with the mapping cited. This is deliberately
  *not* batch 3's T1-by-exhaustion, where the identifiers themselves were in assert strings.
  The class should generalize — many of the remaining families have HUD or console labels.
- **A TU with `assert_halt` cannot take a top-of-file insertion.** `players.c` has four
  `assert_halt(...)` sites (lines 143/160/495/1919) which stamp `__LINE__`; any inserted line above
  one changes its immediate and moves VC71 across the whole TU. The enum therefore went into the two
  force-included headers (`src/common.h` for clang `-include`, `src/xdk_common.h` for VC71 `/FI`) —
  the same routing `common.h` already documents for the `TAG_GROUP_*` codes. Adding lines to an
  included header does not shift `__LINE__` in the includer. Verified no header included *after* the
  insertion point (`nv097.h`) contains an assert of its own. Both edits to the two `ctl.` guards
  wrap onto a second line, which is why they were placed below line 1919.

Not done here: the `assert_halt(respawn_type >= 0 && respawn_type < 2)` at `players.c:1919` keeps
its literal. `assert_halt` stringizes its condition, so naming the bound would change the `.rdata`
string length and shift every string address below it in the TU — a whole-TU immediate change for
one site. `MAXIMUM_NUMBER_OF_LOCAL_PLAYERS` needed no work: already `#define`d 4 in both
force-included headers and already used by name at all seven sites. Note that the shared-bounds
extraction matched it on the `NUMBER_OF_` substring, so that queue row was a prefix artifact rather
than a distinct family — re-rank remaining candidate TUs with word-boundary matching.

## Batch 5 — `enum equipment_powerup_type` (2026-09-03)

Gates: `players.c` 245 of 245 scored functions byte-identical, `IMM-WARN` 0; `units.c` 208 of 208
byte-identical, `IMM-WARN` 2 (both pre-existing, unchanged). All measured `--no-cache` both sides.

Prompted by a question that turned out to expose a missing distinction: *isn't overshield a powerup?*
It is — but not a `player_powerup`. Batch 4's enum indexes the two-entry `int16_t` timer array at
`player+0x68`, and a slot exists there only for a powerup needing a per-player countdown. Overshield
and health are instant unit-state changes; double speed accumulates on a *global* timer at
`players_globals+0x26`. Only camo and full-spectrum vision get slots, which is why the
binary-proven bound of 2 is not evidence of anything missing.

The enum overshield actually belongs to is the `'eqip'` tag field at +0x308, now
`enum equipment_powerup_type` in `types.h`. Evidence splits by value:

| Value | Name | Evidence | Tier |
|---|---|---|---|
| 0 | `_equipment_powerup_none` | `units.c` 0x1ca1 assert `powerup_type!=_equipment_powerup_none` guards `== 0` | **T1** |
| 6 | `_equipment_powerup_grenade` | `units.c` 0x1c72 (`==`, guards `!= 6`) and 0x1ca2 (`!=`, guards `== 6`) | **T1** |
| 1-5 | `_double_speed`, `_over_shield`, `_active_camouflage`, `_full_spectrum_vision`, `_health` | halocea DB-verified (`types_enum_values _270498BB874CAD5ECABAECA7DA81ECAE`); meaning confirmed independently by our dispatch routing each to an already-named handler | T2 |

8 sites converted: 5 in `player_set_action_result_for_equipment`, 3 in the `units.c` equipment
asserts.

Two results worth keeping:

- **2276 names the prefix, so halocea's rename is rejected.** Their header states plainly that the
  DB enum uses `_equipment_powerup_*` and that its own `_powerup_type_*` identifiers are
  "consumer-facing names" — i.e. halocea's editorial choice, not a recovered symbol. Three 2276
  assert strings spell `_equipment_powerup_none` / `_equipment_powerup_grenade` verbatim, which
  settles it: we take the DB prefix. Borrowing the consumer-facing form would have laundered a
  halocea rename as recovered evidence.
- **The initial triage of this family was backwards.** Before reading the code, values 0 and 6 were
  written off as "no 2276 evidence at all" and 1-5 treated as the confirmed part. The reverse is
  true: 0 and 6 are the only two the binary names outright. Grepping `src/` for the halocea
  identifier before planning the batch would have caught it — `units.c` already had all three
  strings, and two doc comments already referenced `_equipment_powerup_grenade (6)` by name.

Not adopted: `NUMBER_OF_POWERUP_TYPES = 7`. Six is the largest value our binary compares against,
which does not prove seven is the count, and batch 3's T1-by-exhaustion needs an independently
proven bound — there is none here. No site needs it.

## Hazards

- **A halocea "consumer-facing" name is not a recovered name.** `equipment_powerup_type.h` renames
  the DB's `_equipment_powerup_*` members to `_powerup_type_*` and says so in its own provenance
  line. Borrowing the visible identifier rather than the one the header cites promotes a corpus
  editorial decision to T2 evidence. Read which name the provenance line attributes to the DB.
- **Grep `src/` for the halocea identifier before planning a batch.** Batch 5's two T1 names were
  already sitting in `units.c` assert strings and doc comments; the batch was scoped from the
  halocea header alone and got the evidence split exactly inverted. One `rtk rg` reorders the whole
  tier table.
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
