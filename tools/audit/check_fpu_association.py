#!/usr/bin/env python3
"""Detect x87 addend-association divergence between our shipped clang code and
the original XBE.

Why this check exists
---------------------
The VC71 lane compiles our C with cl.exe (MSVC 7.1) and compares that against
the original.  But the binary we actually ship is built by clang.  When cl.exe
reassociates a float expression to match the original and clang does not, VC71
scores 100% while the shipped code computes a differently-rounded result.
Floating-point addition is not associative, so ((a+b)+c) and ((b+c)+a) differ by
a ULP -- and a ULP is enough to flip a threshold branch a tick early and desync
a lockstep system-link game.

That is a real bug class VC71 cannot see by construction.  This check closes it
by comparing the two binaries directly: it symbolically executes the x87 stream
of our clang object and of the original XBE function, builds an expression tree
for each, canonicalises commutative-but-not-associative nodes (a+b == b+a is
exact in IEEE; (a+b)+c == a+(b+c) is not), and reports any structural
difference.

Scope: pure-FPU leaves (no CALL) whose every FPU memory operand resolves to a
parameter slot or an absolute address.  Anything else is reported SKIP rather
than guessed at.
"""

import argparse
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import capstone

REPO = Path(__file__).resolve().parents[2]
XBE = REPO / "halo-patched" / "cachebeta.xbe"
BOUNDS = REPO / "tools" / "verify" / "function_bounds.json"
OBJ_ROOT = REPO / "build" / "CMakeFiles" / "halo.dir"

# ---------------------------------------------------------------------------
# XBE section mapping
# ---------------------------------------------------------------------------


_XBE_IMAGE = None


def _xbe_image():
    """Import the shared XBE parser lazily (it lives in a sibling tool dir)."""
    global _XBE_IMAGE
    if _XBE_IMAGE is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "equivalence"))
        import xbe_image
        _XBE_IMAGE = xbe_image
    return _XBE_IMAGE


def load_xbe(path):
    """`(raw, [(va, vsize, raw_off, raw_size), ...])` -- the 4-tuple shape.

    Delegates to `tools/equivalence/xbe_image.py`, the single XBE section
    parser.  Unlike the 3-tuple form in `check_delinked_bounds`, this one
    carries `raw_size`, so a caller can tell real bytes from the BSS tail.
    """
    return _xbe_image().load_xbe_legacy4(path)


def read_va(raw, sections, va, size):
    """Bytes at a virtual address.

    Now zero-fills past a section's `raw_size` instead of returning a short
    read, matching what the loader puts in memory.  No behavioural change for
    this module's callers, which read `.text` (where raw_size == vsize).
    """
    return _xbe_image().read_va(raw, sections, va, size)


# ---------------------------------------------------------------------------
# Symbolic x87 evaluation
# ---------------------------------------------------------------------------

COMMUTATIVE = {"add", "mul"}


class Unsupported(Exception):
    pass


def _neg(n):
    return ("un", "neg", n)


def hoist_neg(n):
    """Return (negated?, node) with sign hoisted out of mul/div chains.

    Negation is exact in IEEE, and -(a/b) == (-a)/b == a/(-b) bit-for-bit, so
    where MSVC put the FCHS relative to the FDIVP is a scheduling choice with no
    numeric content.  Normalising it away keeps the report to differences that
    actually change a result.  Addition is NOT included: -(a+b) != (-a)+b.
    """
    if n[0] == "leaf":
        return False, n
    if n[0] == "un":
        sub_neg, sub = hoist_neg(n[2])
        if n[1] == "neg":
            return (not sub_neg), sub
        return False, ("un", n[1], _neg(sub) if sub_neg else sub)
    ln, l = hoist_neg(n[2])
    rn, r = hoist_neg(n[3])
    if n[1] in ("mul", "div"):
        return (ln != rn), ("bin", n[1], l, r)
    return False, ("bin", n[1],
                   _neg(l) if ln else l,
                   _neg(r) if rn else r)


def node_key(n):
    neg, body = hoist_neg(n)
    return ("neg", _node_key(body)) if neg else _node_key(body)


def _node_key(n):
    """Canonical, hashable form.  Commutative nodes sort their operands so that
    a+b and b+a compare equal (exact in IEEE), while (a+b)+c and a+(b+c) do
    not."""
    if n[0] == "leaf":
        return n
    if n[0] == "un":
        return ("un", n[1], _node_key(n[2]))
    kids = (_node_key(n[2]), _node_key(n[3]))
    if n[1] in COMMUTATIVE:
        kids = tuple(sorted(kids, key=repr))
    return ("bin", n[1], kids[0], kids[1])


