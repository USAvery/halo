"""Exhaust the 65536 RNG outputs using actual original and clang instructions."""
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools/verify"), str(ROOT / "tools/equivalence"), str(ROOT / "tools/audit")]
import xbe_reference
from coff_loader import extract_function
from check_delinked_bounds import load_xbe, va_to_off
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

out = ROOT / "artifacts/fpu_assoc"
out.mkdir(parents=True, exist_ok=True)
source = (ROOT / "src/halo/math/random_math.c").read_text()
old = "return (float)(s >> 16) / 65535.0f;"
new = "return (float)(s >> 16) * *(float *)0x2647f4;"
assert old in source or new in source
flags = ["clang", "-O3", "-DNDEBUG", "-target", "i386-pc-win32", "-march=pentium3",
         "-mno-sse", "-nostdlib", "-ffreestanding", "-fno-builtin", "-fno-exceptions",
         "-fno-omit-frame-pointer", "-mstack-probe-size=65536", "-Isrc",
         "-Ithird_party/xbox", "-Ibuild/generated", "-include", "src/common.h"]
functions = {}
for variant in ("before", "after"):
    text = source.replace(new, old) if variant == "before" else source.replace(old, new)
    path = out / f"rng-{variant}.c"
    path.write_text(text)
    obj = path.with_suffix(".obj")
    subprocess.run(flags + ["-c", str(path), "-o", str(obj)], cwd=ROOT, check=True)
    functions[variant] = extract_function(str(obj), "random_math_real")
original, error = xbe_reference.function_bytes(0x10b240)
assert original is not None, error
data, sections = load_xbe(ROOT / "halo-patched/cachebeta.xbe")
md = Cs(CS_ARCH_X86, CS_MODE_32)
md.detail = True
assembly = [".text", ".globl _start, control, output"]
constants = {}
evidence = {}
for name in ("reference", "before", "after"):
    fn = functions.get(name)
    code = original if fn is None else fn.code
    patches = {}
    if fn is not None:
        for reloc in fn.relocs:
            assert reloc.reloc_type == 6 and reloc.symbol_name in fn.rdata_map, reloc
            label = f"constant_{name}_{reloc.virtual_address}"
            addend = struct.unpack_from("<I", code, reloc.virtual_address)[0]
            constants[label] = fn.rdata_map[reloc.symbol_name][addend:addend + 4]
            patches[reloc.virtual_address] = label
    for insn in md.disasm(code, 0):
        if insn.disp in (0x25fb8c, 0x2647f4):
            label = f"constant_original_{insn.disp:x}"
            offset = va_to_off(sections, insn.disp)
            constants[label] = data[offset:offset + 4]
            patches[insn.address + insn.disp_offset] = label
    evidence[name] = {"bytes": len(code), "patched_constant_offsets": patches}
    assembly += [f".globl run_{name}", f"run_{name}:", "push 4(%esp)", f"call {name}",
                 "add $4,%esp", "mov 8(%esp),%eax", "fstpt (%eax)", "ret", f"{name}:"]
    cursor = 0
    for offset, label in sorted(patches.items()):
        if offset > cursor:
            assembly.append(".byte " + ",".join(str(b) for b in code[cursor:offset]))
        assembly.append(f".long {label}")
        cursor = offset + 4
    if cursor < len(code):
        assembly.append(".byte " + ",".join(str(b) for b in code[cursor:]))
assembly += ["_start:", "call main", "mov %eax,%ebx", "mov $1,%eax", "int $0x80",
             "control:", "fninit", "fldcw 4(%esp)", "ret", "output:", "push %ebx",
             "mov $4,%eax", "mov $1,%ebx", "mov 8(%esp),%ecx", "mov 12(%esp),%edx",
             "int $0x80", "pop %ebx", "ret", ".section .rodata"]
for label, value in constants.items():
    assert len(value) == 4
    assembly += [label + ":", ".byte " + ",".join(str(b) for b in value)]
assembly.append('.section .note.GNU-stack,"",@progbits')
(out / "native_rng.S").write_text("\n".join(assembly) + "\n")
subprocess.run(["gcc", "-m32", "-O2", "-nostdlib", "-fno-pie", "-no-pie", "-fno-stack-protector",
                str(ROOT / "tools/verify/fixtures/rng_real_native.c"), str(out / "native_rng.S"), "-o", str(out / "native-rng")],
               cwd=ROOT, check=True)
result = subprocess.run([str(out / "native-rng")], capture_output=True, text=True, check=True)
print("control_word before_f32 before_f80 after_f32 after_f80 (65536 outputs)")
print(result.stdout)
evidence["results"] = [list(map(int, line.split())) for line in result.stdout.splitlines()]
evidence["constants"] = {label: value.hex() for label, value in constants.items()}
assert all(row[3:] == [0, 0] for row in evidence["results"])
assert all(row[1] == 512 and row[2] == 65535 for row in evidence["results"])
(out / "native_rng_evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
