# Raw-XBE oracle migration

Status: **steps 1-7 of 10 landed; `--oracle=xbe` is the default.**

| Step | What | State |
|---|---|---|
| 1 | `tools/equivalence/xbe_image.py`, one shared loader | done |
| 2 | `tools/equivalence/memmap.py`, layout moved out of the image span | done |
| 3 | oracle-side plumbing, unreachable | done |
| 4 | `--oracle={delinked,xbe}`, default `delinked` | done |
| 5 | globals seeded from the XBE, not only from the capture | done |
| 6a | oracle callees intercepted by patching the image | done |
| 6b | the data image shared with the candidate | done |
| 7 | A/B parity artifact, default flipped to `xbe` | done |
| 8 | regenerate the derived caches (`leaf_cache.json`) | pending |
| 9 | retire the allowlist in reviewed batches | pending |
| 10 | delete the delinked oracle | pending |

The go/no-go evidence is
`tools/equivalence/oracle_migration_expected_deltas.json`: every ported
function in `game_state.obj` under both oracles, 50 seeds, fixed base seed.
Five rows differ, all in the improving direction (three `error`/
`not_applicable` rows become verdicts, two gain coverage or confidence), and
none regresses. `tools/equivalence/test_oracle_ab_parity.py` keeps that
artifact honest and tied to the default.

Two things the migration surfaced that the plan did not predict:

- **Indirect calls are the hard part, not relocations.** With the image
  mapped, function-pointer globals hold real pointers, so the oracle makes
  calls that no relocation table ever described. `game_state_save` calls
  `[0x32eaa0]`; `game_state_call_after_load_procs` walks a 13-entry table at
  `0x32eaa8` through `call [esi]`. Both escaped into real engine code until
  `_pointer_table_callees` resolved them into the H9 intercept set. Five of
  those 13 pointers are real functions listed in neither `kb.json` nor
  `function_bounds.json`, which is why the entry test is "exact function
  entry for the FIRST dword, `.text` for the rest".
- **Enabling the gate is itself a finding.** `regression_test.py` asked only
  whether `delinked/` had an object. `delinked/` is gitignored and holds one,
  so all 72 targets reported SKIP and the gate passed by testing nothing. It
  now runs 71 pass / 1 awaiting triage.

## The idea

`tools/equivalence/unicorn_diff.py` (and `z3_equiv.py`) currently load the
**oracle** side of an equivalence check from a delinked `.obj` exported out of
Ghidra (`delinked/<object>.obj` or `delinked/functions/<hex>.obj`). Because a
delinked COFF is a *relocatable* object, everything it references outside its
own bytes — globals, switch-table data, other functions, internal labels —
arrives as an unresolved relocation that `unicorn_diff.py` has to synthesize
by hand before it can execute the code in Unicorn. That machinery is large:

- `_apply_oracle_switch_table_fixups` — reconstructs switch-table bytes the
  delinked COFF doesn't carry.
- `_relocate_rdata_text_refs` / `_relocate_text_label_refs` — patches DIR32
  relocations against `.rdata`/`.text` and internal `$LNNNNN` labels.
- `_build_globals_seeds` (see [[reference_equiv_oracle_reloc_gap]] in memory)
  — seeds DIR32-relocated globals into a synthetic "globals slots" region.
- ~90 lines of `delinked_path`/`build_path` pairing heuristics (around
  unicorn_diff.py:439-525) to guess which `delinked/*.obj` corresponds to a
  given kb.json object, because naming isn't 1:1 (multi-TU exports, stray
  `LIBCMT:` decorations, `delinked/functions/<hex>.obj` singletons, etc).

**The proposed change:** map the oracle's code directly from the *pristine*
XBE (`halo-patched/cachebeta.xbe` — NOT `default.xbe`, which is our patched
build; see Gotchas below) into Unicorn at its **real, original virtual
address**. Every absolute reference in Xbox debug-build code is already
correct at that address — no relocation of any kind is needed, because
nothing was ever unlinked from its neighbors. This is exactly why the
`--state-snapshot` path already works this way (it maps live captured memory
at real VAs), and why the equivalent migration already happened for VC71
byte-match scoring on 2026-08-09 (see Precedent below).

## Why this is worth doing

- Removes the single largest source of oracle-side false divergence: a
  relocation the synthesizer missed or seeded at the wrong width (see
  [[reference_equiv_oracle_reloc_gap]] — an 8-byte double seeded as 4 bytes
  produced a wrong epsilon and a wrong branch).
