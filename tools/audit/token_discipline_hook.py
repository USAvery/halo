#!/usr/bin/env python3
"""Token-discipline hook for Claude Code.

Wires the rules in CLAUDE.md ("Token Discipline" section) into runtime
feedback so the agent sees its read/edit ratio and gets warned about
repeat reads instead of relying on "track mentally."

Hook protocol (Claude Code):
- Reads JSON payload from stdin: {tool_name, tool_input, session_id, hook_event_name, ...}
- Writes JSON to stdout: {"systemMessage": "..."} when there's something to say.

Wired in `.claude/settings.json` to:
- PostToolUse on Read|Edit|Write|Bash: update counts, emit ratio warning on
  drift, emit duplicate-read warning when (path, offset, limit) repeats.
  Bash is included because repo doctrine routes most reads through the shell
  (`rtk read -o N -l M`, `sed -n 'A,Bp'`, `cat`, `head`/`tail`); counting only
  the Read tool undercounted research and made the 4:1 ratio meaningless
  (59 sessions measured 260 reads vs 370 edits/writes = 0.7:1).
- Stop: emit final session summary.

State file: `.claude/agent-memory/token_discipline/<session_id>.json`.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_DIR = REPO_ROOT / ".claude" / "agent-memory" / "token_discipline"

# Tunables. CLAUDE.md targets a 4:1 read/edit ratio; warn below this after a
# minimum number of edits so the first few hits are not noise.
TARGET_RATIO = 4.0
MIN_EDITS_BEFORE_RATIO_WARN = 5
RATIO_WARN_COOLDOWN = 5

# Retention for per-session state files. Left unbounded, STATE_DIR grew to
# hundreds of files / MBs over a couple of months. Keep recent sessions only.
STATE_RETENTION_DAYS = 14
STATE_KEEP_MAX = 200

# Files we never want to count as "research reads" — generated artifacts,
# logs, and binaries that CLAUDE.md already bans. Reading them is the
# problem, so they should not credit the read/edit ratio.
NOISY_PATH_FRAGMENTS = (
    "/build/", "/build_debug/", "/node_modules/", "/.git/",
    "/halo-patched/", "/__pycache__/", "/dist/",
)


def _state_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in session_id)
    if not safe:
        safe = "default"
    return STATE_DIR / f"{safe}.json"


def _load_state(session_id: str) -> dict:
    p = _state_path(session_id)
    if p.exists():
        try:
            state = json.loads(p.read_text(encoding="utf-8"))
            # Additive schema: sessions written by an older hook lack the
            # Bash-derived counters. Never rename existing keys.
            state.setdefault("bash_reads", 0)
            state.setdefault("searches", 0)
            return state
        except json.JSONDecodeError:
            pass
    return {
        "session_id": session_id,
        "started": datetime.now().isoformat(timespec="seconds"),
        "reads": 0,
        "noisy_reads": 0,
        "bash_reads": 0,
        "searches": 0,
        "edits": 0,
        "writes": 0,
        "repeat_reads": 0,
        "ranges": {},  # "<path>:<offset>:<limit>" -> count
        "files_read": {},  # path -> count
        "last_event": "",
        "last_ratio_warn_at": 0,
    }


def _prune_state_dir() -> None:
    """Bound STATE_DIR growth: drop files older than the retention window, then
    cap the total count by keeping the newest STATE_KEEP_MAX. Best-effort — any
    OS error is swallowed so pruning never breaks the hook."""
    try:
        import time as _time

        files = list(STATE_DIR.glob("*.json"))
        cutoff = _time.time() - STATE_RETENTION_DAYS * 86400
        survivors = []
        for f in files:
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                f.unlink(missing_ok=True)
            else:
                survivors.append((mtime, f))
        if len(survivors) > STATE_KEEP_MAX:
            survivors.sort(reverse=True)  # newest first
            for _mtime, f in survivors[STATE_KEEP_MAX:]:
                f.unlink(missing_ok=True)
    except OSError:
        pass


def _save_state(session_id: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = _state_path(session_id)
    # Per-process tmp name: concurrent hook invocations for the same session
    # (parallel tool calls fire this hook concurrently) must not share a tmp
    # path, or one process's replace() can race ahead of another's, leaving
    # the second replace() targeting an already-renamed-away tmp file.
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        tmp.replace(p)
    except OSError:
        tmp.unlink(missing_ok=True)
    _prune_state_dir()


def _is_noisy(path: str) -> bool:
    norm = path.replace("\\", "/")
    return any(frag in norm for frag in NOISY_PATH_FRAGMENTS) or norm.endswith(".log")


# --- Bash command parsing -------------------------------------------------
#
# CLAUDE.md routes reads through the shell, so the Read tool sees only a
# fraction of the research a session actually does. These tables classify a
# Bash command line into reads (credit the ratio, eligible for the
# duplicate-range warning) and searches (tracked separately: a grep is
# research, but it is not a file read and must not inflate the ratio).

# Commands whose arguments name files we are reading.
_READ_HEADS = ("cat", "head", "tail", "sed", "read")  # "read" = `rtk read`
# Commands that search rather than read.
_SEARCH_HEADS = ("grep", "egrep", "fgrep", "rg", "ripgrep", "ast-grep", "sg")
# Build / test / VCS / packaging heads: never counted, and their arguments are
# not scanned (so `git grep`, `make cat.o`, ... stay silent).
_IGNORED_HEADS = (
    "git", "cmake", "ninja", "make", "msbuild", "pytest", "tox", "cargo",
    "npm", "pnpm", "yarn", "tsc", "gcc", "g++", "clang", "clang++", "cl",
    "ld", "lld-link", "ctest", "gh", "docker", "pip", "pip3", "apt",
    "apt-get", "dpkg", "curl", "wget", "scp", "rsync", "cp", "mv", "rm",
    "mkdir", "chmod", "echo", "printf", "python", "python3", "node", "jq",
    "fd", "find", "ls", "wc", "diff", "sort", "uniq", "awk", "tr", "xargs",
)
# Wrappers that prefix a real command.
_WRAPPER_HEADS = ("rtk", "sudo", "time", "command", "nohup", "env", "exec")
_SEPARATORS = ("&&", "||", ";", "|", "&", "\n")


def _split_segments(tokens: list) -> list:
    """Split a flat token list into command segments on shell separators."""
    segments, cur = [], []
    for tok in tokens:
        if tok in _SEPARATORS:
            if cur:
                segments.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        segments.append(cur)
    return segments


def _strip_prefix(tokens: list) -> list:
    """Drop env assignments and wrapper commands (rtk, sudo, ...)."""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _WRAPPER_HEADS or ("=" in tok and not tok.startswith("-")
                                     and "/" not in tok.split("=", 1)[0]):
            i += 1
            continue
        break
    return tokens[i:]


def _looks_like_path(tok: str) -> bool:
    if not tok or tok.startswith("-"):
        return False
    if tok[0] in "><$(){}`":
        return False
    return True


def _int_or_none(tok: str):
    try:
        return int(tok)
    except (TypeError, ValueError):
        return None


def _parse_sed_range(script: str):
    """`1,20p` / `1,20 p` / `20p` -> (offset, limit); None if not a print range."""
    body = script.strip()
    if not body.endswith("p"):
        return None
    body = body[:-1].strip()
    if "," in body:
        a, _, b = body.partition(",")
        start, end = _int_or_none(a.strip()), _int_or_none(b.strip())
        if start is None or end is None:
            return None
        return (start, max(end - start + 1, 0))
    start = _int_or_none(body)
    if start is None:
        return None
    return (start, 1)


def parse_bash_command(command: str) -> dict:
    """Classify a Bash command line.

    Returns {"reads": [(path, offset, limit), ...], "searches": int}.
    offset/limit of 0 mean "whole file" (matches the Read tool's defaults so
    the duplicate-range key is shared between the two surfaces).
    """
    result = {"reads": [], "searches": 0}
    if not command or not command.strip():
        return result
    try:
        tokens = shlex.split(command, comments=False, posix=True)
    except ValueError:
        tokens = command.split()

    for seg in _split_segments(tokens):
        seg = _strip_prefix(seg)
        if not seg:
            continue
        head = os.path.basename(seg[0])
        args = seg[1:]
        if head in _IGNORED_HEADS:
            continue
        if head in _SEARCH_HEADS:
            result["searches"] += 1
            continue
        if head not in _READ_HEADS:
            continue

        if head == "read":  # `rtk read <path> [-o N] [-l M]`
            path, offset, limit = "", 0, 0
            i = 0
            while i < len(args):
                a = args[i]
                if a in ("-o", "--offset") and i + 1 < len(args):
                    offset = _int_or_none(args[i + 1]) or 0
                    i += 2
                elif a in ("-l", "--limit") and i + 1 < len(args):
                    limit = _int_or_none(args[i + 1]) or 0
                    i += 2
                elif _looks_like_path(a) and not path:
                    path = a
                    i += 1
                else:
                    i += 1
            if path:
                result["reads"].append((path, offset, limit))
            continue

        if head == "cat":
            for a in args:
                if _looks_like_path(a):
                    result["reads"].append((a, 0, 0))
            continue

        if head in ("head", "tail"):
            n, path = 0, ""
            i = 0
            while i < len(args):
                a = args[i]
                if a in ("-n", "-c") and i + 1 < len(args):
                    n = _int_or_none(args[i + 1]) or 0
                    i += 2
                elif a.startswith("-") and _int_or_none(a[1:]) is not None:
                    n = _int_or_none(a[1:]) or 0
                    i += 1
                elif _looks_like_path(a) and not path:
                    path = a
                    i += 1
                else:
                    i += 1
            if path:
                result["reads"].append((path, 0, n))
            continue

        if head == "sed":
            # Positional grammar: `sed [flags] <script> <file>...`, unless the
            # script came from -e/-f, in which case every positional is a file.
            rng, script_from_flag = None, False
            positionals = []
            i = 0
            while i < len(args):
                a = args[i]
                if a.startswith("-"):
                    if a in ("-e", "--expression") and i + 1 < len(args):
                        rng = rng or _parse_sed_range(args[i + 1])
                        script_from_flag = True
                        i += 2
                        continue
                    if a in ("-f", "--file", "-i") and i + 1 < len(args):
                        script_from_flag = True
                        i += 2
                        continue
                    i += 1
                    continue
                if _looks_like_path(a):
                    positionals.append(a)
                i += 1
            if not script_from_flag and positionals:
                rng = _parse_sed_range(positionals[0])
                positionals = positionals[1:]
            offset, limit = rng if rng else (0, 0)
            for path in positionals:
                result["reads"].append((path, offset, limit))
            continue

    return result


def _ratio_message(state: dict) -> str | None:
    research_reads = state["reads"] - state["noisy_reads"]
    total_writes = state["edits"] + state["writes"]
    if total_writes < MIN_EDITS_BEFORE_RATIO_WARN:
        return None
    ratio = research_reads / total_writes if total_writes else 0.0
    if ratio < TARGET_RATIO:
        last = state.get("last_ratio_warn_at", 0)
        if total_writes - last < RATIO_WARN_COOLDOWN:
            return None
        state["last_ratio_warn_at"] = total_writes
        return (
            f"Token-discipline: read/edit ratio {ratio:.1f}:1 "
            f"({research_reads} research reads / {total_writes} edits) — "
            f"below {TARGET_RATIO:.0f}:1 target. CLAUDE.md: do more research "
            f"before editing (rg/jq for callers + read narrow line ranges)."
        )
    return None


def _record_read(state: dict, file_path: str, offset, limit) -> str | None:
    state["reads"] += 1
    if _is_noisy(file_path):
        state["noisy_reads"] += 1
        return (
            f"Token-discipline: read of noisy path {file_path}. CLAUDE.md "
            f"bans reads in build/log/generated dirs — run the command or "
            f"grep instead."
        )

    norm = file_path.replace("\\", "/")
    if norm.endswith("/kb.json") or norm == "kb.json":
        state["noisy_reads"] += 1
        return (
            f"Token-discipline: direct Read of kb.json. CLAUDE.md: use "
            f"`rtk jq` ONLY for kb.json queries (6000+ lines, historically "
            f"239 redundant reads = ~143K wasted tokens)."
        )

    state["files_read"][file_path] = state["files_read"].get(file_path, 0) + 1

    key = f"{file_path}:{offset}:{limit}"
    state["ranges"][key] = state["ranges"].get(key, 0) + 1
    if state["ranges"][key] > 1:
        state["repeat_reads"] += 1
        prior = state["ranges"][key] - 1
        # If an Edit happened on this file since the last read, the file may
        # have changed — but CLAUDE.md still says don't re-read to verify.
        return (
            f"Token-discipline: re-reading {file_path} "
            f"(offset={offset}, limit={limit}) — already read {prior}x this "
            f"session. CLAUDE.md Read-Once Rule: the Edit tool confirms "
            f"success. Recall from context or rg for the specific string."
        )
    return None


def _record_bash(state: dict, command: str) -> list:
    """Credit shell-issued reads/searches to the same counters as the tools."""
    parsed = parse_bash_command(command)
    msgs = []
    for path, offset, limit in parsed["reads"]:
        state["bash_reads"] = state.get("bash_reads", 0) + 1
        m = _record_read(state, path, offset, limit)
        if m:
            msgs.append(m)
    if parsed["searches"]:
        state["searches"] = state.get("searches", 0) + parsed["searches"]
    return msgs


def _record_edit_or_write(state: dict, tool_name: str) -> None:
    if tool_name == "Edit":
        state["edits"] += 1
    elif tool_name == "Write":
        state["writes"] += 1


def _summary(state: dict) -> str:
    research_reads = state["reads"] - state["noisy_reads"]
    total_writes = state["edits"] + state["writes"]
    ratio = research_reads / total_writes if total_writes else 0.0
    lines = [
        f"Token-discipline summary (session {state['session_id'][:8]}):",
        f"  reads:        {state['reads']} ({research_reads} research, "
        f"{state['noisy_reads']} noisy, {state.get('bash_reads', 0)} via bash)",
        f"  searches:     {state.get('searches', 0)}",
        f"  edits/writes: {state['edits']} / {state['writes']}",
        f"  repeat reads: {state['repeat_reads']}",
        f"  ratio:        {ratio:.1f}:1 (target {TARGET_RATIO:.0f}:1)",
    ]
    return "\n".join(lines)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    session_id = payload.get("session_id") or "default"
    hook_event = payload.get("hook_event_name", "")
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    state = _load_state(session_id)
    state["last_event"] = hook_event

    msgs: list[str] = []

    if hook_event == "PostToolUse":
        if tool_name == "Read":
            file_path = tool_input.get("file_path", "")
            offset = tool_input.get("offset", 0)
            limit = tool_input.get("limit", 0)
            if file_path:
                m = _record_read(state, file_path, offset, limit)
                if m:
                    msgs.append(m)
        elif tool_name == "Bash":
            command = tool_input.get("command", "")
            if command:
                msgs.extend(_record_bash(state, command))
        elif tool_name in ("Edit", "Write"):
            _record_edit_or_write(state, tool_name)
            m = _ratio_message(state)
            if m:
                msgs.append(m)

    elif hook_event in ("Stop", "SubagentStop"):
        if state["reads"] + state["edits"] + state["writes"] > 0:
            msgs.append(_summary(state))

    _save_state(session_id, state)

    if msgs:
        print(json.dumps({"systemMessage": "\n".join(msgs)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