def render(n, depth=0):
    if n[0] == "leaf":
        return n[1]
    if n[0] == "un":
        return "%s(%s)" % (n[1], render(n[2]))
    return "(%s %s %s)" % (render(n[2]), n[1], render(n[3]))


ARG_RE = re.compile(r"^arg(\d+)(\+.*)$")


def shift_args(n, delta):
    """Renumber argN leaves by `delta`.

    Our clang build and the original disagree on parameter numbering whenever
    kb.json gives the original register-passed arguments: the same pointer is
    arg1 on one side and arg2 on the other.  That is a naming skew, not a
    numeric difference, so a uniform shift that makes the two trees identical
    proves the association matches.
    """
    if n[0] == "leaf":
        m = ARG_RE.match(n[1])
        return ("leaf", "arg%d%s" % (int(m.group(1)) + delta, m.group(2))) if m else n
    if n[0] == "un":
        return ("un", n[1], shift_args(n[2], delta))
    return ("bin", n[1], shift_args(n[2], delta), shift_args(n[3], delta))


MEM_RE = re.compile(r"(?:dword|qword|tbyte|word)\s+ptr\s+\[(.*?)\]")
NUM = r"(?:0x[0-9a-fA-F]+|\d+)"


class FpuSim:
    """Symbolically execute a straight-line x87 stream."""

    def __init__(self, reg_slots, const_reader=None):
        self.st = []          # index 0 == st(0)
        self.reg_slots = reg_slots
        self.const_reader = const_reader
        self.stores = []      # ordered list of values written to memory

    # -- operand resolution -------------------------------------------------
    def leaf(self, ins):
        m = MEM_RE.search(ins.op_str)
        if not m:
            raise Unsupported("non-memory FPU operand %r" % ins.op_str)
        expr = m.group(1).replace(" ", "")
        # absolute address
        if re.fullmatch(NUM, expr) and expr.startswith("0x"):
            # Resolve .rdata float literals to their value so that a load of a
            # zero constant and an FLDZ compare equal instead of being reported
            # as a difference that does not exist.
            if self.const_reader is not None:
                val = self.const_reader(int(expr, 16), ins)
                if val is not None:
                    return ("leaf", "const:%r" % val)
            return ("leaf", "abs:%s" % expr)
        mm = re.fullmatch(r"([a-z]{2,3})(?:([+-])(" + NUM + r"))?", expr)
        if not mm:
            raise Unsupported("unresolved address form [%s]" % expr)
        reg, sign, disp = mm.groups()
        slot = self.reg_slots.get(reg)
        if slot is None:
            raise Unsupported("register %s not traced to a parameter" % reg)
        off = (int(disp, 0) if disp else 0)
        if sign == "-":
            off = -off
        return ("leaf", "%s+0x%x" % (slot, off))

    def sti(self, ins, which=0):
        parts = [p.strip() for p in ins.op_str.split(",")]
        m = re.fullmatch(r"st\((\d+)\)", parts[which]) if which < len(parts) else None
        if m:
            return int(m.group(1))
        if parts and parts[which] == "st":
            return 0
        raise Unsupported("expected st(i), got %r" % ins.op_str)

    def push(self, v):
        self.st.insert(0, v)

    def pop(self):
        if not self.st:
            raise Unsupported("stack underflow")
        return self.st.pop(0)

    # -- the interpreter ----------------------------------------------------
    def step(self, ins):
        m, ops = ins.mnemonic, ins.op_str

        if m in ("fld", "flds", "fldl"):
            if ops.startswith("st"):
                self.push(self.st[self.sti(ins)])
            else:
                self.push(self.leaf(ins))
            return
        if m in ("fild",):
            self.push(("un", "i2f", self.leaf(ins)))
            return
        if m == "fld1":
            self.push(("leaf", "const:%r" % 1.0))
            return
        if m == "fldz":
            self.push(("leaf", "const:%r" % 0.0))
            return
        if m == "fxch":
            i = self.sti(ins) if ops else 1
            self.st[0], self.st[i] = self.st[i], self.st[0]
            return
        if m == "fchs":
            self.st[0] = ("un", "neg", self.st[0])
            return
        if m == "fabs":
            self.st[0] = ("un", "abs", self.st[0])
            return
        if m == "fsqrt":
            self.st[0] = ("un", "sqrt", self.st[0])
            return
        if m in ("fstp", "fst", "fstps", "fstpl"):
            if ops.startswith("st"):
                i = self.sti(ins)
                self.st[i] = self.st[0]
                if m.startswith("fstp"):
                    self.pop()
                return
            self.stores.append(self.st[0])
            if m.startswith("fstp"):
                self.pop()
            return

        base = None
        for cand in ("faddp", "fadd", "fsubrp", "fsubr", "fsubp", "fsub",
                     "fmulp", "fmul", "fdivrp", "fdivr", "fdivp", "fdiv"):
            if m == cand or (m.startswith(cand) and m[len(cand):] in ("s", "l")):
                base = cand
                break
        if base is None:
            raise Unsupported("opcode %s" % m)

        popping = base.endswith("p")
        # (direction is decoded from the opcode below; the printed form lies)
        core = base[1:-1] if popping else base[1:]
        rev = core.endswith("r")
        if rev:
            core = core[:-1]

        esc = ins.bytes[0] if ins.bytes else None
        if esc not in (0xD8, 0xDC, 0xDE):
            raise Unsupported("unhandled x87 escape %r for %s" % (esc, m))
        modrm = ins.bytes[1] if len(ins.bytes) > 1 else 0
        if modrm < 0xC0:
            # memory form: st(0) op= mem
            src, dst, mem = None, 0, self.leaf(ins)
        else:
            i = modrm & 7
            mem = None
            if esc == 0xD8:              # st(0) op= st(i)
                dst, src = 0, i
            else:                        # 0xDC / 0xDE: st(i) op= st(0)
                dst, src = i, 0

        rhs = mem if mem is not None else self.st[src]
        lhs = self.st[dst]
        if rev:
            lhs, rhs = rhs, lhs
        val = ("bin", core, lhs, rhs)
        if popping:
            self.pop()
            dst -= 1
        self.st[dst] = val

    def result(self):
        if self.st:
            return self.st[0]
        if self.stores:
            return ("bin", "seq", self.stores[0], self.stores[-1]) if len(self.stores) > 1 else self.stores[0]
        raise Unsupported("no FPU result")

    def all_values(self):
        return list(self.stores) + list(self.st)


