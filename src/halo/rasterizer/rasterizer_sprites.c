/* Forwarding wrapper (0x17cd60).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x163590 -- the frame is torn down
 * before the jump, so 0x163590 inherits this function's stack arguments and
 * reads the incoming dword as its own [EBP+8].  Semantics of the argument are
 * unknown; it is forwarded unchanged. */
void FUN_0017cd60(int object_handle)
{
  FUN_00163590(object_handle);
}

/* Forwarding wrapper (0x17cd70).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x163910 -- the frame is torn down
 * before the jump, so 0x163910 inherits this function's stack arguments and
 * reads them as its own [EBP+8] .. [EBP+0x1c] (six dwords).  Semantics of the
 * arguments are unknown; they are forwarded unchanged.  This wrapper is
 * reached only through a data (function-table) reference at 0x195ff3. */
void FUN_0017cd70(int arg1, int arg2, int arg3, int arg4, int arg5, int arg6)
{
  FUN_00163910((void *)arg1, arg2, arg3, arg4, arg5, (void *)arg6);
}

/* Forwarding wrapper (0x17cdb0).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x163fe0 -- the frame is torn down
 * before the jump, so 0x163fe0 inherits this function's stack arguments and
 * reads the incoming dword as its own [ESP+4] pointer (Ghidra
 * `in_stack_00000004`, passed on to 0x155c20).  Semantics of the pointer are
 * unknown; it is forwarded unchanged.  Reached only through a data
 * (function-table) reference at 0x195c8d. */
void FUN_0017cdb0(void *param_1)
{
  FUN_00163fe0(param_1);
}

/* Forwarding wrapper (0x17cdc0).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x1640d0 -- the frame is torn down
 * before the jump, so 0x1640d0 inherits this function's stack arguments and
 * reads them as its own [EBP+8] .. [EBP+0x1c] (six dwords).  Semantics of the
 * arguments are unknown; they are forwarded unchanged.  Reached only through a
 * data (function-table) reference at 0x195c88 (in FUN_00195c40). */
void FUN_0017cdc0(int arg1, int arg2, int arg3, int arg4, int arg5, int arg6)
{
  FUN_001640d0((void *)arg1, arg2, arg3, arg4, arg5, (void *)arg6);
}

/* Forwarding wrapper (0x17ce00).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x164590 -- the frame is torn down
 * before the jump, so 0x164590 inherits this function's stack arguments and
 * reads the incoming dword as its own [EBP+8].  This wrapper never touches the
 * argument slot itself, so the semantics of the dword are unknown here; it is
 * forwarded unchanged.  Reached only through a data (function-table) reference
 * at 0x195cd8 (in FUN_00195cb0). */
void FUN_0017ce00(int arg1)
{
  FUN_00164590((void *)arg1);
}

/* Forwarding wrapper (0x17ce10).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x1609b0 -- the frame is torn down
 * before the jump, so 0x1609b0 inherits this function's stack arguments and
 * reads them as its own [EBP+8] .. [EBP+0x1c] (six dwords).  This wrapper
 * never touches the argument slots itself, so their semantics are unknown
 * here; they are forwarded unchanged.  Reached only through a data
 * (function-table) reference at 0x195cd3 (in FUN_00195cb0). */
void FUN_0017ce10(int arg1, int arg2, int arg3, int arg4, int arg5, int arg6)
{
  FUN_001609b0((void *)arg1, arg2, arg3, arg4, arg5, (void *)arg6);
}

/* Forwarding wrapper (0x17ce50).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x164690 -- the frame is torn down
 * before the jump, so 0x164690 inherits this function's stack arguments and
 * reads them as its own [EBP+8] .. [EBP+0x1c] (six dwords, confirmed by
 * disassembling 0x164690 in the pristine XBE).  This wrapper never touches the
 * argument slots itself, so their semantics are unknown here; they are
 * forwarded unchanged.  Reached only through a data (function-table) reference
 * at 0x195d20 (in FUN_00195d00). */
void FUN_0017ce50(int arg1, int arg2, int arg3, int arg4, int arg5, int arg6)
{
  FUN_00164690((void *)arg1, arg2, arg3, arg4, arg5, (void *)arg6);
}

/* Forwarding wrapper (0x17ce80).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x164cf0 -- the frame is torn down
 * before the jump, so 0x164cf0 inherits this function's stack arguments.
 * Disassembling/decompiling 0x164cf0 in the pristine XBE shows it reads its
 * incoming stack slots at +0x04, +0x08, +0x14 and +0x18, so six dwords are
 * forwarded (the +0x0c and +0x10 slots are never read there, but they must
 * still exist to place the later ones).  The asserts inside 0x164cf0 name the
 * +0x04 slot "shader" and the +0x18 slot "vertex_buffer"
 * (c:\halo\SOURCE\rasterizer\xbox\rasterizer_xbox_environment.c:0x9a6 and
 * :0x9bf); the remaining slots have unknown meaning here and are forwarded
 * unchanged.  Reached only through a data (function-table) reference at
 * 0x195d7f (in FUN_00195d40). */
void FUN_0017ce80(void *shader, int arg2, int arg3, int arg4, int arg5,
                  void *vertex_buffer)
{
  FUN_00164cf0(shader, arg2, arg3, arg4, arg5, vertex_buffer);
}

/* Forwarding wrapper (0x17ceb0).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x165420 -- the frame is torn down
 * before the jump, so 0x165420 inherits this function's stack arguments.
 * Disassembling 0x165420 in the pristine XBE (bounds 0x165420..0x165774) shows
 * it reads its incoming stack slots at +0x04 .. +0x30 relative to its own
 * argument base, i.e. twelve dwords; the +0x28 slot (arg10 here) is never
 * observed accessed, but it must still exist to place the later ones.  Note
 * 0x165420 also WRITES its last slot (`or dword ptr [ebp+0x34],1` / `,7`), so
 * the forward must stay a tail call for the mutation to land in the real
 * caller's frame.  Slot semantics are unknown here and are forwarded
 * unchanged; the two pointer types are the only proven facts (0x165420 reads a
 * word at +0x24 of the first slot's pointer, and three floats at +0x0/+0x4/+0x8
 * of the eighth slot's pointer).  Reached only through a data (function-table)
 * reference at 0x195df2 (in FUN_00195dc0). */
void FUN_0017ceb0(void *arg1, int arg2, int arg3, int arg4, int arg5, int arg6,
                  int arg7, void *arg8, int arg9, int arg10, int arg11,
                  int arg12)
{
  FUN_00165420(arg1, arg2, arg3, arg4, arg5, arg6, arg7, (float *)arg8,
               (uint32_t *)arg9, arg10, arg11, arg12);
}

