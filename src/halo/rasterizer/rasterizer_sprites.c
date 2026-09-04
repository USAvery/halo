/* Forwarding wrapper (0x17cd60).  The original is four instructions:
 * PUSH EBP / MOV EBP,ESP / POP EBP / JMP 0x163590 -- the frame is torn down
 * before the jump, so 0x163590 inherits this function's stack arguments and
 * reads the incoming dword as its own [EBP+8].  Semantics of the argument are
 * unknown; it is forwarded unchanged. */
void FUN_0017cd60(int object_handle)
{
  FUN_00163590(object_handle);
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
