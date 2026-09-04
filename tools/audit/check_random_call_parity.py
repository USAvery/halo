#!/usr/bin/env python3
"""Compare RNG usage between the original XBE and our lifted C, per ported function.

A system-link desync ("client/server random seed mismatch") means a ported
function draws from the synced GLOBAL seed a different number of times, in a
different order, or against the wrong seed (global vs local) than the original
build-2276 code did.

Original side : bytes lifted straight out of the pristine halo-patched/cachebeta.xbe
                and disassembled with capstone; CALL targets resolved through
                kb.json addresses.
Candidate side: the clang COFF objects under build/CMakeFiles/halo.dir, read via
                `objdump -dr`; CALL targets resolved through COFF relocations.

Usage:
  python3 tools/audit/check_random_call_parity.py [--only SUBSTR] [--json OUT]
                                                  [--all] [--self-test]
"""

import argparse
import json
import os
import re
import subprocess
import sys

try:
    import capstone
except ImportError:
    sys.exit("capstone is required: pip install capstone")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
XBE = os.path.join(REPO, "halo-patched", "cachebeta.xbe")
KB = os.path.join(REPO, "kb.json")
BOUNDS = os.path.join(REPO, "tools", "verify", "function_bounds.json")
OBJROOT = os.path.join(REPO, "build", "CMakeFiles", "halo.dir")
EXPECT_MD5 = "c7869590a1c64ad034e49a5ee0c02465"

SEED_GLOBAL = 0x46E3F4
SEED_LOCAL = 0x46E3F8
LCG_MUL = 0x19660D
LCG_ADD = 0x3C6EF35F

# addr -> canonical name.  Verified present in kb.json.
FAMILY = {
    0x10B090: "lock_global_random_seed",
    0x10B0A0: "unlock_global_random_seed",
    0x10B0D0: "get_global_random_seed_address",
    0x10B110: "get_random_seed",
    0x10B120: "random_math_get_local_seed_address",
    0x10B130: "random_seed_debug_log",
    0x10B240: "random_math_real",
    0x10B270: "random_real_range",
    0x10B2B0: "random_seed_step",
    0x10B2D0: "random_range",
    0x10B300: "random_direction_table_get_element",
    0x10B380: "random_seed_get_direction3d",
    0x10B3C0: "seed_random_orientation",
    0x10B4C0: "random_direction3d",
    0x97C80: "local_random_range",
    0x97CA0: "local_random_vector_in_cone3d",
    0x9CE80: "real_local_random",
    0x9CE90: "local_random_direction3d",
    0xBB290: "global_random_get_direction3d",
    0xF8070: "random_vector_in_cone3d",
    0xA76A0: "set_random_seed",
    0x12A060: "network_game_set_random_seed",
}
BY_NAME = dict((v, k) for k, v in FAMILY.items())

# Functions taking an explicit seed pointer as their first argument; the seed a
# call is fed is recovered by looking back from the CALL.
SEED_ARG_FUNCS = set([
    "random_math_real", "random_real_range", "random_range", "random_seed_step",
    "random_seed_get_direction3d", "seed_random_orientation", "random_direction3d",
])
# Functions that advance a seed (a "draw").  Used for the priority sort only.
DRAW_FUNCS = set(list(SEED_ARG_FUNCS) + [
    "local_random_range", "local_random_vector_in_cone3d", "real_local_random",
    "local_random_direction3d", "global_random_get_direction3d",
    "random_vector_in_cone3d",
])
PRIORITY_FILES = [
    "units.c", "bipeds.c", "damage.c", "items.c", "weapons.c", "objects.c",
    "model_animations.c", "players.c", "game_engine", "projectiles.c",
    "physics/", "breakable_surfaces.c",
]
INLINE_LCG = "inline_lcg"


# ---------------------------------------------------------------- XBE reader