/* Forwarding wrapper (0x17cee0).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x165cb0 -- the frame is torn down
 * before the jump, so 0x165cb0 inherits this function's stack arguments.
 * Disassembling 0x165cb0 in the pristine XBE (bounds 0x165cb0..0x165dbd)
 * shows it reads its incoming slots at +0x08, +0x10, +0x14, +0x18 and +0x1c
 * relative to its own EBP, i.e. six dwords; the +0x0c slot (arg2 here) is
 * never observed accessed, but it must still exist to place the later ones.
 * 0x165cb0 writes nothing back into its argument slots, so a plain call is
 * sufficient.  Its asserts name the +0x08 slot "shader" and the +0x1c slot
 * "vertex_buffer" (c:\halo\SOURCE\rasterizer\xbox\
 * rasterizer_xbox_environment_fog.c:0x1ae and :0x1b3).  Only the
 * vertex_buffer slot is proven to be a pointer (0x165cb0 reads a word at its
 * target); the shader slot is only forwarded onward, and the remaining slots
 * have unknown meaning here and are forwarded unchanged.  Reached through a
 * data (function-table) reference used as a surface-draw callback. */
void FUN_0017cee0(void *shader, int arg2, int arg3, int arg4, int arg5,
                  void *vertex_buffer)
{
  FUN_00165cb0(shader, arg2, arg3, arg4, arg5, vertex_buffer);
}

/* Forwarding wrapper (0x17cf00).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x165de0 -- the frame is torn down
 * before the jump, so 0x165de0 inherits this function's stack arguments.
 * Disassembling 0x165de0 in the pristine XBE (bounds 0x165de0..0x165e68)
 * shows it reads three incoming slots relative to its own EBP: +0x08 as a
 * signed word (MOVSX ESI,AX; IMUL ESI,0x4c; ADD ESI,0x47ddfc -- a table index,
 * asserted >= 0 and < 4), +0x0c as a float (FMUL DWORD PTR [EBP+0xc]), and
 * +0x10 as a non-NULL pointer that receives three dwords ([EDI], [EDI+4] and
 * a literal 0 at [EDI+8]).  The sole caller, at 0x166a6e inside
 * FUN_00166890, pushes them in that order (PUSH EAX = LEA [EBP-0x30] out
 * pointer, PUSH ECX = dword loaded from 0x5a5e1c, PUSH EDX = word loaded from
 * 0x5a5bc2 zero-extended).  The meaning of the index and the scalar is
 * unknown here; they are forwarded unchanged.  The narrowing cast reflects
 * only the declared width of the callee at 0x165de0. */
void FUN_0017cf00(int param_1, float param_2, float *param_3)
{
  FUN_00165de0((int16_t)param_1, param_2, param_3);
}

/* Forwarding wrapper (0x17cf10).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x166890 -- the frame is torn down
 * before the jump, so 0x166890 inherits this function's stack arguments and
 * reads the incoming slot as its own [EBP+8].  0x166890 loads it narrow --
 * `MOV DI, WORD PTR [EBP+8]` at 0x16689a -- and immediately asserts
 * "pass==0 || pass==1" (rasterizer_xbox_environment_fog.c:499), so the dword
 * is a 0/1 pass index; the narrowing cast reflects the callee's declared
 * width.  Reached by two calls from FUN_00195ec0 (0x195ecb, 0x195efc). */
void FUN_0017cf10(int pass_index)
{
  FUN_00166890((int16_t)pass_index);
}

/* Forwarding wrapper (0x17cf20).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x1677d0 -- the frame is torn down
 * before the jump, so 0x1677d0 inherits this function's stack arguments.
 * 0x1677d0 (rasterizer_xbox_environment_fog.c, asserts at :0x3dc/:0x3e0/
 * :0x3e4/:0x3e5) reads its incoming slots at +0x04, +0x14 and +0x18 relative
 * to its own argument base, i.e. six dwords; the +0x08/+0x0c/+0x10 slots
 * (arg2..arg4 here) are never observed accessed, but they must still exist to
 * place the later ones.  Two slots are proven pointers: the +0x04 slot is
 * passed to shader_get_vertex_shader_permutation, and the +0x18 slot is
 * dereferenced as a word (`*(ushort *)slot6`) to feed FUN_00178b40.  The
 * +0x14 slot is an int accumulated into a frame-statistics counter.  Slot
 * semantics beyond that are unknown here and are forwarded unchanged.
 * 0x1677d0 writes nothing back into its argument slots, so a plain call is
 * sufficient.  Reached only through two data (function-table) references at
 * 0x195ee2 and 0x195f13 in FUN_00195ec0. */
void FUN_0017cf20(void *shader, int arg2, int arg3, int arg4, int arg5,
                  void *arg6)
{
  FUN_001677d0(shader, arg2, arg3, arg4, arg5, arg6);
}

/* Forwarding wrapper (0x17cf70).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x15f210 -- the frame is torn down
 * before the jump, so 0x15f210 inherits this wrapper's incoming stack
 * arguments and reads them as its own.  That four-instruction shape is MSVC's
 * argument-forwarding tail call and is what every other wrapper in this TU
 * compiles to (0x17cf10, 0x17cf20, 0x17cfc0); a wrapper that forwards no
 * argument compiles to a bare JMP with no frame at all, so the frame here is
 * evidence that at least one stack argument slot is passed through.  The
 * exact COUNT is unknown: nothing reads a slot on either side, and one, two
 * or three forwarded dwords all emit these same four instructions (N=2
 * measured at 100% as well).  One int
 * is the minimal signature consistent with the shape.
 *
 * The jump target is an empty function.  The bytes at 0x15f210 are C3
 * followed by fifteen 90 padding bytes, and the previous function in
 * rasterizer_xbox_dynavobgeom.obj ends at 0x15f209, so 0x15f210 is a
 * 16-byte-aligned entry point whose whole body is a bare RET -- not padding
 * the JMP happens to land on.  A bare RET proves only that the callee reads
 * nothing; its parameter comes from this caller's tail-call shape, not from
 * any observed access.  The call is emitted rather than elided because the
 * E9 target is part of the shape being recovered.  No references to
 * 0x17cf70 were found in the binary. */
void FUN_0017cf70(int param_1)
{
  FUN_0015f210(param_1);
}

/* Forwarding wrapper (0x17cf90).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x15f220 -- the frame is torn down
 * before the jump, so 0x15f220 inherits this wrapper's incoming stack
 * arguments and reads them as its own [EBP+8] and [EBP+0xc].  The argument
 * count is proven by the callee (disassembly at 0x15f224 / 0x15f24c): it
 * loads exactly two dwords, asserts each is non-NULL, and then field-copies
 * from the [EBP+0xc] object into the [EBP+8] object (MOV reg,[EDI+N] /
 * MOV [ESI+N],reg for N = 0x04, 0x09, 0x0a, 0x10, 0x14, 0x19, 0x1a, 0x20,
 * 0x24, 0x30, 0x34, 0x38, 0x3c, 0x48, 0x4c, 0x50, 0x54, 0x5c, 0x60, 0x7c,
 * 0x80, 0x84, 0x86).  The copy direction proves which slot is the
 * destination; the object type is unknown here, so both are void *.  No
 * references to 0x17cf90 were found in the binary. */
