# Raw-XBE oracle migration — work log

Written in Simplified Technical English. Short sentences. Active voice. One
idea per sentence.

## What the change is

The equivalence harness runs two emulators. One runs our lifted C code. The
other runs the reference, and we call that side the oracle.

The oracle used to be a Ghidra-delinked COFF object from `delinked/`. A
delinked object is relocatable. Every address outside its own bytes arrives as
an unresolved relocation. The harness had to synthesize each one by hand.

The oracle is now raw bytes from `halo-patched/cachebeta.xbe`. The harness maps
all 24 sections at their real virtual addresses. Nothing needs relocating.

Three facts made the change necessary:

1. `delinked/` is in `.gitignore`. On this checkout it holds one object with 20
   functions. The nightly batch job could test 20 of about 5700 ported
   functions.
2. 334 of 383 allowlist entries (87%) name a reference-availability or
   relocation problem. They are not lift bugs.
3. The VC71 scoring lane made the same move in August 2026. Its references come
   from the pristine XBE plus `tools/verify/function_bounds.json`.

## Status

| Step | Work | State | Commit |
|---|---|---|---|
| 1 | One shared XBE loader | Done | `19560a7a8` |
| 2 | Memory layout moved out of the image span | Done | `ed0d5e1bb` |
| 3 | Oracle-side plumbing, unreachable | Done | `ed0d5e1bb` |
| 4 | `--oracle={delinked,xbe}`, default `delinked` | Done | `8b87dbe59` |
| 5 | Globals seeded from the XBE | Done | `527fd28ed` |
| 6a | Oracle callees intercepted by patching the image | Done | `3f07dc8ee` |
| 6b | Image shared with the candidate | Done | `e43f020a6` |
| 7 | A/B gate, default flipped to `xbe` | Done | `7134469eb` |
| 8 | Derived caches regenerated | Done | `a8f88ec5c` |
| 9 | Allowlist retired in reviewed batches | In progress | — |
| 10 | Delinked oracle deleted | Not started | — |

## Step 1 — one shared XBE loader

Seven scripts each parsed the XBE header. One copy returned 3-tuples of
`(vaddr, vsize, raw_off)` with no `raw_size`. `.data` has a virtual size of
`0x36a918` but a raw size of `0x69a3c`. Writing `vsize` bytes would have copied
3.1 MB of unrelated file bytes over zero-filled memory.

`tools/equivalence/xbe_image.py` now holds one loader. It returns 4-tuples.
`raw_size` is mandatory.

The loader also asserts the MD5. The oracle must read
`halo-patched/cachebeta.xbe`, MD5 `c7869590a1c64ad034e49a5ee0c02465`. It must
never read `halo-patched/default.xbe`. The patched file contains our own lifted
code. An oracle that read it would equal the candidate in every ported region.
Every test would pass and prove nothing.

## Step 2 — memory layout

Three harness regions sat inside the image span:

| Region | Old base | Landed in |
|---|---|---|
| Stack | `0x00100000` | `.text` |
| Code | `0x00400000` | `.data` |
| Globals | `0x00500000` | `.data` |

`tools/equivalence/memmap.py` now holds the layout. The stack is at
`0x08000000`. Code is at `0x09000000`. Globals are at `0x0A000000`. Scratch
stays at `0x10000000`, because moving it would change pointer argument values.

This step changed no behavior. The gate was the old oracle staying green.

## Step 3 — the classifier

Raw bytes carry no relocation table. An empty relocation list used to read as
"this function makes no calls". That flipped three gates at once, and each one
then tested nothing.

`_classify_raw_oracle` recovers the same facts by disassembly. It counts direct
calls whose target leaves the function body. It counts absolute addresses
inside the image span.

## Step 4 — the flag

`--oracle` accepts `delinked` or `xbe`. The default stayed `delinked`.

