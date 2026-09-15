#!/usr/bin/env python3
"""
check_param_types.py — Cross-check kb.json parameter and return TYPES against
the value-passing shape observed at original call sites in the pristine XBE.

VC71's official match is a mnemonic-only LCS, so it is largely blind to type
errors: declaring a float parameter `int` still scores, but the caller's x87
store lands in a slot the callee reads as an integer.  These are silent
correctness bugs (truncation, 1.07e9-for-0.5f) that no existing gate catches.

Three checks, all derived from disassembly of `halo-patched/cachebeta.xbe`:

  float_param   ERROR  a stack slot filled by `fstp dword/qword ptr [esp+K]`
                       whose declared parameter is not float/double.
  float_return  ERROR  callers consume ST(0) after the CALL (fstp/fadd/... with
                       no intervening fld) but the decl returns a non-float.
  byte_return   WARN   every conclusive caller tests AL (test al,al / cmp al /
                       movzx r,al) but the decl returns a >8-bit type.

Only call sites whose cdecl cleanup (`ADD ESP,N`) matches the declared stack
slot count are counted, which pins the argument-block base and rules out
ESP-relative stores into locals.

Usage:
    python3 tools/audit/check_param_types.py                 # full run
    python3 tools/audit/check_param_types.py --callee 0x14adb0
    python3 tools/audit/check_param_types.py --ported-only
    python3 tools/audit/check_param_types.py --check         # exit 1 on new ERRORs
    python3 tools/audit/check_param_types.py --update-baseline
    python3 tools/audit/check_param_types.py --json <path>
    python3 tools/audit/check_param_types.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_arg_counts as cac   # noqa: E402  (XBE loader, kb loader, decl parser)

REPO_ROOT = cac.REPO_ROOT
BASELINE_PATH = REPO_ROOT / "tools" / "audit" / "param_type_baseline.json"

# How far back from a CALL to look for argument-building stores.
ARG_SCAN_BACKWARD = 48
# How far forward from a CALL to look for return-value consumption.
RET_SCAN_FORWARD = 12

_FLOAT_PARAM_RE = re.compile(r"\b(float|real|real32)\b")
_WIDE_FLOAT_RE = re.compile(r"\b(double|real64)\b")
_VOID_RET_RE = re.compile(r"^\s*(void|VOID)\s*$")

# `fstp dword ptr [esp + 0x10]` / `fstp dword ptr [esp]` / `fstp qword ptr [esp+4]`
_FSTP_ESP_RE = re.compile(
    r"^(dword|qword)\s+ptr\s+\[esp(?:\s*\+\s*(0x[0-9a-f]+|\d+))?\]$",
    re.IGNORECASE,
)

# Instructions that consume ST(0) as a value produced by the callee.
_ST0_CONSUME = {
    "fstp", "fst", "fadd", "faddp", "fsub", "fsubp", "fsubr", "fsubrp",
    "fmul", "fmulp", "fdiv", "fdivp", "fdivr", "fdivrp",
    "fcom", "fcomp", "fcompp", "fucom", "fucomp", "fucompp",
    "fcomi", "fcomip", "fucomi", "fucomip",
    "fistp", "fist", "fchs", "fabs", "fsqrt",
}
# Instructions that push a NEW value onto the x87 stack — seeing one of these
# first means the later ST(0) use is not the callee's return value.
_ST0_PRODUCE = {
    "fld", "fld1", "fldz", "fldpi", "fldl2e", "fldl2t", "fldlg2", "fldln2",
    "fild", "fbld",
}

_AL_CONSUME_RE = re.compile(r"\bal\b", re.IGNORECASE)
_EAX_CONSUME_RE = re.compile(r"\beax\b", re.IGNORECASE)
_AX_CONSUME_RE = re.compile(r"\bax\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Declaration → stack slot map
# ---------------------------------------------------------------------------

class SlotInfo(NamedTuple):
    param_index: int    # 1-based index into the full parameter list
    text: str           # the parameter declaration text
    is_float: bool      # declared float/real (32-bit)
    is_double: bool     # declared double/real64 (64-bit)


def stack_slot_map(decl: str) -> Optional[Dict[int, SlotInfo]]:
    """Map stack dword-slot index -> SlotInfo for a cdecl/fastcall decl.

    Returns None for varargs or unparseable declarations.  Register-annotated
    (`@<reg>`) params and __fastcall ECX/EDX params occupy no stack slot.
    """
    paren_start = decl.find("(")
    paren_end = decl.rfind(")")
    if paren_start == -1 or paren_end == -1:
        return None
    params_raw = decl[paren_start + 1:paren_end]
    if "..." in params_raw:
        return None
    stripped = params_raw.strip()
    if stripped in ("", "void"):
        return {}

    params = [p for p in cac._split_params(stripped) if p.strip()]
    is_fastcall = bool(cac._FASTCALL_RE.search(decl))
    budget = 2 if is_fastcall else 0

    slots: Dict[int, SlotInfo] = {}
    slot = 0
    for i, p in enumerate(params):
        if cac._REG_ANNOTATION_RE.search(p):
            continue
        if budget > 0 and cac._is_fastcall_reg_candidate(p):
            budget -= 1
            continue
        width = cac._param_slots(p)
        info = SlotInfo(
            param_index=i + 1,
            text=p.strip(),
            is_float=bool(_FLOAT_PARAM_RE.search(p)),
            is_double=bool(_WIDE_FLOAT_RE.search(p)),
        )
        for k in range(width):
            slots[slot + k] = info
        slot += width
    return slots


def return_type(decl: str) -> str:
    """Text of the declared return type (everything before the function name)."""
    paren = decl.find("(")
    if paren == -1:
        return ""
    head = decl[:paren]
    m = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*$", head)
    if not m:
        return head.strip()
    return head[:m.start(1)].strip()


# ---------------------------------------------------------------------------
# Call-site evidence
# ---------------------------------------------------------------------------

class SiteEvidence(NamedTuple):
    caller_va: int
    cleanup_dwords: Optional[int]
    float_slots: Dict[int, bool]   # stack slot index -> is_qword (double)
    ret_class: str                 # "float" / "byte" / "word" / "dword" / "none"


def _parse_fstp_esp(op_str: str) -> Optional[Tuple[int, bool]]:
    """Return (displacement, is_qword) for an `fstp [esp+K]` operand, else None."""
    m = _FSTP_ESP_RE.match(op_str.strip())
    if not m:
        return None
    disp = int(m.group(2), 0) if m.group(2) else 0
    return disp, m.group(1).lower() == "qword"


def _classify_return_use(insns, i: int) -> str:
    """Classify how the caller consumes the value returned by insns[i] (a CALL)."""
    n = len(insns)
    for j in range(i + 1, min(i + RET_SCAN_FORWARD + 1, n)):
        _, mnem, ops = insns[j]
        if mnem in _ST0_PRODUCE:
            break                      # new FPU value — not the return value
        if mnem in _ST0_CONSUME:
            return "float"
        if mnem == "call":
            break
        if cac._is_cf_terminator(mnem):
            # A conditional jump after a flag-setting use was already handled
            # by the AL/EAX tests below; anything else bounds the window.
            break
        if mnem in ("test", "cmp", "movzx", "movsx", "and", "or", "xor", "mov",
                    "add", "sub", "inc", "dec", "push", "sete", "setne", "neg"):
            if _AL_CONSUME_RE.search(ops):
                return "byte"
            if _AX_CONSUME_RE.search(ops):
                return "word"
            if _EAX_CONSUME_RE.search(ops):
                return "dword"
    return "none"


def collect_evidence(target_addrs: frozenset) -> Dict[int, List[SiteEvidence]]:
    """Disassemble every XBE section and gather per-callee call-site evidence."""
    cac._load_xbe()
    by_callee: Dict[int, List[SiteEvidence]] = defaultdict(list)

    for sec_idx in range(len(cac._xbe_sections)):
        try:
            insns = cac._disassemble_section(sec_idx)
        except Exception:
            continue
        n = len(insns)
        for i, (va, mnem, ops) in enumerate(insns):
            if mnem != "call":
                continue
            try:
                target = int(ops, 16)
            except ValueError:
                continue
            if target not in target_addrs:
                continue

            # Walk backward: count pushes seen so far (each one shifts every
            # earlier ESP-relative store one slot further into the arg block).
            pushes_after = 0
            float_slots: Dict[int, bool] = {}
            for j in range(i - 1, max(i - ARG_SCAN_BACKWARD, -1), -1):
                _, pm, po = insns[j]
                if cac._is_cf_terminator(pm):
                    break
                if pm == "push":
                    pushes_after += 1
                    continue
                if pm in ("fstp", "fst"):
                    parsed = _parse_fstp_esp(po)
                    if parsed is not None:
                        disp, is_qword = parsed
                        if disp % 4 == 0:
                            float_slots[disp // 4 + pushes_after] = is_qword

            # Forward: cdecl cleanup, then how the return value is used.
            cleanup: Optional[int] = None
            for j in range(i + 1, min(i + cac.CLEANUP_SCAN_FORWARD + 1, n)):
                _, fm, fo = insns[j]
                if fm == "call" or fm.startswith("j") or fm in ("ret", "retn", "retf"):
                    break
                if fm == "add":
                    m = re.match(r"esp\s*,\s*(0x[0-9a-f]+|\d+)", fo, re.IGNORECASE)
                    if m:
                        nb = int(m.group(1), 0)
                        if nb > 0 and nb % 4 == 0:
                            cleanup = nb // 4
                    break

            by_callee[target].append(SiteEvidence(
                caller_va=va,
                cleanup_dwords=cleanup,
                float_slots=float_slots,
                ret_class=_classify_return_use(insns, i),
            ))
    return by_callee


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

class Finding(NamedTuple):
    addr_str: str
    name: str
    ported: bool
    kind: str          # float_param / float_return / byte_return
    severity: str      # ERROR / WARN
    detail: str
    sites: int
    conclusive: int
    example_va: int

    def key(self) -> str:
        return f"{self.addr_str}:{self.kind}:{self.detail}"


def analyze(entries, callee_filter: Optional[int] = None,
            ported_only: bool = False) -> Tuple[List[Finding], Dict]:
    slot_maps: Dict[int, Dict[int, SlotInfo]] = {}
    declared_slots: Dict[int, int] = {}
    for e in entries:
        sm = stack_slot_map(e.decl)
        if sm is None:
            continue
        slot_maps[e.addr] = sm
        declared_slots[e.addr] = (max(sm) + 1) if sm else 0

    targets = frozenset(slot_maps.keys())
    evidence = collect_evidence(targets)

    by_addr = {e.addr: e for e in entries}
    findings: List[Finding] = []
    stats = {
        "callees_audited": 0,
        "callees_with_sites": 0,
        "total_sites": 0,
        "conclusive_sites": 0,
        "confirmed_float_params": 0,
        "errors": 0,
        "warns": 0,
    }

    addrs = [callee_filter] if callee_filter is not None else sorted(targets)
    for addr in addrs:
        e = by_addr.get(addr)
        if e is None:
            continue
        if ported_only and not e.ported:
            continue
        sm = slot_maps.get(addr)
        if sm is None:
            continue
        stats["callees_audited"] += 1

        sites = evidence.get(addr, [])
        if not sites:
            continue
        stats["callees_with_sites"] += 1
        stats["total_sites"] += len(sites)

        want = declared_slots[addr]
        conclusive = [s for s in sites if s.cleanup_dwords == want]
        stats["conclusive_sites"] += len(conclusive)
        if not conclusive:
            continue

        # --- float_param -------------------------------------------------
        slot_hits: Dict[int, int] = defaultdict(int)
        slot_qword: Dict[int, bool] = {}
        first_va: Dict[int, int] = {}
        for s in conclusive:
            for slot, is_q in s.float_slots.items():
                if slot not in sm:
                    continue          # outside the argument block — a local
                slot_hits[slot] += 1
                slot_qword[slot] = slot_qword.get(slot, False) or is_q
                first_va.setdefault(slot, s.caller_va)

        for slot, hits in sorted(slot_hits.items()):
            info = sm[slot]
            if info.is_float or info.is_double:
                stats["confirmed_float_params"] += 1
                continue
            if hits * 2 < len(conclusive):
                continue              # minority of sites — not conclusive
            findings.append(Finding(
                addr_str=e.addr_str, name=e.name, ported=e.ported,
                kind="float_param", severity="ERROR",
                detail=(f"param {info.param_index} `{info.text}` receives an x87 "
                        f"{'qword' if slot_qword[slot] else 'dword'} store "
                        f"(stack slot {slot})"),
                sites=len(sites), conclusive=hits,
                example_va=first_va[slot],
            ))

        # --- return type ---------------------------------------------------
        ret = return_type(e.decl)
        ret_is_void = bool(_VOID_RET_RE.match(ret))
        ret_is_float = bool(_FLOAT_PARAM_RE.search(ret) or _WIDE_FLOAT_RE.search(ret))
        ret_classes = [s.ret_class for s in conclusive if s.ret_class != "none"]
        if ret_classes:
            n_float = ret_classes.count("float")
            n_byte = ret_classes.count("byte")
            if n_float and n_float * 2 >= len(ret_classes) and not ret_is_float:
                findings.append(Finding(
                    addr_str=e.addr_str, name=e.name, ported=e.ported,
                    kind="float_return", severity="ERROR",
                    detail=(f"declared return `{ret or 'int'}` but "
                            f"{n_float}/{len(ret_classes)} callers consume ST(0)"),
                    sites=len(sites), conclusive=n_float,
                    example_va=next(s.caller_va for s in conclusive
                                    if s.ret_class == "float"),
                ))
            elif (n_byte == len(ret_classes) and not ret_is_void
                  and not ret_is_float
                  and not re.search(r"\b(char|byte|boolean|bool|u?int8_t)\b", ret)):
                findings.append(Finding(
                    addr_str=e.addr_str, name=e.name, ported=e.ported,
                    kind="byte_return", severity="WARN",
                    detail=(f"declared return `{ret or 'int'}` but all "
                            f"{n_byte} conclusive callers test AL only"),
                    sites=len(sites), conclusive=n_byte,
                    example_va=next(s.caller_va for s in conclusive
                                    if s.ret_class == "byte"),
                ))

    stats["errors"] = sum(1 for f in findings if f.severity == "ERROR")
    stats["warns"] = sum(1 for f in findings if f.severity == "WARN")
    return findings, stats


# ---------------------------------------------------------------------------
# Baseline / reporting
# ---------------------------------------------------------------------------

def load_baseline() -> Dict[str, Dict]:
    if not BASELINE_PATH.exists():
        return {}
    with open(BASELINE_PATH) as f:
        return json.load(f).get("findings", {})


def write_baseline(findings: List[Finding]) -> None:
    payload = {
        "_comment": ("Known param/return type mismatches accepted at baseline time. "
                     "check_param_types.py --check fails on findings NOT listed here. "
                     "Remove an entry once the decl is fixed."),
        "findings": {
            f.key(): {"name": f.name, "kind": f.kind, "severity": f.severity,
                      "detail": f.detail, "ported": f.ported}
            for f in findings
        },
    }
    with open(BASELINE_PATH, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def print_report(findings: List[Finding], stats: Dict, verbose: bool) -> None:
    errors = [f for f in findings if f.severity == "ERROR"]
    warns = [f for f in findings if f.severity == "WARN"]

    for label, group in (("ERROR", errors), ("WARN", warns)):
        if not group:
            continue
        print(f"\n=== {label} ({len(group)}) ===")
        for f in sorted(group, key=lambda x: (x.kind, x.addr_str)):
            flag = "ported" if f.ported else "unported"
            print(f"  {f.addr_str} {f.name} [{flag}] {f.kind}")
            print(f"      {f.detail}")
            print(f"      {f.conclusive}/{f.sites} sites, e.g. call at 0x{f.example_va:x}")

    print("\n--- summary ---")
    print(f"  callees audited      : {stats['callees_audited']}")
    print(f"  with call sites      : {stats['callees_with_sites']}")
    print(f"  conclusive sites     : {stats['conclusive_sites']}/{stats['total_sites']}")
    print(f"  confirmed float args : {stats['confirmed_float_params']}")
    print(f"  ERRORs               : {stats['errors']}")
    print(f"  WARNs                : {stats['warns']}")


def self_test() -> int:
    """Unit-check the decl parsing and operand decoding (no XBE needed)."""
    failures = 0

    def check(label, got, want):
        nonlocal failures
        if got != want:
            print(f"  FAIL {label}: got {got!r} want {want!r}")
            failures += 1
        else:
            print(f"  ok   {label}")

    sm = stack_slot_map(
        "void collision_features_from_point(int param_1, float param_2, "
        "int param_3, int param_4)")
    check("slot0 -> param 1", sm[0].param_index, 1)
    check("slot1 is float", sm[1].is_float, True)
    check("slot2 not float", sm[2].is_float, False)

    sm2 = stack_slot_map("int f(int a@<eax>, float b, double c)")
    check("@<reg> takes no slot", sm2[0].param_index, 2)
    check("double spans 2 slots", (sm2[1].param_index, sm2[2].param_index), (3, 3))

    check("varargs skipped", stack_slot_map("int p(const char *f, ...)"), None)
    check("void params", stack_slot_map("void f(void)"), {})

    check("fstp [esp]", _parse_fstp_esp("dword ptr [esp]"), (0, False))
    check("fstp [esp+4]", _parse_fstp_esp("dword ptr [esp + 4]"), (4, False))
    check("fstp qword", _parse_fstp_esp("qword ptr [esp + 0x10]"), (16, True))
    check("fstp local rejected", _parse_fstp_esp("dword ptr [ebp - 8]"), None)

    check("ret type", return_type("short unit_find_weapon(int h)"), "short")
    check("ret type ptr", return_type("void *tag_get(int i)"), "void *")

    print(f"\nself-test: {'PASS' if failures == 0 else f'{failures} FAILURES'}")
    return 1 if failures else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--callee", help="audit a single callee (0x... address)")
    ap.add_argument("--ported-only", action="store_true",
                    help="report only functions with ported: true")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 on any ERROR not present in the baseline")
    ap.add_argument("--update-baseline", action="store_true",
                    help="rewrite param_type_baseline.json from this run")
    ap.add_argument("--json", help="write the full finding list to this path")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(self_test())

    callee = int(args.callee, 16) if args.callee else None
    entries = cac._load_kb()
    findings, stats = analyze(entries, callee_filter=callee,
                              ported_only=args.ported_only)

    print_report(findings, stats, args.verbose)

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json, "w") as f:
            json.dump({"stats": stats,
                       "findings": [f_._asdict() for f_ in findings]}, f, indent=2)
        print(f"\nwrote {args.json}")

    if args.update_baseline:
        write_baseline(findings)
        print(f"\nbaseline updated: {BASELINE_PATH} ({len(findings)} entries)")
        sys.exit(0)

    if args.check:
        baseline = load_baseline()
        new_errors = [f for f in findings
                      if f.severity == "ERROR" and f.key() not in baseline]
        if new_errors:
            print(f"\nFAIL: {len(new_errors)} new type mismatch(es) not in baseline:")
            for f in new_errors:
                print(f"  {f.addr_str} {f.name}: {f.detail}")
            print("\nFix the kb.json decl, or record it with --update-baseline "
                  "if the evidence is disputed.")
            sys.exit(1)
        print("\nPASS: no new type mismatches.")
    sys.exit(0)


if __name__ == "__main__":
    main()
