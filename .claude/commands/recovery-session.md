---
description: Recover source datatypes and byte accuracy per object, gate each batch, and auto-land it to main
agent: build
subtask: false
---

Run the `recovery-session` skill as a real bounded workflow for `$ARGUMENTS`.
Do not only explain the procedure. Load that skill, parse its arguments, and
execute every phase in order.

Keep source/type recovery, byte-score work, and integration as separate lanes
with separate commits and their existing gates. Process one object at a time.
Stop immediately on ambiguous recovery output, authored dirty state, failed
safety gate, or `auto_reintegrate.py` status `parked`/`inconclusive`.

Argument: $ARGUMENTS