The default stayed for a reason. Steps 4 to 6 landed the new path one piece at
a time. A flip before the evidence existed would have changed every verdict in
the repository with nothing to compare against.

## Step 5 — globals from the XBE

`_build_globals_seeds` used to seed each global from a 4-byte or 8-byte window.
It now reads 256 bytes from the XBE at the real address.

This retires a known bug class. An 8-byte double seeded as 4 bytes produced a
wrong epsilon. A wrong epsilon produced a wrong branch.

## Step 6a and 6b — symmetric interception

The oracle can call real engine code. The candidate calls return-0 stubs. That
compares our lift against our lift plus the whole engine, and every verdict is
then a false divergence.

Step 6a writes a 5-byte jump at each callee address inside the mapped image.
The jump goes to the same stub the candidate uses. The oracle's callee set is
now identical to the candidate's by construction.

Step 6b maps the image into the candidate's emulator too. Two results follow.
Memory traces compare one address set instead of two. Pointer returns compare
correctly.

Step 6b also has a cost. Data references on the candidate side now resolve to
real addresses. The candidate therefore reads real function pointers. Step 7
found the consequence.

## Step 7 — the A/B gate and the flip

The evidence file is
`tools/equivalence/oracle_migration_expected_deltas.json`. It records all 20
ported `game_state.obj` functions under both oracles, 50 seeds, one fixed base
seed.

Five results differ. Every one improves. None regresses.

| Function | Before | After |
|---|---|---|
| `FUN_001bf760` | inconclusive, 2.6% | pass, 100.0% |
| `game_state_save_to_persistent_storage` | error, 0.0% | inconclusive, 100.0% |
| `game_state_save_core` | error, 0.0% | pass, 73.5% |
| `FUN_001bfb60` | pass, moderate, 42.3% | pass, high, 100.0% |
| `FUN_001c00c0` | not applicable | pass, 48.0% |

`game_state_revert` keeps its status and gains coverage from 39.0% to 100.0%.
Nothing loses a verdict, coverage or confidence. That result licensed the flip.

`--oracle` now defaults to `xbe`.

### Three gaps the A/B sweep found

The mapped image makes function pointers real. Both sides can therefore call
through a pointer. The interception set only knew about calls named by an
instruction.

1. `call dword ptr [0x32eaa0]`. The harness saw a plain data reference.
   `game_state_save` escaped to `eip=0x1bf760`.
2. A 13-entry pointer table at `0x32eaa8`, walked with `call [esi]`.
   `game_state_call_after_load_procs` escaped to `eip=0x18ecd0`.
3. The jump was written into the oracle only. The candidate reads the same real
   pointer, so the two sides called different code.

`_pointer_table_callees` now resolves both pointer shapes. The jumps go into
both emulators.

The table entry test has two tiers. The first dword must be an exact known
function entry. Later dwords need only sit in `.text`. A stricter rule
truncated the real 13-entry table at 4 entries. Five of its pointers are engine
functions that this project never listed. Reading too far costs one unused
stub. Reading too little is a live escape and a lost verdict.

### Scoring honesty

Some seeds drive both sides out of the function body to the same address. Such
a seed carries no information either way. Counting it as an error penalized the
target for the seed generator's choice.

A new counter records these seeds as `domain_skipped`. The two sides must escape
to the same address. That condition is what stops the counter from excusing a
one-sided divergence.

`actor_action_replace_prop` went from 20 passed and 25 errors to 35 passed, 0
failed, 0 errors and 25 domain-skipped.

### The gate that tested nothing

`regression_test.py` asked one question before each target. It asked whether
`delinked/` held an object. `delinked/` is in `.gitignore`. On any other
checkout the answer is no.

All 72 targets reported SKIP. The gate returned 0. It had tested nothing.

The check is now per-oracle. The `xbe` lane needs the pristine image and a
committed bound. Both are in every checkout. The gate now runs 71 passed, 0
failed, 0 errors, 1 awaiting triage, 0 skipped.