void FUN_0017cf90(void *dest, void *src)
{
  FUN_0015f220(dest, src);
}

/* Render sprites by forwarding to the dynavob geometry renderer (0x17cfa0). */
void rasterizer_sprites_render(void *render_data, void *vertices)
{
  FUN_0015f8e0(render_data, vertices);
}

/* Forwarding wrapper (0x17cfc0).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x17b000 -- the frame is torn down
 * before the jump, so 0x17b000 inherits this function's stack arguments and
 * reads them as its own [EBP+8] / [EBP+0xc].  This wrapper never touches the
 * argument slots itself, so their semantics are unknown here; the narrowing
 * casts reflect only the declared widths of the callee at 0x17b000. */
void FUN_0017cfc0(int param_1, int param_2)
{
  FUN_0017b000((int16_t)param_1, (uint16_t)param_2);
}

/* Forwarding wrapper (0x17cfd0).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x17b480 -- the frame is torn down
 * before the jump, so 0x17b480 inherits this function's stack arguments and
 * reads them as its own [EBP+8] / [EBP+0xc] / [EBP+0x10], and its AL return
 * becomes this function's return value.  This wrapper never touches the
 * argument slots itself, so their semantics are unknown here; the parameter
 * widths follow the callee, which loads all three slots as full DWORDs
 * (MOV reg,dword ptr at 0x17b4ac / 0x17b4b5 / 0x17b4b8 / 0x17b4cc), so the
 * third slot is int, not short. */
char FUN_0017cfd0(int param_1, int param_2, int param_3)
{
  return FUN_0017b480(param_1, param_2, param_3);
}

/* Forwarding wrapper (0x17cfe0).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x17b540 -- the frame is torn down
 * before the jump, so 0x17b540 inherits this function's stack argument and
 * reads the incoming dword as its own [EBP+8].
 *
 * The callee 0x17b540 forwards that dword unconverted into the second
 * (float) argument of D3DDevice_SetVertexData2f (`MOV EAX,[EBP+8]` at
 * 0x17b56c, `PUSH EAX` at 0x17b571 -- no FILD/FLD), so the value travelling
 * through this wrapper is a raw IEEE-754 float bit pattern, not an integer.
 * Both lifted call sites agree: 0x17b3xx pushes the literal 0x3f800000
 * (= 1.0f) and rasterizer_text.c loads a dword float field at refl+0x40.
 * The parameter therefore stays a dword (`unsigned int`) so the call sites
 * keep passing bits unchanged, and the argument slot is re-interpreted in
 * place for the float-typed callee.  Converting instead (`(float)value_bits`)
 * would emit a FILD and destroy the value; a union temporary was measured to
 * cost a stack round-trip that blocks the tail call (50% vs the 4-instruction
 * reference). */
void FUN_0017cfe0(unsigned int value_bits)
{
  FUN_0017b540(*(float *)&value_bits);
}

/* Forwarding wrapper (0x17cff0).  Same four-instruction shape as 0x17cfe0:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x17b580 -- the frame is torn down
 * before the jump, so 0x17b580 inherits this function's stack argument and
 * reads the incoming slot as its own [EBP+8].  kb.json declared this
 * `void FUN_0017cff0(void)` and Ghidra therefore showed a bare call with no
 * argument; the pass-through is recovered from the POP/JMP pair, not from the
 * decompiler.
 *
 * The callee loads the slot with `MOVZX EAX,byte ptr [EBP+8]` at 0x17b5a2 and
 * feeds it to D3DDevice_SetRenderState_ZEnable, so only the low byte is
 * observed and the parameter is `bool` -- matching the callee's own recovered
 * signature and keeping the forward conversion-free (an `int` parameter would
 * add a CMP/SETNE that the reference does not have).
 *
 * No xrefs to 0x17cff0 exist in the binary, so the caller-side meaning of the
 * flag is unknown beyond "Z-enable on/off". */
void FUN_0017cff0(bool enable)
{
  FUN_0017b580(enable);
}

/* Forwarding wrapper (0x17d000).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x17b5c0 -- the frame is torn down
 * before the jump, so 0x17b5c0 inherits this function's stack arguments and
 * reads them as its own [EBP+8] .. [EBP+0x1c] (six dwords).  kb.json declared
 * this `void FUN_0017d000(void)` and Ghidra therefore rendered the body as a
 * bare `FUN_0017b5c0()` call with no arguments; the pass-through is recovered
 * from the POP/JMP pair, not from the decompiler.  A `(void)` wrapper would
 * hand the callee a garbage `point`, which its own NULL assert at
 * rasterizer_xbox_widgets.c:0x164 immediately dereferences.
 *
 * The parameter types are the callee's own recovered signature (see
 * FUN_0017b5c0 in src/halo/rasterizer/xbox/rasterizer_xbox_widgets.c): a 2D
 * point, a >0 radius gate, an optional per-axis scale, an optional integer
 * texcoord repeat count, a rotation angle, and a flat vertex colour.  Matching
 * them exactly keeps the forward conversion-free, which is what lets the tail
 * call survive (the neighbouring 0x17cfe0 note records the measured cost of a
 * temporary that blocks it).
 *
 * No xrefs to 0x17d000 exist in the binary, so the caller-side meaning of the
 * arguments is unknown beyond the callee's use of them. */
void FUN_0017d000(float *point, float radius, float *scale,
                  float *texcoord_repeat, float theta, unsigned int color)
{
  FUN_0017b5c0(point, radius, scale, texcoord_repeat, theta, color);
}

/* Forwarding wrapper (0x17d030).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x17ba10 -- the frame is torn down
 * before the jump, so 0x17ba10 inherits this function's stack arguments and
 * reads them as its own [EBP+8] .. [EBP+0x10] (three dwords), and its EAX
 * return becomes this function's return value (Ghidra shows `extraout_EAX`).
 *
 * The parameter types are the callee's own recovered signature (see
 * rasterizer_widget_submit_occlusion_test at 0x17ba10 in
 * src/halo/rasterizer/rasterizer.c): a position pointer, a float radius, and
 * an unsigned index.  kb.json previously declared the middle slot `int`; the
 * slot is forwarded untouched by the POP/JMP, so the callee's own type is the
 * binary-backed one, and matching it keeps the forward conversion-free -- an
 * `int` parameter would insert a FILD that the reference does not have.
 *
 * The only reference is a call at 0x181bdb inside FUN_00181a90, which is not
 * yet lifted, so the caller-side meaning of the arguments is unknown beyond
 * the callee's use of them. */
int FUN_0017d030(float *position, float radius, unsigned int index)
{
  return rasterizer_widget_submit_occlusion_test(position, radius, index);
}