- Removes the delinked/build pairing heuristics entirely — there is nothing
  to pair; the oracle is just "this VA range of the one true XBE."
- Removes this lane's dependency on a live Ghidra bridge. Today's incident is
  a direct example: `delinked/game_state.obj` went missing from this host and
  regenerating it required a working `ghidra-live` MCP session, a correct
  export range, and hit an internal Ghidra exporter error
  (`buffer does not contain relocation`) on the first attempt. A raw-XBE
  oracle needs none of that — the XBE bytes are just read off disk.
- Kills the "too narrow / too wide" reference-bounding bug class
  ([[reference_delinked_ref_bounding]]) for the equivalence lane the same way
  the committed `function_bounds.json` + true-bounds detector already killed
  it for VC71 scoring.

## Precedent: how VC71 scoring already did this (2026-08-09)

`tools/verify/xbe_reference.py` is the model to imitate:

- `_xbe()` (line ~146) lazily loads the pristine XBE once via
  `check_delinked_bounds.load_xbe()` (in `tools/audit/`, not `tools/verify/` —
  this document had the path wrong), which parses XBE sections into a
  VA→file-offset map. As of step 1 that parser is a shim over the shared
  `tools/equivalence/xbe_image.py`.
- `_bounds()` / `bounds_entry()` (line ~186) load the **committed**
  `tools/verify/function_bounds.json` table — the single authority for where
  a function ends. Recomputing bounds at run time is kept only as a fallback
  with a loud warning, specifically because per-run drift in bounds was the
  root cause of the whole "too narrow/too wide" bug class.
- `function_bytes(addr)` (line ~273) returns the raw bytes for a function
  directly from the XBE image using that bounds table — no Ghidra call, no
  COFF, no relocation.

The equivalence lane's version of `_xbe()`/`function_bytes()` should reuse
these exact helpers rather than reimplementing XBE parsing.

## What does NOT change

- The **candidate** side stays a clang-built `.obj` that must still be loaded
  and relocated into Unicorn's address space (it has no fixed "real" VA of
  its own — it's freshly compiled, and its address only exists inside the
  emulator).
- Stub interception addresses move from *loaded-obj-relative offsets* to
  *original XBE VAs* — every existing stub/intercept table entry needs the
  same address-space translation.
- `--real-callees` (BFS-loading non-intercepted callees and running them
  natively, see [[reference_equiv_oracle_reloc_gap]]) still applies — it
  gets simpler, since a callee loaded at its real VA needs no relocation
  either, but the "which callees to pull in" logic is unchanged.

## Open questions / where to start

1. **Mapping granularity.** Map just the target function's bytes at its real
   VA (mirrors today's per-function delinked export), or map the *entire*
   pristine XBE image at its real base once per Unicorn instance? The latter
   is almost certainly simpler and more robust — it makes every absolute
   reference correct by construction (switch tables, rdata, sibling
   functions) with no bounds table needed for the oracle at all, at the cost
   of a larger one-time memory-map per run. Worth prototyping both.
2. **Which XBE.** Must read `halo-patched/cachebeta.xbe` (pristine,
   MD5 `c7869590a1c64ad034e49a5ee0c02465` per CLAUDE.md), never
   `halo-patched/default.xbe` (our patched build). Loading the patched XBE
   as the oracle would silently make oracle == candidate in every region
   we've already ported, hiding real divergences. This is the single most
   important correctness gotcha for whoever implements this.
3. **Self-modifying / patch redirects.** If the whole-image-mapping approach
   is chosen, confirm nothing in the patched build's own toolchain expects to
   patch memory the oracle emulator also maps (shouldn't apply — they're
   separate Unicorn instances — but verify before assuming).
4. **Stub/intercept table migration.** Enumerate every existing stub sentinel
   address in `stubs.py` and convert from obj-relative to VA — likely a
   mechanical pass once the addressing scheme is decided.
5. **`batch_verify_allowlist.json` chronic-timeout entries** (e.g.
   `game_state.obj @ 0x1bf8e0`, excluded for not terminating under the
   differential harness) are a pre-existing, unrelated concern — don't expect
   this migration to fix them, and don't let them block validating the
   migration itself.

## Related memory

[[reference_unicorn_z3_raw_xbe_oracle_followup]] (the original approved
follow-up; steps 1-7 have since landed),
[[reference_equiv_oracle_reloc_gap]], [[reference_delinked_ref_bounding]].