class Xbe(object):
    def __init__(self, path):
        self.data = open(path, "rb").read()
        u32 = lambda o: int.from_bytes(self.data[o:o + 4], "little")
        base = u32(0x104)
        nsec = u32(0x11C)
        shp = u32(0x120) - base
        self.secs = []
        for i in range(nsec):
            o = shp + i * 0x38
            self.secs.append((u32(o + 4), u32(o + 8), u32(o + 12), u32(o + 16)))

    def read(self, addr, n):
        for va, vsz, raw, rsz in self.secs:
            if va <= addr < va + vsz:
                off = raw + (addr - va)
                return self.data[off:off + min(n, rsz - (addr - va))]
        return None


# ------------------------------------------------------------- kb.json load

def decl_name(decl):
    """Pull the identifier out of a C declaration, ignoring @<reg> annotations."""
    if not decl:
        return None
    head = decl.split("(")[0]
    m = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", head)
    return m[-1] if m else None


def load_kb():
    kb = json.load(open(KB))
    if kb.get("md5") != EXPECT_MD5:
        sys.exit("kb.json md5 %r != expected %r" % (kb.get("md5"), EXPECT_MD5))
    funcs = {}
    for ob in kb.get("objects", []):
        for f in ob.get("functions", []):
            a = f.get("addr")
            if not a:
                continue
            addr = int(a, 16)
            funcs[addr] = {
                "addr": addr,
                "name": f.get("name") or decl_name(f.get("decl")) or "FUN_%08x" % addr,
                # kb "name" is the recovered name; the C symbol follows `decl`,
                # and the two disagree wherever a rename has not reached source.
                "decl_name": decl_name(f.get("decl")),
                "obj": ob.get("name", "?"),
                "src": f.get("src"),
                "ported": bool(f.get("ported")),
            }
    return funcs


# ----------------------------------------------------------- original side

MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
HEXRE = re.compile(r"0x([0-9a-f]+)")


GPR = ("eax", "ecx", "edx", "ebx", "esi", "edi", "ebp")


def _taint_call(taint, callee):
    """A seed-address getter returns its seed pointer in EAX; other calls kill
    every caller-saved register."""
    for r in ("eax", "ecx", "edx"):
        taint.pop(r, None)
    if callee == "get_global_random_seed_address":
        taint["eax"] = "global"
    elif callee == "random_math_get_local_seed_address":
        taint["eax"] = "local"


def _taint_mov(taint, dst, src):
    """Propagate/kill a seed-pointer taint across a register move."""
    if dst not in GPR:
        return
    if src in taint:
        taint[dst] = taint[src]
    else:
        taint.pop(dst, None)


def _push_seed(taint, ops):
    """Classify the operand of a PUSH that is feeding a seed-taking callee."""
    if "0x46e3f4" in ops:
        return "global"
    if "0x46e3f8" in ops:
        return "local"
    r = ops.strip().lstrip("%")
    return taint.get(r, "?")


def scan(insns, is_orig):
    """Count RNG usage in one function.

    `insns` is a list of (mnemonic, operand-string, callee-name-or-None).
    Returns {callee: n} plus {"callee@seed": n}, "ref:*_seed" and "inline_lcg".
    """
    counts, taint, pending = {}, {}, []
    bump = lambda k: counts.__setitem__(k, counts.get(k, 0) + 1)
    for mn, ops, callee in insns:
        if mn == "push":
            pending.append(_push_seed(taint, ops))
            continue
        if mn.startswith("call"):
            if callee in BY_NAME:
                bump(callee)
                if callee in SEED_ARG_FUNCS:
                    # cdecl pushes right-to-left, so arg0 (the seed) is the LAST push.
                    bump("%s@%s" % (callee, pending[-1] if pending else "?"))
            _taint_call(taint, callee)
            pending = []
            continue
        if mn in ("mov", "movl"):
            a = [p.strip().lstrip("%") for p in ops.split(",")]
            if len(a) == 2:
                # capstone is Intel (dst, src); objdump is AT&T (src, dst).
                dst, src = (a[0], a[1]) if is_orig else (a[1], a[0])
                if "0x46e3f4" in src:
                    taint[dst] = "global"
                elif "0x46e3f8" in src:
                    taint[dst] = "local"
                else:
                    _taint_mov(taint, dst, src)
        elif mn in ("lea", "leal"):
            a = [p.strip().lstrip("%") for p in ops.split(",")]
            if len(a) == 2:
                dst, src = (a[0], a[1]) if is_orig else (a[1], a[0])
                taint.pop(dst, None)
        for h in HEXRE.findall(ops):
            v = int(h, 16)
            if v == SEED_GLOBAL:
                bump("ref:global_seed")
            elif v == SEED_LOCAL:
                bump("ref:local_seed")
            elif v in (LCG_MUL, LCG_ADD):
                bump(INLINE_LCG)
    return counts