/* Forwarding wrapper (0x17d040).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x17adc0 -- the frame is torn down
 * before the jump, so 0x17adc0 inherits this function's stack argument and
 * reads it as its own [EBP+8], and its EAX return becomes this function's
 * return value.  The single dword parameter therefore comes from the POP/JMP
 * pair, not from the decompiler (Ghidra renders this as `void (void)` because
 * it cannot see through the tail jump).
 *
 * The callee is rasterizer_widget_get_occlusion_test_result at 0x17adc0 in
 * src/halo/rasterizer/xbox/rasterizer_xbox_widgets.c, declared there as
 * `unsigned int (unsigned int index)`.  Unlike the neighbouring 0x17d030 note,
 * the signedness difference against kb.json's `int` costs no instruction (an
 * int<->unsigned dword needs no conversion, whereas an int->float slot would
 * have inserted a FILD), so the kb.json declaration is left unchanged; the
 * sole caller, rasterizer_text.c:783, consumes the result in signed
 * arithmetic.
 *
 * That caller is the lens-flare occlusion path: the result is scaled by 0xff
 * against a per-flare divisor.  The meaning of `index` beyond the callee's own
 * use of it as a widget/occlusion-query slot is unknown. */
int FUN_0017d040(int index)
{
  return (int)rasterizer_widget_get_occlusion_test_result((unsigned int)index);
}

/* Forwarding wrapper (0x17d060).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x16e160 -- the frame is torn down
 * before the jump, so 0x16e160 runs on this function's incoming stack and
 * reads the incoming dwords as its own [EBP+8..0x18].  The wrapper's arity is
 * therefore the callee's arity; Ghidra renders both as `void (void)` because
 * it cannot see through the tail jump (its param_count for 0x16e160 is 0, and
 * that is wrong -- see the call-site evidence below).
 *
 * Arity and slot types come from the sole caller, render_blip at 0xdb4a0,
 * which is cdecl and cleans 20 bytes:
 *     000db495: PUSH EAX          ; arg5, materialized by SETZ AL at 0xdb48c
 *     000db496: PUSH ESI          ; arg4
 *     000db497: PUSH ECX          ; arg3 slot -- dummy push, overwritten below
 *     000db498: MOV  ECX,[EBP+0x14]
 *     000db49b: FSTP float [ESP]  ; arg3 is a float (push-then-fstp idiom)
 *     000db49e: PUSH ECX          ; arg2 = caller's [EBP+0x14]
 *     000db49f: PUSH EDX          ; arg1 = LEA EDX,[EBP-0xc], a stack pointer
 *     000db4a0: CALL 0x17d060
 *     000db4a5: ADD  ESP,0x14     ; 5 dword slots
 *
 * The callee is unlifted (FUN_0016e160); its string constants place it in
 * c:\halo\SOURCE\rasterizer\xbox\rasterizer_xbox_motion_sensor.c and it
 * issues D3DDevice_Begin, D3DDevice_SetVertexData* and D3DDevice_End, so it
 * draws motion-sensor geometry.  The meaning of the individual arguments
 * beyond their slot widths is unknown; they are forwarded unchanged. */
void FUN_0017d060(void *param_1, int param_2, float param_3, int param_4,
                  int param_5)
{
  FUN_0016e160(param_1, param_2, param_3, param_4, param_5);
}

/* 0x17d8f0.  Ghidra decompiles this as `void` and drops the result, but the
 * original never pops the x87 stack:
 *     0017d8f4: CALL 0x000b5aa0        ; game_time_get()
 *     0017d8f9: MOV  [EBP-0x4],EAX     ; int spill for the FILD cast
 *     0017d8fc: FILD dword [EBP-0x4]
 *     0017d8ff: FMUL float [0x002546a4] ; seconds-per-tick
 *     0017d908: RET                    ; product left in ST(0)
 * so the return value is a float.  Called four times from
 * the caller at 0x17dc70 (call sites 0x17dcd4, 0x17dcf8, 0x17dd61,
 * 0x17dd85).  The int cast is the left operand to keep the
 * FILD-then-FMUL-memory shape. */
float FUN_0017d8f0(void)
{
  return (float)game_time_get() * *(float *)0x2546a4;
}

/* rasterizer_screen_effects_initialize (0x17d910).  Allocates the 0x78-byte
 * cinematic screen-effect globals block from the game-state heap and halts if
 * the allocation fails.  The original has no frame; it computes into EAX,
 * TESTs EAX, stores to the global, then branches:
 *     0017d919: CALL 0x1bfbf0          ; game_state_malloc
 *     0017d91e: ADD  ESP,0xc
 *     0017d921: TEST EAX,EAX
 *     0017d923: MOV  [0x0047e4d4],EAX
 *     0017d928: JNZ  0x17d947
 * so the null test is on the returned value, not on a re-read of the global.
 * The .rdata assert literals (0x2af314 / 0x2af334 / line 0x36) name Bungie's
 * TU c:\halo\SOURCE\rasterizer\rasterizer_cinematics.c and are reproduced
 * verbatim; kb.json maps this address to rasterizer_sprites.obj.  The layout
 * of the 0x78-byte block is unknown. */
void rasterizer_screen_effects_initialize(void)
{
  void *globals;

  globals = game_state_malloc("screen effect filth", 0, 0x78);
  *(void **)0x47e4d4 = globals;
  if (globals == 0) {
    display_assert("cinematic_screen_effect_globals",
                   "c:\\halo\\SOURCE\\rasterizer\\rasterizer_cinematics.c",
                   0x36, 1);
    system_exit(-1);
  }
}

/* 0x17d950.  Resets the 0x78-byte cinematic screen-effect globals block
 * allocated by rasterizer_screen_effects_initialize (0x17d910): zero-fills
 * the whole block, then writes 1.0f into the four dwords at 0x64/0x68/0x6c/
 * 0x70 (an unknown 4-component identity, unproven whether colour or scale).
 *     0017d950: MOV  EAX,[0x0047e4d4]
 *     0017d955: TEST EAX,EAX
 *     0017d957: JZ   0x0017d97c        ; no-op when never allocated
 *     0017d959: PUSH 0x78
 *     0017d95b: PUSH 0x0
 *     0017d95d: PUSH EAX
 *     0017d95e: CALL 0x0008db80        ; csmemset
 *     0017d963: MOV  EAX,[0x0047e4d4]  ; re-read AFTER the call
 *     0017d968: MOV  ECX,0x3f800000
 *     0017d96d: MOV  [EAX+0x64],ECX
 *     0017d970: MOV  [EAX+0x68],ECX
 *     0017d973: MOV  [EAX+0x6c],ECX
 *     0017d976: ADD  ESP,0xc
 *     0017d979: MOV  [EAX+0x70],ECX
 * The global is loaded twice (once for the test, once after csmemset), so the
 * pointer is re-read through the raw address rather than cached in a local.
 * The block layout is unknown; offsets stay raw. */
