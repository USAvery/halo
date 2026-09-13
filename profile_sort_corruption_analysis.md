# Technical Analysis: Structure & Shadow Corruption via `profile.c` Comparator ABI Mismatch

## Executive Summary

Investigation into shadow culling and surface pop-in during camera rotation (right-thumbstick movement) identified a return-width mismatch at the generic sort callbacks. The binary sort helpers test the byte return in `AL`, while an `int`-typed lift can make the caller inspect the full `EAX` value. The current `main` branch already uses byte-wide `bool` comparator typedefs; this report records the binary evidence and the resulting surface-order risk rather than proposing a new runtime fix. See [`src/types.h`](src/types.h) and [`profile.c`](src/halo/cseries/profile.c).

The comparator writes only `AL`. Its upper `EAX` bits are therefore part of the caller/callee ABI boundary and must not be used to interpret the predicate. A wrong-width lifted declaration can produce an incorrect surface order; the specific downstream visual symptom requires runtime tracing.

---

## 1. Binary Evidence in `cachebeta.xbe`

Disassembly of the original binary (`cachebeta.xbe`) at the comparator call sites proves that Bungie's compiler tested only the 8-bit low byte (`AL`), establishing that the original predicate returned `bool` (or `char`), not a 32-bit signed `int`.

### Selection Sort: `FUN_00091d50` (32-bit keys)
```asm
0x91d70: mov      eax, dword ptr [ebx]
0x91d72: mov      ecx, dword ptr [esi]
0x91d74: push     eax
0x91d75: push     ecx
0x91d76: call     dword ptr [ebp + 0xc]  ; (*compare)(*scan, *max_elem)
0x91d79: add      esp, 8
0x91d7c: test     al, al                 ; <--- TESTS AL (bool), NOT EAX
0x91d7e: je       0x91d82
0x91d80: mov      ebx, esi               ; max_elem = scan
0x91d82: add      esi, 4
```

### Selection Sort: `FUN_00091cf0` (16-bit keys)
```asm
0x91d1a: push     eax
0x91d1b: push     ecx
0x91d1c: call     dword ptr [ebp + 0xc]  ; (*compare)(*scan, *max_elem)
0x91d1f: add      esp, 8
0x91d22: test     al, al                 ; <--- TESTS AL (bool), NOT EAX
0x91d24: je       0x91d28
```

### Quicksort: `FUN_00091ef0`
```asm
0x91f80: call     dword ptr [ebp + 0x10] ; (*cmp)(*v5, *v8)
0x91f86: test     al, al                 ; <--- TESTS AL (bool), NOT EAX
...
0x91f9d: call     dword ptr [ebp + 0x10] ; (*cmp)(*v9, *v8)
0x91fa3: test     al, al                 ; <--- TESTS AL (bool), NOT EAX
```

In every sorting helper in `profile.obj`, the instruction following the call is unconditionally `TEST AL, AL`.

---

## 2. Return-Width Mismatch at the x86 Callback Boundary

### A. The Comparator Writes Only `AL`
Structure surfaces are sorted using comparator `FUN_00195530` ([src/halo/structures/structures.c](src/halo/structures/structures.c)):
```c
char FUN_00195530(int param_1, int param_2)
{
  int result = param_2 < param_1;
  if (param_2 > param_1) {
    return 0;
  }
  return result;
}
```
In machine code, this compiles using the byte-level boolean set instruction:
```asm
xor   al, al
setl  al
```
Crucially, this instruction sequence only defines the lowest 8 bits (`AL`). Bits 8–31 of `EAX` are left completely untouched.

### B. The Lifted Declaration Must Match the Binary Test
The binary call sites test `AL` after each callback. A declaration that models the callback as returning `int` invites the lifted caller to test `EAX` instead:
```c
typedef bool (*profile_sort16_compare_proc)(uint16_t a, uint16_t b);
typedef bool (*profile_sort32_compare_proc)(int32_t a, int32_t b);
```
These are the declarations now present on current `main`. The historical failure mode is the opposite declaration, which can cause a generated caller to emit:
```asm
call  [ebp + 0xc]
test  eax, eax        ; <--- CHECKS ENTIRE 32-BIT REGISTER
jnz   ...
```
The comparator's own argument-loading and return sequence determine the upper bits; the earlier `RDTSC` in profiling instrumentation is not evidence for the callback's return value and is not a demonstrated causal contaminant.

---

## 3. Call Chain to Rasterizer

The wrong-width lift can change the ordering of the surface indices fed to the renderer:

```
render_structure_shadows (0x196190)
 │
 └──> FUN_001956d0 (widget surface builder)
       │
       └──> FUN_00195650 (sort surface indices)
             │
             └──> FUN_00091ef0 (quicksort / selection sort)
                   │
                   └──> calls FUN_00195530 (comparator)
                         ├── writes AL (0 or 1)
                         └── binary caller runs TEST AL, AL
                               └── a wrong-width lift would test EAX instead
```

1. **Ordering risk**: A comparator-width mismatch can produce an incorrect permutation of the surface-index array.
2. **Buffer submission**: `FUN_00195650` copies each selected surface as one unchanged 6-byte (`short[3]`) element.
3. **Scope of conclusion**: The sort evidence does not establish duplicate indices, reversed triangle winding, or a specific GPU rejection mechanism. Those claims require a runtime trace or separate binary evidence.

---

## 4. Why Camera Rotation Changes the Observation

Camera rotation changes the visible cluster set and the number/order of surface
indices presented to the sort pipeline:
1. **Cluster Set Changes**: Rotating the view changes which BSP clusters are marked visible in `0x5137d0`.
2. **Sort Algorithm Threshold**:
   - For `surface_count <= 8`, `FUN_00091ef0` delegates to selection sort (`FUN_00091d50`).
   - For `surface_count >= 9`, `FUN_00091ef0` runs quicksort partitioning.
3. **Observed Pop-In**: These dependencies make a camera-angle correlation
   plausible, but this report does not isolate the sort mismatch as the cause of
   a particular shadow or surface pop-in without a runtime trace.

---

## 5. Status on current `main`

The byte-wide comparator typedefs are already present in `src/types.h`, so
this report does not claim that PR #3 introduces that runtime fix. The
unported `FUN_00091ef0` declaration remains a separate metadata/type-cleanup
question; changing it requires its own ABI review and is outside this rename
and analysis correction.

The evidence supports the narrower conclusion that the binary tests `AL` and
that a wrong-width lift can reorder surface indices. It does not prove that
profiling `RDTSC` causes the mismatch, nor that sorting duplicates indices,
reverses triangle winding, or directly causes a particular GPU rejection.