### Two markers

`game_state_memory_pool_new` carried a `known_artifact` marker. The delinked
oracle returned to address `0x00000000`, because a sibling call's relocation
resolved to nothing. Under `--oracle=xbe` that sibling has real bytes. The
target passes 40 of 40 seeds at 100.0% coverage. The marker is gone.

`FUN_000d7cd0` carries a new `triage_pending` marker. The marker claims only
that a verdict is new and unread. It does not claim the harness is at fault.

A marked target that starts to pass is scored as a failure. Neither marker can
outlive its reason.

## Step 8 — the derived caches

`--batch-classify` used to iterate `delinked/*.obj`. It now iterates
`tools/verify/function_bounds.json` against the pristine XBE. It classified
7997 of 8011 bounded functions: 6736 stubbable, 694 data-only, 567 leaf.

It skips 14 bounds. Seven have no terminator. Four do not disassemble to a
terminator. Three are marked as table data. A class derived from a guessed
extent would look exactly like a reviewed one.

### Three defects the sweep found

**Two key forms.** The old sweep took its key from a delinked symbol name.
`FUN_00012000` became `"0x00012000"`, zero-padded. `_record_confidence` writes
`hex(addr)`, unpadded. 6125 padded rows sat beside 1278 unpadded rows. 1223
addresses existed in both forms.

`populate_regression_targets.py` looks each key up in an index built from
`kb.json`. Those addresses are unpadded. Every padded row was therefore
invisible to target selection. Nothing failed. The file quietly described 1278
functions instead of 7403.

**A wholesale merge destroyed the z3 worklist.** `existing.update(cache)`
replaced each row and dropped `z3_proven`. Those 58 flags are the worklist that
`revalidate_z3_proofs.py` reads. The sweep would have deleted the list before
anything re-checked it. The merge is now field-wise.

**Rows that assert nothing.** Stripping a measurement from a junk key leaves
`{}` behind. An empty row reads as a classified function. Those rows are now
dropped.

### Invalidated measurements

`oracle_func_size` changed from a delinked slice length to a bounds-table
length. Every pre-migration coverage number therefore has a different
denominator than the number it would be compared against.

`coverage_pct` and `confidence` were dropped from the 1066 rows measured under
the delinked oracle. A missing number reads as "not measured yet". A stale
number reads as fact. The nightly run refills them.

`_meta` now records the oracle, the schema version, the XBE MD5 and the counts.

### Z3 re-validation

58 cached proofs were re-run under the strict lifter. 49 survived. 9 lost the
flag.

The tool had one bucket for all 9 and called it "revoked". That was wrong for 6
of them. The tool now separates two cases:

| Verdict | Count | Meaning |
|---|---|---|
| Disproven | 3 | Z3 returned a counterexample. The proof is false. |
| Not re-established | 6 | The gate returned no proof and no counterexample. |

`tea_encrypt` and `tea_decrypt` also fail all 5 seeds. `ui_widget_find_by_tag`
has a counterexample at `tag_handle=0`, and its differential still passes, so
the counterexample may be outside the callable domain.

The 6 remaining flags were earned under the gate that step 3 fixed. The gate now
declines on code that contains calls. The flags still go, because `z3_proven`
asserts that a proof holds. Calling them failed proofs would overstate what is
known.

## Step 9 — the allowlist

`tools/equivalence/batch_verify_allowlist.json` excuses 383 targets. Each entry
carries a written reason.

| Count | Reason | Oracle-specific |
|---|---|---|
| 245 | The reference crashed. An unmapped callee page, or a relocation that resolved to nothing. | Yes |
| 71 | No delinked object existed, so no oracle could be built. | Yes |
| 39 | The run never stopped. | No |
| 20 | The candidate side failed to extract. | No |
| 7 | `deflate_state*`. A random seed dereferences the state struct at once. | No |
| 1 | Other. | Unknown |