# ---------------------------------------------------------------------------
# Parameter-slot tracing
# ---------------------------------------------------------------------------

LOAD_RE = re.compile(r"^(e[a-z]{2}),\s*(?:dword ptr )?\[\s*(e[bs]p)\s*([+-])\s*(0x[0-9a-fA-F]+|\d+)\s*\]$")


def _update_slots(ins, slots, state):
    """Advance the register -> parameter-slot map by one instruction.

    This has to be flow-sensitive, not a single map for the whole function: the
    original routinely reuses one register for two different parameters (e.g.
    "ECX switches from param_1 to param_2" in FUN_001057c0), so a static map
    binds every FPU operand to whichever parameter was loaded last and invents
    differences that are not there.
    """
    if ins.mnemonic == "push" and ins.op_str == "ebp":
        state["esp"] += 4
        return
    if ins.mnemonic == "mov" and ins.op_str == "ebp, esp":
        state["frame"] = True
        return
    if ins.mnemonic == "mov":
        m = LOAD_RE.match(ins.op_str)
        if m:
            reg, breg, sign, disp = m.groups()
            off = int(disp, 0) * (-1 if sign == "-" else 1)
            if breg == "ebp" and state["frame"] and off >= 8:
                slots[reg] = "arg%d" % ((off - 8) // 4)
            elif breg == "esp" and off >= state["esp"] + 4:
                slots[reg] = "arg%d" % ((off - state["esp"] - 4) // 4)
            else:
                slots.pop(reg, None)
            return
    dst = ins.op_str.split(",")[0].strip()
    if dst in slots and ins.mnemonic not in ("cmp", "test", "push"):
        slots.pop(dst, None)


def is_fpu(ins):
    return ins.mnemonic.startswith("f")


def analyse(insns, const_reader=None):
    slots = {}
    state = {"frame": False, "esp": 0}
    sim = FpuSim(slots, const_reader)
    for ins in insns:
        _update_slots(ins, slots, state)
        if ins.mnemonic == "call":
            raise Unsupported("has CALL")
        if is_fpu(ins):
            if ins.mnemonic.startswith(("fcom", "fucom", "fnst", "fst sw", "fwait", "fldcw", "fnstcw")):
                raise Unsupported("comparison/control op %s" % ins.mnemonic)
            try:
                sim.step(ins)
            except Unsupported:
                raise
            except (IndexError, ValueError, AttributeError) as e:
                raise Unsupported("%s %s: %s" % (ins.mnemonic, ins.op_str, e))
    return sim


# ---------------------------------------------------------------------------
# Our clang objects
# ---------------------------------------------------------------------------


def objdump_functions(obj):
    """Return {func_name: [capstone insns]} for one COFF object."""
    try:
        out = subprocess.run(["llvm-objdump", "-d", "--x86-asm-syntax=intel", str(obj)],
                             capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    funcs, cur, rows = {}, None, []
    hdr = re.compile(r"^[0-9a-f]+ <_?(\w+)>:")
    row = re.compile(r"^\s*([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*(\S+)\s*(.*)$")
    for line in out.splitlines():
        h = hdr.match(line)
        if h:
            if cur:
                funcs[cur] = rows
            cur, rows = h.group(1), []
            continue
        r = row.match(line)
        if r and cur:
            rows.append(_Ins(int(r.group(1), 16), r.group(3), r.group(4).strip(),
                             bytes.fromhex(r.group(2).replace(" ", ""))))
    if cur:
        funcs[cur] = rows
    return funcs


class _Ins:
    __slots__ = ("address", "mnemonic", "op_str", "bytes")

    def __init__(self, a, m, o, b=b""):
        self.address, self.mnemonic, self.op_str, self.bytes = a, m, o, b


def disasm_xbe(raw, sections, start, end):
    data = read_va(raw, sections, start, end - start)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.syntax = capstone.CS_OPT_SYNTAX_INTEL
    return [_Ins(i.address, i.mnemonic, i.op_str, bytes(i.bytes))
            for i in md.disasm(data, start)]


# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="restrict to these source files")
    ap.add_argument("--function", action="append", default=[],
                    help="restrict to these function names")
    ap.add_argument("--show-skips", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not XBE.exists():
        print("missing %s" % XBE, file=sys.stderr)
        return 2
    raw, sections = load_xbe(XBE)
    bounds = json.loads(BOUNDS.read_text())
    by_name = {}
    for addr, ent in bounds.items():
        n = ent.get("name")
        if n:
            by_name[n] = (int(addr, 16), int(ent["end"], 16))

    objs = sorted(OBJ_ROOT.rglob("*.obj"))
    if args.paths:
        want = {Path(p).name + ".obj" for p in args.paths}
        objs = [o for o in objs if o.name in want]
    if not objs:
        print("no clang objects under %s -- build first" % OBJ_ROOT, file=sys.stderr)
        return 2

    mismatches, checked, skipped = [], 0, []
    for obj in objs:
        for name, insns in objdump_functions(obj).items():
            if args.function and name not in args.function:
                continue
            if name not in by_name:
                continue
            start, end = by_name[name]
            def _const(addr, ins, _raw=raw, _sec=sections):
                width = 8 if "qword" in ins.op_str else 4
                b = read_va(_raw, _sec, addr, width)
                if len(b) != width:
                    return None
                return struct.unpack("<d" if width == 8 else "<f", b)[0]

            try:
                ours = analyse(insns, _const)
            except Unsupported as e:
                skipped.append((name, "ours: %s" % e))
                continue
            try:
                theirs = analyse(disasm_xbe(raw, sections, start, end), _const)
            except Unsupported as e:
                skipped.append((name, "xbe: %s" % e))
                continue
            ov, tv = ours.all_values(), theirs.all_values()
            if len(ov) != len(tv):
                skipped.append((name, "value-count %d vs %d" % (len(ov), len(tv))))
                continue
            checked += 1
            # A uniform argN shift is a numbering skew (register-passed args),
            # not a numeric divergence -- accept it if it reconciles every value.
            deltas = [d for d in (0, 1, -1, 2, -2, 3, -3)
                      if all(node_key(shift_args(a, d)) == node_key(b)
                             for a, b in zip(ov, tv))]
            if deltas:
                continue
            for i, (a, b) in enumerate(zip(ov, tv)):
                if node_key(a) != node_key(b):
                    mismatches.append((name, obj, i, render(a), render(b)))

    for name, obj, i, ours_s, theirs_s in mismatches:
        rel = obj.relative_to(OBJ_ROOT)
        print("[FPU-ASSOC] %s (%s) value %d" % (name, rel, i))
        print("    ours (clang): %s" % ours_s)
        print("    xbe (orig)  : %s" % theirs_s)

    if args.show_skips:
        for name, why in sorted(skipped):
            print("  SKIP %s: %s" % (name, why))

    print("\n%d compared, %d skipped, %d MISMATCH"
          % (checked, len(skipped), len(mismatches)))
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
