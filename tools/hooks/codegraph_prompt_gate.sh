#!/bin/sh
# Intent-gated CodeGraph prompt frontloading (arch review 2026-07-07, fix #1).
#
# The user-global `codegraph prompt-hook` fires on ANY prompt that names an
# indexed symbol (~16KB / ~4K tokens per hit), which is redundant noise in this
# token-disciplined repo where CLAUDE.md already mandates on-demand
# `codegraph explore`. Project settings therefore set CODEGRAPH_NO_PROMPT_HOOK=1
# to silence the global hook, and THIS project hook re-enables the injection
# selectively: only for debug / hard-problem / explicit-codegraph prompts, where
# frontloaded structural context (call paths, dispatch hops) earns its tokens.
#
# Double gate: this intent gate, then codegraph's own structural/symbol gate
# and 16KB cap still apply inside `codegraph prompt-hook`.
# Contract (like upstream): never break the prompt — all failure paths exit 0.

payload=$(cat) || exit 0
[ -n "$payload" ] || exit 0

prompt=$(printf '%s' "$payload" | jq -r '.prompt // empty' 2>/dev/null) || exit 0
[ -n "$prompt" ] || exit 0

# Two-part gate (narrowed 2026-09-02 after a 15.8KB false fire on an
# "evaluate ... investigate it" efficiency-audit prompt):
#
#   1. INTENT: genuine *runtime-debug* vocabulary only. Deliberately dropped:
#      investigate, regression, bug, broken, stuck, root cause, misbehav,
#      divergen, debug -- all of them fire on planning / audit / meta prompts
#      that have no code locus at all.
#   2. LOCUS: the prompt must also name at least one plausible code symbol --
#      a Ghidra name (FUN_0009fd30), an address (0x9fd30), or a snake_case
#      identifier (object_damage_update). Without a locus there is nothing
#      for codegraph to anchor on, so the injection is guaranteed noise.
#
# Both must match. Keep both lists narrow: routine lift & command prompts
# must stay zero-cost.
INTENT_RE='crash|page fault|access.violation|assert|freeze|hang|deadlock|corrupt|garbage|trap frame|EIP|CR2|wrong (value|color|position|behavior|output|result)|codegraph'
SYMBOL_RE='FUN_[0-9a-fA-F]{8}|0[xX][0-9a-fA-F]{4,6}|[a-z_]+_[a-z_]+'

printf '%s' "$prompt" | grep -qiE "$INTENT_RE" || exit 0
printf '%s' "$prompt" | grep -qE "$SYMBOL_RE" || exit 0

printf '%s' "$payload" | CODEGRAPH_NO_PROMPT_HOOK=0 codegraph prompt-hook 2>/dev/null
exit 0