void FUN_0017d950(void)
{
  if (*(void **)0x47e4d4 != 0) {
    csmemset(*(void **)0x47e4d4, 0, 0x78);
    *(float *)((char *)*(void **)0x47e4d4 + 0x64) = 1.0f;
    *(float *)((char *)*(void **)0x47e4d4 + 0x68) = 1.0f;
    *(float *)((char *)*(void **)0x47e4d4 + 0x6c) = 1.0f;
    *(float *)((char *)*(void **)0x47e4d4 + 0x70) = 1.0f;
  }
}

/* 0x17da00.  Latches the cinematic screen-effect globals block allocated by
 * rasterizer_screen_effects_initialize (0x17d910): optionally zero-fills the
 * first 0x38 bytes and sets the byte flag at 0x39, then always sets the byte
 * flag at 0x38.  The zero-fill runs when the caller's byte argument is
 * non-zero, or when the 0x39 flag is still clear.
 *     0017da03: MOV  EAX,[0x0047e4d4]
 *     0017da08: TEST EAX,EAX
 *     0017da0a: JZ   0x0017da34        ; no-op when never allocated
 *     0017da0c: MOV  CL,byte ptr [EBP + 0x8]
 *     0017da0f: TEST CL,CL
 *     0017da11: JNZ  0x0017da1a
 *     0017da13: MOV  CL,byte ptr [EAX + 0x39]
 *     0017da16: TEST CL,CL
 *     0017da18: JNZ  0x0017da30        ; skip the reset, keep the old EAX
 *     0017da1a: PUSH 0x38
 *     0017da1c: PUSH 0x0
 *     0017da1e: PUSH EAX
 *     0017da1f: CALL 0x0008db80        ; csmemset
 *     0017da24: MOV  EAX,[0x0047e4d4]  ; re-read AFTER the call
 *     0017da29: ADD  ESP,0xc
 *     0017da2c: MOV  byte ptr [EAX + 0x39],0x1
 *     0017da30: MOV  byte ptr [EAX + 0x38],0x1
 * The 0x38 store has two predecessors and merges the pre-call EAX with the
 * reloaded one, so the pointer is held in a local rather than re-read at every
 * use.  Only 0x38 bytes are cleared out of the 0x78-byte block, so this is a
 * partial reset; the block layout and the meaning of the two flag bytes are
 * unknown and the offsets stay raw.  The only xref is the CALL at 0x0c3681 in
 * FUN_000c3660 (hs.c).  The one-byte stack argument is proven by this
 * function's own MOV CL,byte ptr [EBP + 0x8] and matches the kb decl. */
void FUN_0017da00(char param_1)
{
  void *globals;

  globals = *(void **)0x47e4d4;
  if (globals != 0) {
    if (param_1 != 0 || *(char *)((char *)globals + 0x39) == 0) {
      csmemset(globals, 0, 0x38);
      globals = *(void **)0x47e4d4;
      *(char *)((char *)globals + 0x39) = 1;
    }
    *(char *)((char *)globals + 0x38) = 1;
  }
}

/* 0x17db20.  Stores three dwords into the cinematic screen-effect globals
 * block allocated by rasterizer_screen_effects_initialize (0x17d910), at
 * offsets 0x14/0x18/0x1c; no-op when the block was never allocated.
 *     0017db23: MOV  EAX,[0x0047e4d4]
 *     0017db28: TEST EAX,EAX
 *     0017db2a: JZ   0x0017db3e        ; no-op when never allocated
 *     0017db2c: MOV  ECX,dword ptr [EBP + 0x8]
 *     0017db2f: MOV  EDX,dword ptr [EBP + 0xc]
 *     0017db32: MOV  [EAX + 0x14],ECX
 *     0017db35: MOV  ECX,dword ptr [EBP + 0x10]
 *     0017db38: MOV  [EAX + 0x18],EDX
 *     0017db3b: MOV  [EAX + 0x1c],ECX
 * The global is loaded once and all three stores use that pointer (there is
 * no intervening call), so it is held in a local.  Every argument is copied
 * as a raw dword -- there is no FLD/FSTP anywhere in the original -- so the
 * two float parameters from the kb decl are copied through a dword pun rather
 * than assigned through float lvalues; that spelling is what keeps the plain
 * MOV [EAX+0x18]/[EAX+0x1c] store shape in the reference lane.  (Our clang
 * build still lowers the same bit-copy through FLDS/FSTPS because the float
 * parameters are x87-live at entry; that is value-identical, not a second
 * semantic.)  The block layout is unknown; the meaning of the
 * three stored dwords is unproven, so the offsets stay raw.  The only xref is
 * the CALL at 0x0c378f in FUN_000c3760. */
void FUN_0017db20(int param_1, float param_2, float param_3)
{
  void *globals;

  globals = *(void **)0x47e4d4;
  if (globals != 0) {
    *(int *)((char *)globals + 0x14) = param_1;
    *(unsigned int *)((char *)globals + 0x18) = *(unsigned int *)&param_2;
    *(unsigned int *)((char *)globals + 0x1c) = *(unsigned int *)&param_3;
  }
}

/* rasterizer_screen_effect_set_video (0x17db40).  Arms the cinematic
 * "video" screen effect in the 0x78-byte globals block allocated by
 * rasterizer_screen_effects_initialize (0x17d910): clears the first 0x38
 * bytes, zeroes the ten dwords at 0x3c..0x60, raises the flag byte at 0x23,
 * records the caller's mode word at 0x24, and caches two bitmap-data pointers
 * at 0x28 and 0x34 resolved from the two global bitmap tag indices held in the
 * rasterizer globals block at 0x476204 (+0x128 and +0x138).  When either index
 * is -1 the effect is refused with the .rdata error string at 0x2af380.
 * The assert literals (0x29da1c / 0x2af334 / line 0xe1) name Bungie's TU
 * c:\halo\SOURCE\rasterizer\rasterizer_cinematics.c; kb.json maps this address
 * to rasterizer_sprites.obj.  The only xref is the CALL at 0x0c37d9 in
 * FUN_000c37b0 (hs.c).
 *
 * Global re-reads are reproduced exactly as the original sequences them --
 * [0x47e4d4] is loaded at 0x17db43 (null test), 0x17db9b (csmemset argument),
 * 0x17dba9 (base for the 0x3c..0x60 / 0x23 / 0x24 stores), 0x17dc01 (base for
 * 0x28/0x2c/0x30) and 0x17dc3f (base for 0x34); [0x476204] is loaded at
 * 0x17db53 (null test AND both -1 compares, not reloaded between them),
 * 0x17dbdb (+0x128) and 0x17dc1a (+0x138) -- so each is held in a local only
 * across call-free runs of stores.
 *
 * The mode parameter arrives in a full dword slot but only its low word is
 * consumed: 0017dbae MOV CX,word ptr [EBP+0x8] / 0017dbd7 MOV word ptr
 * [EAX+0x24],CX.  The kb decl keeps `int` (the hs.c caller at 0x0c37d9 widens
 * a uint16 field into the slot); the truncation is expressed at the store.
 * The float parameter is copied as a raw dword -- there is no FLD/FSTP in the
 * original -- so it goes through a dword pun like FUN_0017db20 above, while
 * the 1.0f immediate at +0x30 (0x3f800000) is a plain float store.
 *
 * Both tag lookups reuse the already-pushed 0x30/0 slots as
 * tag_block_get_element's 2nd/3rd arguments (ADD ESP,8 pops only tag_get's own
 * two); C has no spelling for that sharing, so the nested call is written
 * plainly.  The ARG_COUNT note on the error() call site is the variadic
 * ellipsis being counted as a parameter -- the disassembly is
 * PUSH 0x2af380 / PUSH 0x2 / CALL / ADD ESP,8, i.e. two arguments.
 * The 0x78-byte block layout is unknown, so the offsets stay raw. */