def scan_original(xbe, addr, end):
    blob = xbe.read(addr, end - addr)
    if not blob:
        return None
    insns, regimm = [], {}
    for i in MD.disasm(blob, addr):
        mn, callee = i.mnemonic, None
        if mn == "call" and i.op_str.startswith("0x"):
            callee = FAMILY.get(int(i.op_str, 16), "<other>")
        elif mn == "call":
            # MSVC can call through a register holding a literal address.
            callee = FAMILY.get(regimm.get(i.op_str.strip())) or "<other>"
        elif mn == "jmp" and i.op_str.startswith("0x"):
            # A jmp leaving the function body is a tail call, and it draws.
            t = int(i.op_str, 16)
            if not (addr <= t < end) and t in FAMILY:
                mn, callee = "call", FAMILY[t]
        elif mn == "mov":
            a = [x.strip() for x in i.op_str.split(",")]
            if len(a) == 2 and a[1].startswith("0x"):
                regimm[a[0]] = int(a[1], 16)
        if mn == "call":
            regimm = {}
        insns.append((mn, i.op_str, callee))
    return scan(insns, True)


# ---------------------------------------------------------- candidate side

FUNC_HDR = re.compile(r"^([0-9a-f]+) <([^>]+)>:")
INSN_RE = re.compile(r"^\s+([0-9a-f]+):\t[0-9a-f ]+\t(\S+)\s*(.*)$")
RELOC_RE = re.compile(r"^\s+([0-9a-f]+):\s+\S+\s+(.+)$")


def disasm_obj(path):
    """objdump one COFF object -> {symbol: [(mnem, ops, reloc_or_None), ...]}."""
    try:
        out = subprocess.run(["objdump", "-dr", "--no-show-raw-insn", path],
                             capture_output=True, text=True, timeout=180).stdout
    except Exception:
        return {}
    # --no-show-raw-insn changes the insn line shape; re-derive it here.
    insn_re = re.compile(r"^\s+([0-9a-f]+):\t(\S+)\s*(.*)$")
    funcs, cur, pend = {}, None, None
    for line in out.splitlines():
        m = FUNC_HDR.match(line)
        if m:
            cur = funcs.setdefault(m.group(2), [])
            continue
        if cur is None:
            continue
        m = insn_re.match(line)
        if m:
            cur.append([m.group(2), m.group(3).split("<")[0].strip(), None,
                        int(m.group(1), 16)])
            continue
        m = RELOC_RE.match(line)
        if m and cur:
            cur[-1][2] = m.group(2).strip()
    return funcs


def build_candidate_index():
    """symbol name -> (objpath, insns).  One objdump pass over every clang obj."""
    index, objs = {}, []
    for root, _, files in os.walk(OBJROOT):
        for f in files:
            if f.endswith(".obj"):
                objs.append(os.path.join(root, f))
    dup = {}
    for p in sorted(objs):
        for sym, insns in disasm_obj(p).items():
            if sym in index:
                dup.setdefault(sym, [index[sym][0]]).append(p)
            else:
                index[sym] = (p, insns)
    return index, objs, dup