The first two groups total 316. Neither problem can happen now.

Work done in this step:

1. An entry may carry an `"oracle"` field. The field scopes the excuse to one
   lane. An entry with no field applies to every oracle.
2. `tools/equivalence/retry_allowlisted.py` re-runs each entry under a chosen
   oracle and writes a CSV plus a JSON summary. It never edits the allowlist.

Work still to do:

3. Read the results. Delete entries in small reviewed batches. Each commit must
   cite the artifact.

The 39 timeouts and the 7 `deflate_state*` entries stay. The migration does not
touch them.

The retry tool reports four verdicts, not two:

| Verdict | Meaning |
|---|---|
| `now_passes` | The excuse is gone and the target is green. |
| `now_fails` | The harness ran and the two sides differed. |
| `now_runs_no_verdict` | The reference no longer crashes, but the run yields no evidence. |
| `still_errors` | No verdict and no run. The excuse still stands. |

`now_fails` is separate from `still_errors` on purpose. A recovered target that
found a real divergence must not read as the same infra problem it was
allowlisted for. Those rows need a human reader before anything is retired.

`now_runs_no_verdict` is separate too. Retiring one of those rows trades an
honest infra excuse for a permanently grey row.

### Retry results, 245 reference-crash entries

The sweep ran 245 targets under `--oracle=xbe`, 20 seeds each, in 479 seconds.

| Verdict | Count | Share |
|---|---|---|
| `now_runs_no_verdict` | 118 | 48% |
| `now_passes` | 69 | 28% |
| `now_fails` | 35 | 14% |
| `still_errors` | 23 | 9% |

222 of 245 targets (91%) no longer produce the error the allowlist excuses.
The excuse text is therefore false for those rows.

The 35 `now_fails` rows need a human reader before anything retires them. A
divergence is not the infra problem the row was written for. `weapon_try_place`
and `unit_exit_seat_end` are two of the 35.

The 118 `now_runs_no_verdict` rows all report `vacuous_output`. The reference
runs, and the seeds produce no unique return value and no memory variation.
Hazard H10 in the plan predicted this shape. `.data` is mostly zero at load, so
a NULL guard can take every seed down one path. Retiring one of those rows
would trade an honest infra excuse for a permanently grey row.

The artifacts are `artifacts/equivalence/allowlist_retry_xbe_oracle_unmappable.csv`
and the matching `.json` file. `artifacts/` is in `.gitignore`, so a retirement
commit must quote the numbers in its message.

### Stale allowlist names, 16 rows

Ten of the 23 `still_errors` rows reported `missing_kb_entry`. The name in the
row does not exist. A wider audit of all 383 rows found 16 such rows.

`batch_verify` finds a target by the name kb.json gives it. A row keeps the old
name after a rename. The row then matches nothing. Nothing fails. The row goes
inert, and the file still reads as an excuse.

| Row | kb.json name now |
|---|---|
| `FUN_000425c0` | `ai_handle_spatial_effect` |
| `FUN_000a9fd0` | `game_engine_test_flag` |
| `FUN_000a9ff0` | `game_engine_test_trait` |
| `FUN_000ad140` | `game_show_score` |
| `FUN_000b1f00` | `king_initialize_for_new_map` |
| `FUN_0010bdc0` | `point_in_rectangle3d` |
| `FUN_00114630` | `inflate_blocks_free` |
| `FUN_001146e0` | `inflate_codes_new` |
| `FUN_00114f60` | `inflate_codes_free` |
| `FUN_00117250` | `_tr_init` |
| `FUN_0012d5b0` | `network_game_server_get_oldest_client_update_received` |
| `FUN_00140750` | `objects_disconnect_from_structure_bsp` |
| `FUN_0019a490` | `file_create` |
| `FUN_001ab730` | `seat_label_to_base_seat_index` |
| `FUN_001ab770` | `base_weapon_label_get` |