void rasterizer_screen_effect_set_video(int mode, float value)
{
  char *globals;
  char *raster_globals;
  void *bitmap_data;

  if (*(void **)0x47e4d4 == 0) {
    return;
  }
  raster_globals = *(char **)0x476204;
  if (raster_globals == 0) {
    display_assert("global_rasterizer_data",
                   "c:\\halo\\SOURCE\\rasterizer\\rasterizer_cinematics.c",
                   0xe1, 1);
    system_exit(-1);
  }
  if (*(int *)(raster_globals + 0x128) == -1 ||
      *(int *)(raster_globals + 0x138) == -1) {
    error(2, "### ERROR cinematics failed to set video mode; global bitmaps "
             "are not set");
    return;
  }
  csmemset(*(void **)0x47e4d4, 0, 0x38);
  globals = *(char **)0x47e4d4;
  *(int *)(globals + 0x3c) = 0;
  *(int *)(globals + 0x40) = 0;
  *(int *)(globals + 0x44) = 0;
  *(int *)(globals + 0x48) = 0;
  *(int *)(globals + 0x4c) = 0;
  *(int *)(globals + 0x50) = 0;
  *(int *)(globals + 0x54) = 0;
  *(int *)(globals + 0x58) = 0;
  *(int *)(globals + 0x5c) = 0;
  *(int *)(globals + 0x60) = 0;
  *(char *)(globals + 0x23) = 1;
  *(short *)(globals + 0x24) = (short)mode;
  raster_globals = *(char **)0x476204;
  bitmap_data = tag_block_get_element(
    (char *)tag_get(0x6269746d /* 'bitm' */, *(int *)(raster_globals + 0x128)) +
      0x60,
    0, 0x30);
  globals = *(char **)0x47e4d4;
  *(void **)(globals + 0x28) = bitmap_data;
  *(unsigned int *)(globals + 0x2c) = *(unsigned int *)&value;
  *(float *)(globals + 0x30) = 1.0f;
  raster_globals = *(char **)0x476204;
  bitmap_data = tag_block_get_element(
    (char *)tag_get(0x6269746d /* 'bitm' */, *(int *)(raster_globals + 0x138)) +
      0x60,
    0, 0x30);
  globals = *(char **)0x47e4d4;
  *(void **)(globals + 0x34) = bitmap_data;
}

/* FUN_0017dc60 (0x17dc60).  Clears the byte at +0x38 of the 0x78-byte screen
 * effect globals block held at [0x47e4d4], guarded by a null test on the block
 * pointer.  The whole body is five instructions:
 *
 *     0017dc60: MOV  EAX,[0x0047e4d4]
 *     0017dc65: TEST EAX,EAX
 *     0017dc67: JZ   0x0017dc6d
 *     0017dc69: MOV  byte ptr [EAX + 0x38],0x0
 *     0017dc6d: RET
 *
 * The global is loaded ONCE (single MOV, reused as the store base), so it is
 * held in a local rather than re-read.  No callers or callees are recorded in
 * the Ghidra artifact for this address, and no assert/string literal names the
 * function, so the name and the meaning of the +0x38 byte stay unknown.  Note
 * that +0x38 is deliberately just past the 0x38 bytes that
 * rasterizer_screen_effect_set_video (0x17db40) clears with csmemset, so it is
 * a separate flag from that effect's own state -- but nothing in the binary
 * proves what it selects, so no name is asserted here. */
void FUN_0017dc60(void)
{
  char *globals;

  globals = *(char **)0x47e4d4;
  if (globals != 0) {
    *(globals + 0x38) = 0;
  }
}

/* FUN_0017dec0 (0x17dec0).  Stores its single dword argument into +0x74 of the
 * 0x78-byte screen effect globals block held at [0x47e4d4], guarded by a null
 * test on the block pointer.  The whole body is nine instructions:
 *
 *     0017dec0: PUSH EBP
 *     0017dec1: MOV  EBP,ESP
 *     0017dec3: MOV  EAX,[0x0047e4d4]
 *     0017dec8: TEST EAX,EAX
 *     0017deca: JZ   0x0017ded2
 *     0017decc: MOV  ECX,dword ptr [EBP + 0x8]
 *     0017decf: MOV  dword ptr [EAX + 0x74],ECX
 *     0017ded2: POP  EBP
 *     0017ded3: RET
 *
 * The global is loaded ONCE (single MOV, reused as the store base), so it is
 * held in a local rather than re-read, matching FUN_0017dc60 above.  The
 * argument is moved through ECX as a plain dword -- there is no FLD/FSTP -- so
 * the kb decl's `int` is kept even though the neighbouring slots +0x64..+0x70
 * that FUN_0017d950 initialises are floats; adjacency is a lead, not evidence.
 * The Ghidra artifact records no callers, no callees and no string/assert for
 * this address, so both the function name and the meaning of the +0x74 field
 * are unproven and stay raw. */
void FUN_0017dec0(int param_1)
{
  char *globals;

  globals = *(char **)0x47e4d4;
  if (globals != 0) {
    *(int *)(globals + 0x74) = param_1;
  }
}