def lookup_candidate(index, name, alt=None):
    for nm in (name, alt):
        if not nm:
            continue
        for cand in (nm, "_" + nm):
            if cand in index:
                return index[cand]
    return None, None


def cand_family(tok):
    """Map a candidate call target (reloc symbol) to a family name, thunks included."""
    if not tok:
        return None
    t = tok.lstrip("_")
    if t in BY_NAME:
        return t
    for nm in BY_NAME:                      # generated @<reg> thunk wrappers
        if nm in t:
            return nm
    return None


def scan_candidate(insns):
    """Normalise clang output before counting.

    Two shapes hide a real call from a naive reloc scan, and both occur in our
    tree: an XCALL to an unported callee (`mov $0xADDR,%reg` + `call *%reg`,
    which carries no relocation), and a tail call emitted as `jmp` rather than
    `call`.  Rewrite both into plain calls so the two sides stay comparable.
    """
    out, regimm = [], {}
    for mn, ops, rel, _ in insns:
        callee = cand_family(rel)
        if mn.startswith("call"):
            if callee is None and "*" in ops:
                reg = ops.split("*")[-1].strip().lstrip("%")
                callee = FAMILY.get(regimm.get(reg))
            out.append(("call", ops, callee or "<other>"))
            regimm = {}
            continue
        if mn.startswith("jmp") and callee:
            out.append(("call", ops, callee))
            continue
        if mn in ("mov", "movl"):
            a = [x.strip() for x in ops.split(",")]
            if len(a) == 2 and a[0].startswith("$0x"):
                regimm[a[1].lstrip("%")] = int(a[0][1:], 16)
        out.append((mn, ops, None))
    return scan(out, False)


# ------------------------------------------------------------------ LCG sites

PARAM_RE = re.compile(r"ebp \+ (0x[0-9a-f]+|\d+)\]|(0x[0-9a-f]+)\(%ebp\)")


def _seed_class(operand, taint):
    """Classify the memory operand an LCG step stores its new seed into."""
    if "0x46e3f4" in operand:
        return "global(0x46e3f4)"
    if "0x46e3f8" in operand:
        return "local(0x46e3f8)"
    for r in GPR:
        if r in operand:
            t = taint.get(r)
            if t:
                return "%s(via %s)" % (t, r)
            return "param/other(via %s)" % r
    return "param/other"


def _is_frame_slot(dst):
    """True for a store into this frame (scratch), not through a pointer."""
    return "ebp" in dst or "esp" in dst


def lcg_sites(insns, is_orig):
    """Find inline LCG steps and say which seed each chain writes back to.

    `insns` is a list of (addr, mnemonic, operands, callee).  A step is
    `x * 0x19660d + 0x3c6ef35f`; we anchor on the ADD immediate because the
    multiply may be an IMUL with either operand order.  Consecutive steps that
    keep the value in a register form one chain with a single write-back, and
    MSVC freely reuses the incoming parameter slot as FILD scratch, so a store
    into the frame is skipped rather than mistaken for the seed.
    """
    out, taint, steps = [], {}, []
    for addr, mn, ops, callee in insns:
        if mn.startswith("call"):
            _taint_call(taint, callee)
        elif mn in ("mov", "movl"):
            a = [x.strip().lstrip("%") for x in ops.split(",")]
            if len(a) == 2:
                dst, src = (a[0], a[1]) if is_orig else (a[1], a[0])
                if "0x46e3f4" in src:
                    taint[dst] = "global"
                elif "0x46e3f8" in src:
                    taint[dst] = "local"
                else:
                    m = PARAM_RE.search(src)
                    if m:
                        # A seed pointer read off the incoming frame belongs to
                        # the caller; this function does not choose the seed.
                        taint[dst] = "param+%d" % int(m.group(1) or m.group(2), 0)
                    else:
                        _taint_mov(taint, dst, src)
        if steps and mn in ("mov", "movl"):
            a = [x.strip() for x in ops.split(",")]
            if len(a) == 2:
                dst = a[0] if is_orig else a[1]
                mem = "[" in dst or "(%" in dst
                if mem and not _is_frame_slot(dst):
                    out.append((steps, addr, _seed_class(dst, taint)))
                    steps = []
                    continue
        if "0x3c6ef35f" in ops:
            steps.append(addr)
        elif steps and mn.startswith(("ret", "call")):
            # A conditional jump inside the chain (the FILD sign fixup) does not
            # end it; only a call (clobbers the value register) or a return can.
            out.append((steps, None, "unresolved(no write-back found)"))
            steps = []
    if steps:
        out.append((steps, None, "unresolved(no write-back found)"))
    return out


