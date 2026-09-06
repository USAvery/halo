"""Dual-oracle differential for FUN_0010a5e0 (periodic_function_evaluate).

Runs the pristine cachebeta.xbe function at 0x10a5e0 (with the real CRT
_CIfmod) and our lift from halo-patched/default.xbe at OURS_VA on identical
seeded curve tables and reports any output divergence.
"""
import sys, struct, math, random
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'audit'))
from check_fpu_association import load_xbe
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_MEM_INVALID, UcError
from unicorn.x86_const import *

ORIG_VA = 0x10a5e0
OURS_VA = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x724c00
FPCW = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x037f
STACK = 0x1000000
TABLES = 0x1200000
TRAMP = 0x1300000
OUT = 0x1301000

random.seed(1234)
tables = []
for i in range(12):
    t = bytearray(random.getrandbits(8) for _ in range(1024))
    # force slide-branch material: high followed by low, and exact 0/255 runs
    for k in range(0, 1024, 97):
        t[k] = 255; t[(k + 1) & 0x3ff] = 0
    t[10] = 255; t[11] = 255; t[12] = 255
    tables.append(bytes(t))

def make(path, entry):
    raw, secs = load_xbe(Path(path))
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    uc.mem_map(0x10000, 0x1000000 - 0x10000)
    for va, vs, ra, rs in secs:
        uc.mem_write(va, raw[ra:ra + rs])
    uc.mem_map(STACK, 0x100000)
    uc.mem_map(TABLES, 0x100000)
    uc.mem_map(TRAMP, 0x10000)
    uc.mem_write(0x46e39c, b'\x01')
    for i, t in enumerate(tables):
        uc.mem_write(TABLES + i * 0x400, t)
        uc.mem_write(0x46e3b8 + i * 4, struct.pack('<I', TABLES + i * 0x400))
    # fstp qword [OUT]; hlt
    uc.mem_write(TRAMP, b'\xdd\x1d' + struct.pack('<I', OUT) + b'\xf4')
    def bad(uc, access, addr, size, value, ud):
        print('  MEM FAULT %s at %#x eip=%#x' % (access, addr, uc.reg_read(UC_X86_REG_EIP)))
        return False
    uc.hook_add(UC_HOOK_MEM_INVALID, bad)
    def call(ftype, x):
        esp = STACK + 0x80000
        uc.mem_write(esp, struct.pack('<Iif', TRAMP, ftype, x))
        uc.reg_write(UC_X86_REG_ESP, esp)
        uc.reg_write(UC_X86_REG_EBP, esp + 0x100)
        uc.reg_write(UC_X86_REG_FPCW, FPCW)
        try:
            uc.emu_start(entry, TRAMP + 6, timeout=2_000_000, count=200000)
        except UcError as e:
            return ('ERR', str(e), uc.reg_read(UC_X86_REG_EIP))
        return struct.unpack('<d', uc.mem_read(OUT, 8))[0]
    return call

orig = make('halo-patched/cachebeta.xbe', ORIG_VA)
ours = make('halo-patched/default.xbe', OURS_VA)

def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]
def nextf(x, n):
    b = struct.unpack('<I', struct.pack('<f', x))[0]
    return struct.unpack("<f", struct.pack("<I", max(0, b + n)))[0]

inputs = []
for k in range(0, 80):
    base = f32(k / 25.6)
    for n in (-2, -1, 0, 1, 2):
        inputs.append(nextf(base, n))
inputs += [random.uniform(0, 60) for _ in range(400)]
inputs += [random.uniform(-60, 0) for _ in range(100)]
inputs += [1e5, 3e6, 1e9, 1e-40, -1e-40, 0.0, -0.0, float('inf'), -float('inf'), float('nan')]

n = 0; worst = 0.0; bad = []
for ft in range(1, 12):
    for x in inputs:
        a = orig(ft, x); b = ours(ft, x); n += 1
        if isinstance(a, tuple) or isinstance(b, tuple):
            bad.append((ft, x, a, b)); continue
        if math.isnan(a) and math.isnan(b):
            continue
        d = abs(a - b) if not (math.isnan(a) or math.isnan(b)) else float('inf')
        worst = max(worst, d)
        ra = 0.0 <= a <= 1.0; rb = 0.0 <= b <= 1.0
        if d > 1e-6 or ra != rb:
            bad.append((ft, x, a, b))
print('cases', n, 'worst |diff|', worst, 'bad', len(bad))
for r in bad[:25]:
    print('  ft=%d x=%r orig=%r ours=%r' % r)

# --- model of the PRE-FIX lift (rev 8637924b1): narrow scaled BEFORE fmod,
# subtraction not narrowed.  Python doubles stand in for the wide x87 value;
# the float32 narrowing dominates the difference being measured.
def old_model(ft, x):
    scaled = f32(f32(x) * f32(25.6))
    w = f32(math.fmod(scaled, 1.0))
    idx = int(round(scaled - w)) & 0x3ff
    T = tables[ft]
    v0 = T[idx] * f32(1 / 255); v1 = T[(idx + 1) & 0x3ff] * f32(1 / 255)
    if (1 << ft) & 0xc0:
        if v0 > f32(0.75) and v1 < f32(0.25):
            v1 += 1.0
        r = (1.0 - w) * v0 + v1 * w
        return r - 1.0 if r > 1.0 else r
    return (1.0 - w) * v0 + v1 * w

worst_old = 0.0; worst_case = None
for ft in range(1, 12):
    for x in inputs:
        if not math.isfinite(x): continue
        a = orig(ft, x); b = old_model(ft, x)
        if isinstance(a, tuple): continue
        d = abs(a - b)
        if d > worst_old: worst_old, worst_case = d, (ft, x, a, b)
print('PRE-FIX model vs original: worst |diff|', worst_old, worst_case)