/* FUN_0017df10 (0x17df10).  Reserves one slot in a debug-geometry buffer and
 * returns its index, or -1 when either the caller's own per-buffer counter
 * (*count) or the shared global counter [0x47e4f4] has already reached the
 * 0x2000 cap.
 *
 * Ghidra decompiles this as `void` because the return value is parked in ESI:
 *
 *     0017df19: OR   ESI,0xffffffff
 *     ...
 *     0017df49: JNZ  0x17df75          (skips the MOV, keeps EAX = old *count)
 *     0017df73: MOV  EAX,ESI           (overflow path -> -1)
 *
 * EAX still holds the pre-increment `*count` loaded at 0x17df16 on both
 * success paths, so the success result is the index of the slot just claimed.
 * Both compares are signed (JGE at 0x17df21 / 0x17df2d), so the counters are
 * signed int.
 *
 * [0x47e4f4] is loaded twice (CMP at 0x17df23, MOV EDX at 0x17df34) -- the
 * increment is written as a re-read to preserve that shape.  [0x47e4f8] is a
 * one-shot "already warned" byte flag.  [0x3256ba] is the usual 16-bit
 * rasterizer render-mode selector; == 2 bumps the debug counter at 0x5a5540.
 *
 * The error() call pushes exactly two arguments (ADD ESP,0x8 at 0x17df69);
 * the ARG_COUNT hazard on it is the varargs "..." counted as a third param. */
int FUN_0017df10(int *count)
{
  int index;
  int result;
  short *render_mode;
  int *debug_counter;

  result = -1;
  render_mode = (short *)0x3256ba;
  index = *count;
  if (index < 0x2000 && *(int *)0x47e4f4 < 0x2000) {
    *count = index + 1;
    *(int *)0x47e4f4 = *(int *)0x47e4f4 + 1;
    debug_counter = (int *)0x5a5540;
    if (*render_mode == 2) {
      *debug_counter = *debug_counter + 1;
    }
    result = index;
    goto done;
  }
  if (*(char *)0x47e4f8 == 0) {
    error(2, "### WARNING debug geometry buffer overflow");
    *(char *)0x47e4f8 = 1;
  }
done:
  return result;
}

/* Zero-fills four rasterizer-sprite dword globals (0x17e010).  The whole body
 * is XOR EAX,EAX followed by four MOV [imm32],EAX and RET -- no calls, no
 * arguments, no return value.
 *
 * The four dwords are written as signed int to stay consistent with
 * FUN_0017df10 above, which compares [0x47e4f4] with JGE (signed) and uses the
 * same raw-cast form.  Meanings of [0x47e4e0], [0x47e4e8] and [0x47e4f0] are
 * unknown; only their width (dword) and their reset-to-zero role are proven.
 *
 * Explicit unknown: the dwords at 0x47e4e4 and 0x47e4ec lie between the four
 * written slots and are NOT touched here.  Whether they belong to the same
 * record is unproven by this function. */
void FUN_0017e010(void)
{
  *(int *)0x47e4e0 = 0;
  *(int *)0x47e4e8 = 0;
  *(int *)0x47e4f0 = 0;
  *(int *)0x47e4f4 = 0;
}

/* Disposes the three debug-geometry buffers (0x17e040).  Guarded by the byte
 * flag at 0x47e4d8: when it is non-zero the three pointers are asserted
 * non-null, freed, and the flag is cleared.
 *
 * The .rdata assert literals name Bungie's TU
 * c:\halo\SOURCE\rasterizer\rasterizer_debug.c and the three fields
 * `debug_data.opaque_triangles` (0x47e4dc), `debug_data.opaque_lines`
 * (0x47e4e4) and `debug_data.non_opaque_primitives` (0x47e4ec); kb.json maps
 * this address to rasterizer_sprites.obj.  The field names prove what the
 * three pointers ARE, not what this function is called, so the name stays raw.
 *
 * Each pointer is loaded twice in the reference -- once for the null test
 * (0017e04d / 0017e076 / 0017e09f) and again as the debug_free argument
 * (0017e0c8 / 0017e0dd / 0017e0f3) -- so they are re-read rather than held in
 * locals.  The function is frameless (entry is MOV AL,[0x47e4d8]; no PUSH EBP)
 * and no locals are introduced.
 *
 * The flag is a byte (MOV AL / MOV byte ptr [0x47e4d8],0x0), not a dword.
 * ADD ESP,0x24 at 0017e109 is the coalesced cleanup for the three 3-dword
 * debug_free calls; the assert paths emit no cleanup because system_exit is
 * noreturn.  The ARG_COUNT hazard on the last debug_free is that coalescing.
 *
 * Explicit unknown: the layout of the `debug_data` record these three fields
 * belong to, and whether the neighbouring dwords zeroed by FUN_0017e010
 * (0x47e4e0 / 0x47e4e8 / 0x47e4f0) are counters paired with them. */
void FUN_0017e040(void)
{
  if (*(char *)0x47e4d8 != 0) {
    if (*(void **)0x47e4dc == 0) {
      display_assert("debug_data.opaque_triangles",
                     "c:\\halo\\SOURCE\\rasterizer\\rasterizer_debug.c", 0x89,
                     1);
      system_exit(-1);
    }
    if (*(void **)0x47e4e4 == 0) {
      display_assert("debug_data.opaque_lines",
                     "c:\\halo\\SOURCE\\rasterizer\\rasterizer_debug.c", 0x8a,
                     1);
      system_exit(-1);
    }
    if (*(void **)0x47e4ec == 0) {
      display_assert("debug_data.non_opaque_primitives",
                     "c:\\halo\\SOURCE\\rasterizer\\rasterizer_debug.c", 0x8b,
                     1);
      system_exit(-1);
    }
    debug_free(*(void **)0x47e4dc,
               "c:\\halo\\SOURCE\\rasterizer\\rasterizer_debug.c", 0x8d);
    debug_free(*(void **)0x47e4e4,
               "c:\\halo\\SOURCE\\rasterizer\\rasterizer_debug.c", 0x8e);
    debug_free(*(void **)0x47e4ec,
               "c:\\halo\\SOURCE\\rasterizer\\rasterizer_debug.c", 0x8f);
    *(char *)0x47e4d8 = 0;
  }
}

/* Record comparator (0x17e130), cdecl with two record pointers on the stack
 * ([EBP+8] -> EDX, [EBP+0xc] -> ESI) and the ordering key returned in EAX.
 * The plain RET (no immediate) and the two stack loads prove cdecl/2 args.
 *
 * Three record fields are touched; none has a proven meaning:
 *   +0x30  signed 16-bit (MOVSX word at 0017e177 / 0017e184)
 *   +0x34  float        (FLD/FCOMP at 0017e14c / 0017e15e)
 *   +0x38  byte flag    (MOV AL/BL + TEST at 0017e136 / 0017e144)
 *
 * When neither flag byte is set the result is the ordinary three-way float
 * comparison on +0x34.  The reference does it with two independent
 * FLD/FCOMP/FNSTSW pairs:
 *   TEST AH,0x41 masks C3|C0, so it is zero only for a > b -> ECX = 1.
 *   TEST AH,0x05 masks C2|C0; JP is taken for gt (0 bits), eq (0 bits) and
 *   unordered (2 bits), so only a < b falls through to MOV EAX,-1.
 * Both fields are therefore re-read per comparison rather than held in float
 * locals -- a float temporary would also let clang keep the value in 80-bit
 * under -mno-sse where MSVC narrows on assignment.
 *
 * When either flag byte is set the float key is ignored and the result is
 * built from the signed 16-bit field instead: negated for the first record,
 * added for the second.  The two entries into that block (JNZ 0017e177 skips
 * the shared TEST AL,AL, JNZ 0017e173 lands on it) are the shape of a single
 * `||` guard whose body re-tests each flag.
 *
 * Explicit unknown: the record type, and what the +0x38 flag distinguishes --
 * only that it switches the sort key from the +0x34 float to the +0x30 short.
 * The PUSH EBX / MOV BL / TEST BL / POP EBX at 0017e143..0017e149 is MSVC
 * spending a callee-saved register on one byte test; it is not reproducible
 * from C. */