def orig_insns(xbe, addr, end):
    blob = xbe.read(addr, end - addr)
    if not blob:
        return []
    out = []
    for i in MD.disasm(blob, addr):
        callee = None
        if i.mnemonic == "call" and i.op_str.startswith("0x"):
            callee = FAMILY.get(int(i.op_str, 16), "<other>")
        out.append((i.address, i.mnemonic, i.op_str, callee))
    return out


# ------------------------------------------------------------------ report

def call_deltas(o, c):
    """Per-callee count deltas (candidate - original), seed suffixes excluded."""
    d = {}
    for k in set([x for x in list(o) + list(c) if "@" not in x]):
        n = c.get(k, 0) - o.get(k, 0)
        if n:
            d[k] = n
    return d


def draw_delta(o, c):
    """Signed and absolute change in the number of seed-advancing calls."""
    net = tot = 0
    for k, n in call_deltas(o, c).items():
        if k in DRAW_FUNCS:
            net += n
            tot += abs(n)
    return net, tot


def seed_flips(o, c):
    """Determinate global<->local changes.  '?' attributions are ignored."""
    out = []
    for nm in SEED_ARG_FUNCS:
        og = o.get(nm + "@global", 0), o.get(nm + "@local", 0)
        cg = c.get(nm + "@global", 0), c.get(nm + "@local", 0)
        ou, cu = o.get(nm + "@?", 0), c.get(nm + "@?", 0)
        # Only a difference the unattributed calls cannot account for is a flip.
        for i, kind in ((0, "global"), (1, "local")):
            if cg[i] > og[i] + ou or og[i] > cg[i] + cu:
                out.append("%s %s draws %d -> %d (unattributed o/c %d/%d)"
                           % (nm, kind, og[i], cg[i], ou, cu))
    return out


def fmt(counts):
    if not counts:
        return "-"
    return " ".join("%s=%d" % (k, counts[k]) for k in sorted(counts))


def prio(src):
    for i, p in enumerate(PRIORITY_FILES):
        if p in (src or ""):
            return i
    return len(PRIORITY_FILES)


DEATH_PATH = ("0x1b3060", "0x1a7fa0", "0x1abbd0", "0x1abb20", "0xba550", "0xaf660",
              "0xb3470", "0xbbcb0", "0x13e1f0", "0x140ad0", "0x140a00", "0x120f20",
              "0xf6d60")