One row drifted the other way. `ai_profile_change_render_spray` is a real PDB
symbol, but it belongs to the real ai_profile.c near `0x536xx`. So
`src/halo/ai/ai_profile.c` lines 608 to 616 returned `0x54a80` to
`FUN_00054a80`. The row kept the withdrawn name.

All 16 rows name a function with `ported: true`.

`tools/equivalence/test_allowlist_names.py` is the gate. It holds 6 tests. Each
row must name a kb.json function. No row may use a `FUN_` name whose address
carries a real name. Every row needs a written reason of more than 20
characters. An `oracle` scope must read `xbe` or `delinked`. A mutation check
showed that the gate fails on the defect it was written for.

### Retry results, the 16 renamed rows

`retry_allowlisted.py` gained a `--names` flag, because a rename batch cuts
across categories. The 16 ran under `--oracle=xbe`, 20 seeds, in 49 seconds.

| Verdict | Count | Rows |
|---|---|---|
| `now_runs_no_verdict` | 7 | all report vacuous output or vacuous coverage |
| `now_passes` | 4 | `_tr_init`, `base_weapon_label_get`, `point_in_rectangle3d`, `seat_label_to_base_seat_index` |
| `still_errors` | 4 | the 3 zlib inflate rows, and `FUN_00054a80` |
| `now_fails` | 1 | `file_create`, a divergence |

The 4 passing rows are gone. One fact made that safe. Only `FUN_00054a80` of
the 16 appears in the 787 rows of `batch_verify_baseline.json`, so the delinked
corpus never reached the other 15. Deleting a row therefore does not un-mute a
gate that was green. It deletes an excuse that nothing needs.

The other 12 rows stay. Each one gained a note that records the retry date, the
oracle, and the measured verdict. The original claim stays in place, because a
reason is an audit trail.

`file_create` needs a reader. Its row says the reference crashes. The reference
no longer crashes, so the row is false. The replacement verdict is a
divergence, which is not the problem the row was written for. The note says so
and calls the row a triage item, not an excuse.

The allowlist now holds 379 rows.

### Scoping the unmappable passes, 67 rows

The 69 `now_passes` rows from the 245-row sweep were audited. 67 rows have
proven coverage (averaging 65.8%). Two rows (`actor_look_compute_prop_interest`
and `game_engine_post_rasterize_post_game`) had 0.0% coverage because all seeds
were `domain_skipped` to an unmapped callback or early exit; they stay excused as
inconclusive rather than claiming a false pass.

All 67 solid passes gained `"oracle": "delinked"` and a note citing the artifact.
They are now active under the raw XBE oracle and continue to excuse the delinked
lane until step 10.

### Relabeling the 8 un-emulatable system functions

Eight rows that still errored are permanently un-emulatable in an isolated zero-fill
harness: `actor_delete_props`, `director_initialize_for_new_map`, `director_update`,
`main`, `path_state_new`, `system_get_used_memory_size`, `system_malloc`, and
`update_loaded_module_section_attributes`.

Their reason was rewritten from the obsolete delinked reference-crash text to state
the permanent truth: "not differentially testable: allocator / state initializer /
memory protection / CRT entry point". They carry no oracle scope and remain excused
everywhere.

Five concrete error rows gained specific failure reasons: `FUN_0003dc20`,
`FUN_000d7080`, and `FUN_000d7d10` hit `insn_limit` (assert->stub loop); `FUN_000b3770`
and `FUN_0011c4d0` hit unmapped callbacks/fetches (`eip=0x0`).

### Sweep results, 68 `oracle_extract_failed` entries

Three of the original 71 rows were retired in the rename pass (`seat_label_to_base_seat_index`,
`base_weapon_label_get`, `point_in_rectangle3d`). The remaining 68 ran under `--oracle=xbe`,
20 seeds each, in 60.0 seconds:

