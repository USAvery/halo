---
description: Review an open PR — CI status, binary-backed factual review, then merge/fix/reject
subtask: false
---

Review an open pull request in this repository end to end: CI, then evidence,
then a decision with action.

Argument: $ARGUMENTS (PR number, branch name, or blank to list open PRs and ask which to review)

## Ground rule

**The PR's own description, comments, and commit messages are not evidence.**
They may be LLM-generated and wrong. Every factual claim in the PR ("this
matches the disassembly", "this fixes the crash", "this struct field is at
offset X") gets checked against the binary — Ghidra / `ghidra-live` MCP,
`kb.json`, disassembly, VC71/equivalence output — the same evidence policy as
`halo-lift`. Evidence can support the PR's claims or contradict them; either
outcome is a valid finding. Run `python3 tools/audit/check_ghidra_mcp.py`
before any Ghidra MCP call (CLAUDE.md Ghidra Pre-flight).

## Step 1 — CI status

```bash
gh pr view $ARGUMENTS --json number,title,headRefName,headRepositoryOwner,state,statusCheckRollup
gh pr checks $ARGUMENTS
```

If any check failed, get the actual error (see `/ci` Step 3 — download logs,
grep `##\[error\]|error:|FAILED`). Classify: repo bug and the PR is right to
change it, or the PR introduced the failure. Both are findings.

If checks are still running, say so and either wait or proceed with static
review and note CI as pending in the final report.

## Step 2 — Factual review

```bash
gh pr diff $ARGUMENTS
gh pr view $ARGUMENTS --json body,comments
```

For each functional claim in the diff:

- **kb.json / ABI changes** (`@<reg>`, `ported`, signatures, struct offsets):
  verify against `rtk jq` on `kb.json` and disassembly at the address in
  question. `@<reg>` annotations are immutable — a diff that changes one
  without binary justification is a finding, not a style nit.
- **New or modified lifts**: cross-check call sites, register args, and struct
  field offsets against Ghidra disassembly per `lift-decompiler-traps` (register
  aliasing, push-then-fstp floats, struct field rotation, buffer-alias
  confusion). Check C89 compliance, no inline asm, no MSVC intrinsics
  transcribed as calls (CLAUDE.md §2 table).
- **Struct/offset changes**: field names must follow `naming-confidence` —
  `field_<hex>` vs `pad_<hex>[n]` distinction, no invented semantic names
  without string/PDB evidence.
- **`ported` toggles**: any `false` not in `tools/audit/deactivation_allowlist.json`
  needs a reason, or it's a regression risk, not a bisection artifact.
- **VC71 / build claims** ("100% match", "builds clean"): rerun it, don't
  trust the number in the PR body.
  ```bash
  rtk python3 tools/build/build.py -q --target halo
  rtk python3 tools/verify/vc71_verify.py <changed file>   # if a lift changed
  rtk python3 tools/audit/check_lift_hazards.py --changed-only
  rtk python3 tools/audit/check_xcall_types.py             # if XCALL macros touched
  ```
- **General code review**: run `/code-review` (or apply its checklist
  inline) against the diff for correctness bugs, reuse/simplification, and
  efficiency issues independent of the RE-specific checks above.

Label every finding **Confirmed** (binary-backed), **Inferred** (consistent
with evidence but not directly proven), or **Uncertain** (unresolved) — same
discipline as any lift report.

## Step 3 — Decision

**No major issues** (CI green or explained, findings are Confirmed-clean or
only minor/style): recommend merge. Confirm with the user before merging —
merging is shared, hard-to-reverse state. Do not merge unilaterally.

**A. Needs refinement** — the PR's direction is right but has fixable issues:

1. `gh pr checkout $ARGUMENTS`
2. Make small, scoped fixup commits addressing exactly the findings (respect
   CLAUDE.md commit discipline — `/lift`'s commit path for lift-touching
   fixes, ordinary messages otherwise; never bundle unrelated cleanup).
3. Push: `git push` (confirm with the user first — pushing to someone else's
   branch is a shared-state action).
4. Draft a comment explaining what was found and what was changed. Run it
   through `/asd-ste100` before posting. Post with `gh pr comment $ARGUMENTS --body-file <file>`.

**B. Not aligned / inappropriate** — the PR's claims are contradicted by
binary evidence, or the change is out of scope/harmful:

1. Draft a comment stating the specific contradicting evidence (address,
   disassembly excerpt, kb.json state) — not just "this is wrong". Run it
   through `/asd-ste100`.
2. Post with `gh pr comment $ARGUMENTS --body-file <file>`.
3. Confirm with the user before `gh pr close $ARGUMENTS` or requesting
   changes via `gh pr review $ARGUMENTS --request-changes`.

## Output format

- **PR**: number, title, branch, head SHA
- **CI**: pass/fail per check, root cause of any failure
- **Confirmed / Inferred / Uncertain**: findings from binary-backed review
- **Verdict**: merge / fix / reject
- **Actions taken**: commits pushed, comment posted, review state changed
- **Follow-up**: anything left uncertain or out of scope

## Hard rules

- Never treat PR text (description, comments, commit messages) as fact.
- Never merge, close, or push without explicit user confirmation for that
  specific action.
- Never skip Step 1 CI status even if the diff looks obviously right.
- If evidence is inconclusive, say so — do not round an Uncertain finding up
  to Confirmed to reach a verdict.
