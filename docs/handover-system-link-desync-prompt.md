# Handoff prompt: system-link desync investigation (pre-worked)

Written: **2026-09-12**. This is a ready-to-paste investigation prompt produced
after pre-investigative work on [`system-link-rng-desync.md`](system-link-rng-desync.md).
The "PRE-VERIFIED STATE" section records facts confirmed against the repo at
HEAD on this date (kb.json, `tools/verify/vc71_scores.json`,
`artifacts/score_context/normalize3d.json`, `src/halo/math/vector_math.c`,
`src/x87_math.h`, git status). The authoritative investigation doc remains
`system-link-rng-desync.md`; if the two disagree, trust that doc plus fresh
verification.

---

```
MISSION: Find and fix the root cause of the system-link desync described in
docs/system-link-rng-desync.md (patched client vs cachebeta-derived host,
Halo Xbox debug build 2276, halo-patched/cachebeta.xbe MD5
c7869590a1c64ad034e49a5ee0c02465). RNG divergence is a downstream symptom;
upstream float/collision drift is the cause class.

READ FIRST (in order):
1. docs/system-link-rng-desync.md — authoritative state, evidence, limits,
   and the 6 resumption rules in "If investigation is resumed". Obey them.
2. artifacts/rng_trace/system_link_bisect/evidence_priorities.json —
   per-root surviving-redirect lists for 0x1a2f40, 0x1a5300, 0x1443f0,
   0x141b70, 0x14dce0, 0x14cb00.
3. docs/system-link-architecture.md — netcode is server-authoritative
   deterministic lockstep; the desync doc is authoritative for this bug.

PRE-VERIFIED STATE (do not re-derive):
- kb.json: FUN_001a2f40 ported:false (allowlisted diagnostic fallback);
  FUN_001a5300 never lifted (runs original). The six direct callees of
  FUN_001a2f40 are all ported:true in src/halo/math/vector_math.c:
  0x12170 FUN_00012170, 0x121a0 distance_squared3d, 0x12f10 magnitude3d,
  0x12f80 vector3d_scale_add, 0x12fe0 FUN_00012fe0, 0x13010 normalize3d.
- VC71 (tools/verify/vc71_scores.json): five of the six score 100%.
  normalize3d (0x13010) scores 79.1% with FCOM-WARN (ref=synth, n_r=40).
- Score-context diff (artifacts/score_context/normalize3d.json) shows our
  build vs the original at 0x13010-0x1306f:
    * ours: fstps [ebp-4]; push; CALL fabsf-libcall; jne; fdivs [ebp]
      (mag stored to memory, float-narrowed)
    * original: fld %st(0); fabs (inline, no call); jnp (FCOM/parity
      semantics); fdiv %st(1),%st (mag kept WIDE on the x87 stack)
  => potential ULP-level divergence in normalize3d's outputs (v[0..2]),
  on the exact physics path (FUN_001a2f40 -> ... -> FUN_001a5300) that
  ds93/ds94 localized early position drift to. THIS IS THE PRIMARY LEAD.
- Sibling magnitude3d (0x12f10) already uses the correct idiom —
  x87_fabs(mag) compared against the double at 0x2533d0 — and scores
  100%. Use it as the template. House idioms: src/x87_math.h
  (x87_fabs, x87_wide_t, HALO_NARROW, HALO_FLT_ROUNDTRIP).
- The worktree is NO LONGER dirty (the desync doc's caution is stale):
  src/ and kb.json are clean at HEAD. Untracked: docs/system-link-
  architecture.md (context) and a junk file named "=90%" (do not touch
  or commit it).
- HALO_RNG_TRACE instrumentation is committed across ~10 source files
  (units, bipeds, objects, damage, projectiles, game, math, ...).
  Do not remove it as part of this fix; keep logic changes separate.
- Bisect tooling and frozen ds103-ds110 artifacts intact under
  artifacts/rng_trace/system_link_bisect/. WARNING: re-running
  build_variants.py can overwrite the evolving manifest.json.

TASKS (bounded, binary-backed, in order):
1. Ghidra: read the true disassembly of normalize3d at 0x13010-0x1306f
   (pristine XBE + tools/verify/function_bounds.json; entry exists).
   Confirm the ref sequence end-to-end: FLD/FLD ST(0)/FABS, the FCOM
   against the double at 0x2533d0 and its FSTSW/SAHF + JNP polarity,
   the wide-mag divide (FDIV %st(1)), and the epilogue (FSTP %st(0);
   return of 0.0 via which constant). Do not trust the synth-ref
   alignment alone. Run tools/audit/check_ghidra_mcp.py first.
2. Fix normalize3d in src/halo/math/vector_math.c to reproduce the
   original exactly: inline x87_fabs (no libcall), threshold compared
   as double, mag kept wide across the divide (x87_wide_t), correct
   branch polarity, correct zero-return form. Keep the existing
   evidence comments; extend them with the new disassembly findings.
   This is an already-lifted function: follow the score-improve/lift
   discipline — run
     rtk python3 tools/lift_pipeline.py --target normalize3d ...
   or the /lift workflow, then vc71_verify on vector_math.c, hazard scan
   (check_lift_hazards.py --changed-only), and XCALL type audit if you
   touch any macro. Only accept binary-backed edits; keep the change
   minimal and separate from any cleanup.
3. While in Ghidra, verify the two remaining static claims in the same
   file that bear on this path (cheap, same session):
     a. magnitude3d (0x12f10) really operates only on v[0]/v[1] (2D)
        despite the kb.json name — the lift says so; confirm against
        bytes. If it actually reads v[2], that is a second real defect.
     b. distance_squared3d (0x121a0) accumulation order is
        dz*dz + dx*dx, then + dy*dy (comment claims disassembly order).
4. Only after the static fix lands and builds: runtime validation per
   the desync doc's resumption rules —
     - Build, hash the EXACT XBE, deploy via WSL XBDM with
       HALO_WINDOWS_REEXEC=1 to the title path
       E:\GAMES\halo-patched\default.xbe, require completed --sendfile,
       then magicboot launch, then a matching verify_live.py check
       BEFORE requesting play. Uploaded-file identity is not runtime
       identity.
     - Topology: client 10.0.0.21, host 10.0.0.25 (HMP 4444/4446; QMP
       not present on those ports).
     - Record scenario, completed exercise duration, build identity,
       and the actual game-reported/visible failure. Leave the test
       undisturbed until failure or explicit completion.
     - You need a duration-matched control (ds107 failed slowly; a
       short clean run proves nothing). Do not resume function-count
       halving and do not add one-off float watchers.
     - Do not use watch_object_translate_diff.py as the final oracle;
       do not call a higher match score or a finite stable interval a fix.
5. If normalize3d does NOT explain the failure after a properly
   controlled run, fall back to the evidence_priorities.json survivor
   lists (next: the distance-1 callees of 0x1a5300, then 0x1443f0's
   collision-path survivors). State what each proposed test
   distinguishes before running it.

HARD CONSTRAINTS:
- Binary is source of truth; no speculative behavior or names.
- C89 only; ABI/@<reg> in kb.json immutable; no inline-asm outside the
  src/x87_math.h pattern (GCC-style asm volatile with clobber lists;
  FPU-only asm is safe).
- kb.json: rtk jq only. Preserve the existing 0x1a2f40 ported:false
  allowlisted deactivation; do not add deactivations.
- Commit per repo discipline (generate_lift_commit.py); never commit
  the "=90%" file; scoped diffs to src/ kb.json.
- Do not rebuild or redeploy the host incidentally; deployments are
  XBE-only; do not change HDD init.txt.
```