def lcg_report(rows):
    """Table of inline-LCG sites, with the seed each one targets on both sides."""
    def draws(counts):
        return sorted(k for k in counts if k in DRAW_FUNCS)

    sel = [r for r in rows if r["orig_lcg"] or r["cand_lcg"]]
    sel.sort(key=lambda r: (r["addr"] not in DEATH_PATH, prio(r["src"]), r["name"]))
    print("functions with an inline LCG on either side: %d\n" % len(sel))
    hdr = "%-30s %-9s %-26s %s" % ("FUNCTION", "ADDR", "SRC", "PATH")
    print(hdr)
    for r in sel:
        tag = "DEATH/RESPAWN" if r["addr"] in DEATH_PATH else ""
        print("%-30s %-9s %-26s %s" % (r["name"][:30], r["addr"], r["src"][-26:], tag))
        for lbl, sites, other in (("orig", r["orig_lcg"], r["cand_lcg"]),
                                  ("cand", r["cand_lcg"], r["orig_lcg"])):
            if sites:
                for ats, st, cls in sites:
                    flag = ("  <== CHECK" if "unresolved" in cls or cls.startswith("param/other")
                            else "")
                    print("    %s %d LCG step(s) @ %s  write-back @ %-8s -> %s%s"
                          % (lbl, len(ats), ",".join("0x%x" % x for x in ats),
                             ("0x%x" % st) if st else "?", cls, flag))
            else:
                prim = draws(r["orig"] if lbl == "orig" else r["cand"])
                print("    %s no inline LCG; calls: %s"
                      % (lbl, ", ".join(prim) if prim else "(none)"))
        base = lambda c: c.split("(via")[0].strip()
        oc = [base(c) for _, _, c in r["orig_lcg"]]
        cc = [base(c) for _, _, c in r["cand_lcg"]]
        if oc and cc and oc != cc:
            print("    *** SEED TARGET DIFFERS: orig %s vs cand %s" % (oc, cc))
        elif bool(oc) != bool(cc):
            print("    *** FORM DIFFERS: inline on one side only")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="restrict to source paths containing this substring")
    ap.add_argument("--json", help="write full results here")
    ap.add_argument("--all", action="store_true", help="also print matching rows")
    ap.add_argument("--always", default="", help="comma-separated addrs to always print")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--lcg-report", action="store_true",
                    help="per-site inline-LCG seed-target table")
    args = ap.parse_args()

    import hashlib
    md5 = hashlib.md5(open(XBE, "rb").read()).hexdigest()
    if md5 != EXPECT_MD5:
        sys.exit("XBE md5 %s != pristine %s -- original side would read patched code" % (md5, EXPECT_MD5))

    xbe = Xbe(XBE)
    kb = load_kb()
    bounds = json.load(open(BOUNDS))
    sys.stderr.write("indexing clang objects...\n")
    index, objs, dup = build_candidate_index()
    sys.stderr.write("  %d objects, %d symbols, %d symbols in >1 obj\n"
                     % (len(objs), len(index), len(dup)))

    rows, unresolved = [], []
    n_ported = n_bounded = n_resolved = 0
    for addr in sorted(kb):
        f = kb[addr]
        if not f["ported"]:
            continue
        n_ported += 1
        b = bounds.get("0x%x" % addr)
        if not b:
            continue
        n_bounded += 1
        ocounts = scan_original(xbe, addr, int(b["end"], 16))
        if ocounts is None:
            unresolved.append((f["name"], "0x%x" % addr, "xbe-read-failed"))
            continue
        objpath, insns = lookup_candidate(index, f["name"], f["decl_name"])
        if insns is None:
            unresolved.append((f["name"], "0x%x" % addr,
                               "no candidate symbol" + (" [ORIG USES RNG]" if ocounts else "")))
            continue
        n_resolved += 1
        ccounts = scan_candidate(insns)
        src = os.path.relpath(objpath, OBJROOT)[:-4]
        if args.only and args.only not in src and args.only not in f["name"]:
            continue
        stale = False
        csrc = os.path.join(REPO, src)
        if os.path.exists(csrc) and os.path.getmtime(csrc) > os.path.getmtime(objpath):
            stale = True
        olcg = clcg = []
        if args.lcg_report:
            olcg = lcg_sites(orig_insns(xbe, addr, int(b["end"], 16)), True)
            clcg = lcg_sites([(ia, mn, ops, cand_family(rel) or "<other>")
                              for mn, ops, rel, ia in insns], False)
        net, absd = draw_delta(ocounts, ccounts)
        deltas = call_deltas(ocounts, ccounts)
        flips = seed_flips(ocounts, ccounts)
        kbobj = os.path.basename(f["obj"] or "").rsplit(".obj", 1)[0]
        mismatched_tu = bool(kbobj) and kbobj != os.path.basename(src).rsplit(".", 1)[0]
        rows.append({
            "name": f["name"], "addr": "0x%x" % addr, "src": src, "obj": f["obj"],
            "tu_mismatch": mismatched_tu, "dup_sym": f["name"] in dup or ("_" + f["name"]) in dup,
            "orig": ocounts, "cand": ccounts, "stale_obj": stale,
            "call_deltas": deltas, "seed_flips": flips,
            "d_draw": net, "d_draw_abs": absd,
            "orig_lcg": olcg, "cand_lcg": clcg,
            "match": not deltas and not flips,
        })

    always = set(a.strip() for a in args.always.split(",") if a.strip())
    bad = [r for r in rows if not r["match"] or r["addr"] in always or args.all]
    bad.sort(key=lambda r: (-r["d_draw_abs"], not r["seed_flips"],
                            prio(r["src"]), r["name"]))

    print("scanned: %d ported, %d with bounds, %d resolved candidate-side, "
          "%d unresolved-with-rng" % (n_ported, n_bounded, n_resolved, len(unresolved)))
    n_tu = len([r for r in rows if r["tu_mismatch"]])
    n_dup = len([r for r in rows if r["dup_sym"]])
    print("integrity: %d rows with kb-obj/TU mismatch, %d rows whose symbol "
          "exists in >1 obj, %d rows with stale .obj"
          % (n_tu, n_dup, len([r for r in rows if r["stale_obj"]])))
    if n_tu:
        for r in rows:
            if r["tu_mismatch"]:
                print("  TU? %-40s kb=%s obj=%s" % (r["name"][:40], r["obj"], r["src"]))
    print("mismatches: %d of %d functions with any RNG evidence\n" %
          (len([r for r in rows if not r["match"]]),
           len([r for r in rows if r["orig"] or r["cand"]])))
    if args.lcg_report:
        rc = lcg_report(rows)
        if args.json:
            json.dump({"rows": rows}, open(args.json, "w"), indent=1)
            print("\nwrote %s" % args.json)
        return rc

    print("%-44s %-9s %-30s %5s %s" % ("FUNCTION", "ADDR", "SRC", "dDRAW", "STALE"))
    for r in bad:
        print("%-44s %-9s %-30s %5d %s" % (r["name"][:44], r["addr"], r["src"][-30:],
                                           r["d_draw"], "STALE" if r["stale_obj"] else ""))
        if r["call_deltas"]:
            print("    delta: %s" % " ".join(
                "%s%+d" % (k, v) for k, v in sorted(r["call_deltas"].items())))
        for fl in r["seed_flips"]:
            print("    SEED : %s" % fl)
        print("    orig : %s" % fmt(r["orig"]))
        print("    cand : %s" % fmt(r["cand"]))

    if unresolved:
        print("\nunresolved (ported, but no candidate symbol found): %d" % len(unresolved))
        for nm, a, why in unresolved:
            print("  %-44s %-9s %s" % (nm, a, why))

    if args.json:
        json.dump({"rows": rows, "unresolved": unresolved,
                   "n_ported": n_ported, "n_bounded": n_bounded,
                   "n_resolved": n_resolved},
                  open(args.json, "w"), indent=1)
        print("\nwrote %s" % args.json)

    if args.self_test:
        want = {"get_global_random_seed_address": 1,
                "random_math_get_local_seed_address": 1, "random_math_real": 2,
                "random_math_real@global": 1, "random_math_real@local": 1}
        o = scan_original(xbe, 0x120F20, 0x120FC6)
        _, ci = lookup_candidate(index, "model_animation_choose_random")
        c = scan_candidate(ci) if ci else {}
        ok = (o == want and c == want)
        print("\nSELF-TEST model_animation_choose_random: %s" % ("PASS" if ok else "FAIL"))
        if not ok:
            print("  want %s\n  orig %s\n  cand %s" % (fmt(want), fmt(o), fmt(c)))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