| Verdict | Count | Share |
|---|---|---|
| `now_passes` | 44 | 65% |
| `now_runs_no_verdict` | 18 | 26% |
| `now_fails` | 4 | 6% |
| `still_errors` | 2 | 3% |

Every one of the 44 `now_passes` rows has proven coverage (>0%). All 44 gained
`"oracle": "delinked"` and a note citing `artifacts/equivalence/allowlist_retry_xbe_oracle_extract_failed.{csv,json}`.

The 4 `now_fails` are real divergences needing triage: `FUN_00188d00`,
`object_detach_from_parent`, `object_get_markers_by_string_id`, and `object_set_position`.

The 2 `still_errors` are `system_calloc` (an allocator, relabeled permanently) and
`FUN_00084ae0` (escapes to CRT code at `0x1d0581`).

111 of 379 allowlist rows (29%) are now scoped to `delinked`, leaving 268 active
excuses under `--oracle=xbe`.

### Two harness fixes found during triage

1. `abi.py` parameter parsing used `\(([^)]*)\)\s*;?\s*$` which failed on function
   pointer parameters like `void (*callback)(void)`. It fell back to `params: []`,
   pushing 0 stack arguments to multi-argument functions. It now extracts between
   the first `(` and last `)`.
2. `unicorn_diff.py` fell through to `status="pass"` when `passed == 0` if all seeds
   were `domain_skipped` (since `failed == 0` and `errors == 0`). It now explicitly
   reports `inconclusive` when `passed == 0`.

### Triage of the 35 `now_fails`: Data Iterators and HUD Inlining

The 35 `now_fails` were triaged by evidence strength rather than alphabetic order. The highest-value cluster was the `0xd6e50–0xd8cf0` neighborhood and the `data_t` iterators (`data_next_index`, `data_prev_index`).

1. **`data_next_index` (`0x1198f0`, `src/halo/memory/data.c`)**:
   - **Diagnosis**: Not a harness pointer-seeding asymmetry, but a real compiler defect. Clang optimized the signed 32-bit loop with an extraneous local variable into a 16-bit `decw %cx` countdown loop with `%edi = -index` and `subl %edi, %eax`. When traversing arrays > 65,535 items, `%cx` wrapped, terminating early and returning `0x00000000` instead of the found handle (`0x7257d305` in oracle).
   - **Fix**: Removed the extra `int16_t index` variable and incremented `prev_index` in place, directly matching the original binary structure (`inc esi; test si, si`).
   - **Result**: Raw-XBE equivalence reached 91/100 passes (95.5% coverage).

2. **`data_prev_index` (`0x119980`, `src/halo/memory/data.c`)**:
   - **Diagnosis**: Decompiled C used multi-step casts and messy temporaries (`psVar1`, `sVar2`, `uVar3`).
   - **Fix**: Refactored to match Bungie's original `do { ... } while (index-- >= 0);` loop idiom.
   - **Result**: VC71 operand-normalized match jumped from 79.3% to **92.5%** (+13.2 pp), instruction match improved from 93.7% to **94.3%** (+0.6 pp), and raw-XBE equivalence achieved **19/20 passes** (87.1% coverage). `score_improve.py check` verified 0 regressions across all 27 functions in `data.c`. Committed in `f18eb4687`.

3. **`FUN_000d7cd0` (`0xd7cd0`, `src/halo/interface/hud_messaging.c`)**:
   - **Diagnosis**: In `cachebeta.xbe`, `0xd7cd0` calls `FUN_000d7280` (`unit_hud_get_slot`) out-of-line (`call 0xd7280`). In `hud_messaging.c`, clang inlined `FUN_000d7280`. `FUN_000d7280` asserts `*(int*)0x46bd20 != 0` (`unit_hud_globals`). In the test harness, uninitialized memory at `0x46bd20` is 0, causing the candidate to halt on `display_assert`. The oracle never asserted because `0xd7280` was intercepted as an external callee stub.

## The progress dashboard