int FUN_0017e130(void *record_a, void *record_b)
{
  int result;

  result = 0;
  if (*(char *)((char *)record_a + 0x38) != 0 ||
      *(char *)((char *)record_b + 0x38) != 0) {
    if (*(char *)((char *)record_a + 0x38) != 0) {
      result = -(int)*(short *)((char *)record_a + 0x30);
    }
    if (*(char *)((char *)record_b + 0x38) != 0) {
      result += (int)*(short *)((char *)record_b + 0x30);
    }
    return result;
  }
  if (*(float *)((char *)record_a + 0x34) >
      *(float *)((char *)record_b + 0x34)) {
    result = 1;
  }
  if (*(float *)((char *)record_a + 0x34) <
      *(float *)((char *)record_b + 0x34)) {
    return -1;
  }
  return result;
}

/* Forwarding wrapper (0x17eb10).  A real frame -- PUSH EBP / MOV EBP,ESP ...
 * CALL 0x17e5b0 / ADD ESP,0x10 / POP EBP / RET -- not a tail call.  The third
 * incoming dword is loaded once (MOV EAX,[EBP+0x10] at 0017eb13) and pushed
 * TWICE (PUSH EAX / PUSH EAX at 0017eb19..0017eb1a) before [EBP+0xc] and
 * [EBP+0x8], so 0x17e5b0 receives it as both of its trailing arguments.  The
 * duplicated argument is binary-proven, not a decompiler artifact.
 *
 * Explicit unknown: the meaning of the duplicated dword.  The callee decl
 * names its trailing pair transform_a/transform_b; nothing here proves what
 * they select. */
void FUN_0017eb10(float *vert_a, float *vert_b, int param_3)
{
  FUN_0017e5b0(vert_a, vert_b, param_3, param_3);
}

/* Allocator/initializer (0x17eb50).  Returns bool in AL: the reference sets
 * MOV BL,0x1 before the call and MOV AL,BL on the success path, XOR AL,AL on
 * the failure path.  Ghidra's decompile shows `void` and drops the return
 * value; the disassembly and the kb.json decl both say bool, so the decompile
 * is wrong here.  The PUSH EBX / MOV BL / MOV AL,BL / POP EBX shape is MSVC
 * spending a callee-saved register to materialize the constant 1 and is not
 * reproducible from C.
 *
 * The store to the global at 0x47ec40 is unconditional (MOV [0x47ec40],EAX
 * follows TEST EAX,EAX at 0017eb69..0017eb6b).
 *
 * The __FILE__ / __LINE__ pair passed to debug_malloc is binary-proven:
 * PUSH 0x29 / PUSH 0x2af728 names rasterizer_frame_statistics.c line 0x29,
 * even though kb.json maps this address to rasterizer_sprites.obj.
 *
 * The error() call pushes exactly two arguments (PUSH 0x2af710 / PUSH 0x2,
 * ADD ESP,0x8) -- no varargs are supplied despite the variadic decl.
 *
 * Explicit unknown: what the 0x24000-byte block at 0x47ec40 holds.  It is read
 * elsewhere as an opaque pointer (rasterizer_text.c). */
bool FUN_0017eb50(void)
{
  *(void **)0x47ec40 = debug_malloc(
    0x24000, false,
    "c:\\halo\\SOURCE\\rasterizer\\rasterizer_frame_statistics.c", 0x29);
  if (*(void **)0x47ec40 == NULL) {
    error(2, "### ERROR out of memory");
    return false;
  }
  return true;
}

/* Zero-fills a 0x170-byte global block at 0x5a5400 (0x17eb90).  The whole
 * body is six instructions -- PUSH 0x170 / PUSH 0x0 / PUSH 0x5a5400 /
 * CALL csmemset / ADD ESP,0xc / RET -- with no frame, no arguments and no
 * return value; the csmemset result in EAX is discarded.
 *
 * The debug counter at 0x5a5540 documented on FUN_0017df10 above lies inside
 * this range (0x5a5400 + 0x170 == 0x5a5570), so this function resets it.  That
 * is containment only: it does not prove the 0x170 bytes form one record.
 *
 * Explicit unknown: the layout and meaning of the block.  Only its base, its
 * size, and its reset-to-zero role are proven here.  csmemset (0x8db80) is
 * called by name rather than memset so the call is not lowered inline. */
void FUN_0017eb90(void)
{
  csmemset((void *)0x5a5400, 0, 0x170);
}

/* Frame-statistics recording start (0x17ed30).  Nine instructions, no frame,
 * no arguments, no return value:
 *
 *   MOV byte ptr [0x3256b8],0x1     -- byte-sized store of 1 (a flag)
 *   CALL 0x8e370                    -- system_milliseconds(), result in EAX
 *   MOV ECX,dword ptr [0x32566c]
 *   MOV [0x47ec48],EAX              -- start timestamp
 *   MOV EAX,[0x325668]
 *   MOV dword ptr [0x47ec4c],0x0
 *   MOV [0x47ec50],EAX
 *   MOV dword ptr [0x47ec54],ECX
 *
 * The two loads from 0x325668 / 0x32566c are hoisted by MSVC ahead of the
 * stores they feed; the C order below preserves the store order, which is the
 * observable one.  0x325668 and 0x32566c are an adjacent dword pair copied
 * verbatim into the adjacent pair 0x47ec50 / 0x47ec54.
 *
 * Explicit unknowns: the meaning of the byte flag at 0x3256b8, and what the
 * 0x325668/0x32566c pair holds.  Elsewhere in this tree 0x325668 is read both
 * as a frame-parity selector (rasterizer_xbox_decals.c) and as the low half of
 * a 64-bit counter incremented with ADD/ADC; nothing in THIS function
 * discriminates, so the two dwords are copied independently rather than as one
 * 64-bit quantity.  The zero written to 0x47ec4c is likewise unexplained --
 * only its position between the timestamp and the copied pair is proven. */
void FUN_0017ed30(void)
{
  *(unsigned char *)0x3256b8 = 1;
  *(unsigned int *)0x47ec48 = system_milliseconds();
  *(int *)0x47ec4c = 0;
  *(int *)0x47ec50 = *(int *)0x325668;
  *(int *)0x47ec54 = *(int *)0x32566c;
}