A read-only audit checked `tools/report/` against the new cache schema. The
dashboards do not crash. Every read uses `.get()` with a fallback. They do
report two wrong numbers.

**The front-page equivalence card under-reports.**
`generate_ci_status.py` averages `coverage_pct` over every row in
`leaf_cache.json`. Before step 8, up to 1066 rows carried that number. Now 60
do. The card therefore shows an apparent 94% collapse. No code regressed. Step
8 reset the field, and the field is a measurement.

**The `ci.html` confidence bar has the same defect.**
`generate_ci_status.py` holds a second loop over the same cache. The two loops
share no code, so each one needs the same fix. Both loops also count `_meta` as
one uncached function.

**The numbers do not heal on their own.** `unicorn_diff.py` writes
`coverage_pct` back only under `batch_verify.py --update-leaf-cache`.
`.github/workflows/equivalence.yml` does not pass that flag. The depressed
numbers therefore stay until someone runs a re-measurement pass.

The per-function lookup in `generate_decomp_report.py` needs no change. It reads one
key as `f"0x{addr:x}"`, which is the canonical form.

Three fixes follow from the audit:

1. Skip `_meta` in both loops. `generate_decomp_report.py` line 128 already
   holds the pattern to copy.
2. Relabel the card while the cache is mostly unmeasured. Show the measured
   count against the bounds count instead of an average.
3. Add `--update-leaf-cache` to a scheduled `batch_verify.py` run.

No CI job asserts on report content. No job fails on these numbers.

## Step 10 — delete the old oracle

This step runs only after steps 7 to 9 stay green for a full nightly cycle.

Delete from `unicorn_diff.py`:

- `_find_obj_paths`
- `_per_function_ref`
- the `CoffParseError` cascade
- `_apply_oracle_switch_table_fixups`
- the `--oracle` flag
- `DELINKED_DIR`

Delete from `stubs.py` the oracle path through `_load_callee_code` and
`_redirect_raw_calls`.

Delete from `.github/workflows/equivalence.yml` the symlink steps and the
`DELINKED_DIR` variable.

Keep `coff_loader.py` and `slice_looks_truncated`. The candidate side still
needs both.

After step 10 the equivalence lane needs two committed inputs. It needs the
pristine XBE and the bounds table. It needs no Ghidra.

## Open items

| Item | Kind |
|---|---|
| `FUN_000d7cd0` divergence. Both sides call `datum_get` with identical arguments. The oracle returns early. The candidate does not. | Harness, mechanism unknown |
| `game_state_malloc`. 9 of 20 write-trace differences. The oracle stores to `[0x4ea990]` at `0x1bfc94`. The candidate does not. | Lift question |
| 3 disproven z3 proofs. `tea_encrypt`, `tea_decrypt`, `ui_widget_find_by_tag`. | Lift question |
| The progress dashboard under `tools/report/`. It may read the old cache schema. An audit is running. | Reporting |

## Test gates added

| File | Tests | What it pins |
|---|---|---|
| `test_xbe_image_map.py` | — | 24 sections, BSS reads zero, the MD5 |
| `test_memmap.py` | — | Regions disjoint, none inside the image span |
| `test_raw_oracle_classify.py` | 41 | The classifier agrees with the relocation table |
| `test_known_globals_vs_xbe.py` | — | No seed writes over real image bytes |
| `test_oracle_intercept.py` | 26 | The jumps go into both emulators |
| `test_shared_data_image.py` | 17 | One address set, no unresolved data references |
| `test_oracle_flag.py` | 16 | The default is `xbe` |
| `test_oracle_ab_parity.py` | 13 | The 5 delta rows reproduce live |
| `test_escape_guard.py` | 21 | The marker discipline |
| `test_leaf_cache_schema.py` | 15 | Canonical keys, the `_meta` stamp, the z3 floor |

`run_all_tests.py` reports 28 passed, 1 skipped, 0 failed.
