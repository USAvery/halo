/* 0x19ccf0 — Lay out `text` without drawing it and report the resulting
 * bounds plus the rectangle of the final pen position.
 *
 * Confirmed from the pristine-XBE disassembly of [0x19ccf0, 0x19cda9):
 *   - Resets the draw_string min/max accumulator block that
 *     FUN_0019b3c0 updates per element (same globals documented in
 *     draw_string.c): 0x4d9afc/0x4d9afe (min_y/min_x) <- 0x7fff,
 *     0x4d9b00/0x4d9b02 (max_y/max_x) <- 0x8000.
 *   - `mov esi,[0x4d9b14]` / `mov edi,[0x4d9b0c]` feed the @<si>/@<edi>
 *     register args of FUN_0019bcc0(style, font_index); its EAX result is
 *     stored to 0x4d9b04 and later read for two int16 fields at +4 and +6
 *     (vertical extents above/below the baseline — meaning unproven).
 *   - `push 0x19b3c0 / push esi / push ecx(=&screen_pos) / push 0 / push 0 /
 *      push eax(=text)` then `call 0x19c5d0` + `add esp,0x18`: cdecl
 *     draw_string(FUN_0019b3c0, screen_pos, &screen_pos, 0, 0, text).
 *     The third argument is the address of this function's own first
 *     parameter slot; after the call [ebp+8]/[ebp+0xa] are re-read as two
 *     int16s (the pen position the callee wrote back), while the ORIGINAL
 *     pointer value is still live in ESI and is dereferenced once for
 *     out_bounds[0]. Both reads are reproduced here.
 *   - Store order is [eax+2], [eax+6], [eax], [eax+4] then [ecx+2], [ecx],
 *     [ecx+6], [ecx+4]; 0x4d9b04 is re-loaded between the two eax stores,
 *     so the global is re-read rather than cached.
 *
 * Inferred: the +0x14 rectangle is a one-pixel-wide vertical rect at the
 * final pen position (x = pen_x .. pen_x + 1), i.e. a text cursor/caret;
 * the name below records the shape, not proven intent.
 */
void draw_string_compute_bounds(void *screen_pos, char *text,
                                int16_t *out_bounds, int16_t *out_cursor)
{
  void *start_pos;
  const int16_t *pen;

  start_pos = screen_pos;
  pen = (const int16_t *)&screen_pos;

  *(int16_t *)0x4d9afc = 0x7fff;
  *(int16_t *)0x4d9afe = 0x7fff;
  *(int16_t *)0x4d9b00 = (int16_t)0x8000;
  *(int16_t *)0x4d9b02 = (int16_t)0x8000;

  *(void **)0x4d9b04 = FUN_0019bcc0(*(int16_t *)0x4d9b14, *(int *)0x4d9b0c);

  draw_string(FUN_0019b3c0, screen_pos, &screen_pos, 0, 0, text);

  out_cursor[1] = pen[0];
  out_cursor[3] = (int16_t)(pen[0] + 1);
  out_cursor[0] = (int16_t)(pen[1] - (*(const int16_t **)0x4d9b04)[2]);
  out_cursor[2] = (int16_t)((*(const int16_t **)0x4d9b04)[3] + pen[1]);

  out_bounds[1] = *(int16_t *)0x4d9afe;
  out_bounds[0] = *(const int16_t *)start_pos;
  out_bounds[3] = *(int16_t *)0x4d9b02;
  out_bounds[2] = out_cursor[2];
}

/* 0x19ce70 — Seed the shared draw-string cursor hit-test search and resolve
 * the text cursor position nearest a screen point.
 *
 * Writes the reference point (*ref_point, a packed pair of shorts) and a
 * "no match yet" sentinel into the small globals block at 0x4d9af0 that
 * FUN_0019b430 (draw_string.c) reads/updates per candidate text element:
 *   0x4d9af0 (short ref_x) / 0x4d9af2 (short ref_y) <- *ref_point
 *   0x4d9af4 (short) cursor marker A -> reset to 0, returned
 *   0x4d9af6 (short) best distance   -> reset to 0x7fff (sentinel)
 *   0x4d9af8 (short) cursor marker B -> reset to 0
 * Then walks the text layout via draw_string (international_strings.obj,
 * same TU) with FUN_0019b430 as the per-element hit-test callback — same
 * calling idiom as the draw_string call in rasterizer_text.c. Returns the
 * resulting cursor marker.
 */
int16_t FUN_0019ce70(void *screen_pos, char *text, const void *ref_point)
{
  *(int *)0x4d9af0 = *(const int *)ref_point;
  *(int16_t *)0x4d9af4 = 0;
  *(int16_t *)0x4d9af6 = 0x7fff;
  *(int16_t *)0x4d9af8 = 0;

  draw_string(FUN_0019b430, screen_pos, 0, 0, 0, text);

  return *(int16_t *)0x4d9af4;
}

/* 0x19cec0 — Draw `text` into a bitmap through the software glyph
 * blitter (bitmap_draw_character, 0x19b910) instead of the rasterizer.
 *
 * Confirmed from the pristine-XBE disassembly of [0x19cec0, 0x19cfd1):
 *   - `mov [0x4d9ae8],esi` stores the bitmap pointer into the software
 *     glyph global block documented in draw_string.c (+0x00 bitmap_data*)
 *     unconditionally, before the format test.
 *   - `movsx eax,[esi+0xc]` / `cmp eax,0xb` / `ja end` then an 12-byte
 *     index table at 0x19cfdc feeding a 2-entry jump table at 0x19cfd4:
 *     only the signed int16 at bitmap+0xc in {0,1,2,6,11} reaches the
 *     body; every other value (negative ones via the unsigned JA) returns
 *     with no side effect beyond the 0x4d9ae8 store. Field meaning is
 *     unproven here; the case set matches a pixel-format selector.
 *   - Both clamp blocks are the same shape: two `cmp bm,r / jg` minima
 *     against bitmap+0x4 and bitmap+0x6, two `test/jge` clamps of r[0]
 *     and r[1] to >= 0, then cdecl FUN_001089a0(&local, max(0,r[1]),
 *     max(0,r[0]), min(bm[2],r[3]), min(bm[3],r[2])) with `add esp,0x14`.
 *     The frame is `sub esp,0x10` holding exactly the two 8-byte rects at
 *     [ebp-8] (screen) and [ebp-0x10] (clip).
 *   - The screen_bounds block runs on the NULL path (`test ebx,ebx` /
 *     `jnz`), and the original dereferences that same NULL pointer there:
 *     three of the four loads are assembled as absolute `[0x2]`, `[0x4]`,
 *     `[0x6]` (MSVC propagated the proven-zero base) and the fourth as
 *     `[ebx]`. Reproduced as written rather than "corrected" — the reads
 *     are on the parameter, so a NULL screen_bounds faults in the
 *     original exactly as it does here.
 *   - Tail call `push ecx(=text) / push 0 / push eax / push 0 / push ebx /
 *     push 0x19b910` + `add esp,0x18`: cdecl
 *     draw_string(bitmap_draw_character, screen_bounds, 0, clip, 0, text),
 *     where clip is 0 when clip_bounds was NULL.
 */
void bitmap_draw_string(void *bitmap, int16_t *screen_bounds,
                        int16_t *clip_bounds, char *text)
{
  const int16_t *bm;
  int16_t screen_rect[4];
  int16_t clip_rect[4];
  void *clip_arg;

  bm = (const int16_t *)bitmap;
  *(void **)0x4d9ae8 = bitmap;

  switch (bm[6]) {
  case 0:
  case 1:
  case 2:
  case 6:
  case 11:
    if (screen_bounds == 0) {
      FUN_001089a0((int *)screen_rect,
                   screen_bounds[1] < 0 ? 0 : screen_bounds[1],
                   screen_bounds[0] < 0 ? 0 : screen_bounds[0],
                   bm[2] > screen_bounds[3] ? screen_bounds[3] : bm[2],
                   bm[3] > screen_bounds[2] ? screen_bounds[2] : bm[3]);
      screen_bounds = screen_rect;
    }

    clip_arg = 0;
    if (clip_bounds != 0) {
      FUN_001089a0((int *)clip_rect, clip_bounds[1] < 0 ? 0 : clip_bounds[1],
                   clip_bounds[0] < 0 ? 0 : clip_bounds[0],
                   bm[2] > clip_bounds[3] ? clip_bounds[3] : bm[2],
                   bm[3] > clip_bounds[2] ? clip_bounds[2] : bm[3]);
      clip_arg = clip_rect;
    }

    draw_string(bitmap_draw_character, screen_bounds, 0, clip_arg, 0, text);
    break;
  }
}

/* 0x19d060 — Set the language/encoding selector read by
 * unicode_is_multibyte below. Values outside 0..5 (the encodings that
 * switch recognizes: 1=Shift-JIS, 2=Big5, 3=GBK, 4=Johab-like,
 * 5=Thai-like, 0=none) are clamped to 0. Stores into the encoding
 * selector global at 0x4d9be0. */
void set_language_code(short code)
{
  if (code < 0)
    code = 0;
  else if (code >= 6)
    code = 0;

  *(int16_t *)0x4d9be0 = code;
}

/* 0x19d080 — Return true if the two bytes starting at p form a valid
 * multibyte character under the current language encoding.
 *
 * The encoding selector lives at 0x4d9be0 (int16_t):
 *   1 = Shift-JIS  (lead: 0x81..0x9f or 0xe0..0xfe; trail: 0x40..0xfc, !=0x7f)
 *   2 = Big5       (lead: 0xa1..0xfe;                trail: 0xa1..0xfe)
 *   3 = GBK        (lead: 0x81..0xfe;                trail: 0x40..0x7e or
 * 0xa1..0xfe) 4 = Johab-like (lead: 0x81..0xfe;                trail:
 * 0x41..0x5a or 0x61..0x7a or 0x81..0xfe) 5 = Thai-like  (lead: 0x84..0xd3 or
 * 0xd8..0xde or 0xe0..0xf9; trail: 0x41..0x7e or 0x81..0xfe) Any other encoding
 * value returns false.
 *
 * A leading '|' byte (0x7c) followed by a byte in "ibukprlctn" is treated
 * as multibyte regardless of the encoding setting. */
bool unicode_is_multibyte(const uint8_t *p)
{
  uint8_t b0;
  uint8_t b1;
  bool result;

  b0 = p[0];
  b1 = p[1];
  result = 0;
  if (b0 != 0) {
    if (b0 == 0x7c && b1 != 0 &&
        crt_strchr("ibukprlctn", (int)b1) != (char *)0x0) {
      result = 1;
    } else {
      switch (*(int16_t *)0x4d9be0) {
      case 1:
        if ((b0 >= 0x81 && b0 <= 0x9f) || (b0 >= 0xe0 && b0 <= 0xfe)) {
          if (b1 >= 0x40 && b1 <= 0xfc && b1 != 0x7f)
            result = 1;
        }
        break;
      case 2:
        if (b0 >= 0xa1 && b0 <= 0xfe && b1 >= 0xa1 && b1 <= 0xfe)
          result = 1;
        break;
      case 3:
        if (b0 >= 0x81 && b0 <= 0xfe &&
            ((b1 >= 0x40 && b1 <= 0x7e) || (b1 >= 0xa1 && b1 <= 0xfe)))
          result = 1;
        break;
      case 4:
        if (b0 >= 0x81 && b0 <= 0xfe &&
            ((b1 >= 0x41 && b1 <= 0x5a) || (b1 >= 0x61 && b1 <= 0x7a) ||
             (b1 >= 0x81 && b1 <= 0xfe)))
          result = 1;
        break;
      case 5:
        if (((b0 >= 0x84 && b0 <= 0xd3) || (b0 >= 0xd8 && b0 <= 0xde) ||
             (b0 >= 0xe0 && b0 <= 0xf9)) &&
            ((b1 >= 0x41 && b1 <= 0x7e) || (b1 >= 0x81 && b1 <= 0xfe)))
          result = 1;
        break;
      }
    }
  }
  return result;
}

/* 0x19d1b0 — Read the character at *cursor and advance cursor forward.
 * If the byte is a multibyte lead byte (per unicode_is_multibyte), reads
 * two bytes big-endian and advances by 2; otherwise reads one byte and
 * advances by 1. Returns the character as uint16_t. */
uint16_t unicode_cursor_forward(const char *str, int16_t *cursor)
{
  if (*cursor < 0 || (size_t)*cursor > csstrlen(str)) {
    display_assert(csprintf((char *)0x5ab100,
                            "#%d is out of range in string @%p", (int)*cursor,
                            str),
                   "c:\\halo\\SOURCE\\text\\international_strings.c", 0x20, 1);
    system_exit(-1);
  }

  str += *cursor;
  if (unicode_is_multibyte((const uint8_t *)str)) {
    uint16_t ch = (uint16_t)(((uint8_t)str[0] << 8) | (uint8_t)str[1]);
    *cursor += 2;
    return ch;
  } else {
    uint16_t ch = (uint8_t)str[0];
    *cursor += 1;
    return ch;
  }
}

/* 0x19d240 — Move cursor backward by one character. Scans forward from
 * position 0 using unicode_cursor_forward, tracking the previous position.
 * Warns if *cursor falls between multibyte character bytes. Sets *cursor
 * to the start of the preceding character and returns it. */
uint16_t unicode_cursor_backward(const char *str, int16_t *cursor)
{
  int16_t pos;
  int16_t prev;
  uint16_t ch;

  if (*cursor <= 0 || (unsigned int)(int)*cursor > csstrlen(str)) {
    display_assert(csprintf((char *)0x5ab100,
                            "#%d is out of range in string @%p", (int)*cursor,
                            str),
                   "c:\\halo\\SOURCE\\text\\international_strings.c", 0x37, 1);
    system_exit(-1);
  }

  pos = 0;
  do {
    prev = pos;
    ch = unicode_cursor_forward(str, &pos);
  } while (pos < *cursor);

  if (pos != *cursor) {
    display_assert(csprintf((char *)0x5ab100,
                            "index #%d is inbetween characters in string %p",
                            (int)*cursor, str),
                   "c:\\halo\\SOURCE\\text\\international_strings.c", 0x43, 0);
  }

  *cursor = prev;
  return ch;
}

/* 0x19d300 — Snap cursor to a valid character boundary. Scans forward from
 * position 0 using unicode_cursor_forward until reaching or passing *cursor,
 * then writes the last valid position back to *cursor. */
void unicode_snap_cursor(const char *str, int16_t *cursor)
{
  int16_t pos;

  if (*cursor < 0 || (unsigned int)(int)*cursor > csstrlen(str)) {
    display_assert(csprintf((char *)0x5ab100,
                            "#%d is out of range in string @%p", (int)*cursor,
                            str),
                   "c:\\halo\\SOURCE\\text\\international_strings.c", 0x55, 1);
    system_exit(-1);
  }

  pos = 0;
  if (*cursor > 0) {
    do {
      unicode_cursor_forward(str, &pos);
    } while (pos < *cursor);
  }

  *cursor = pos;
}

/* 0x19d380 — Return true if character ch occurs anywhere in str. Walks str
 * with unicode_cursor_forward from position 0, comparing each decoded
 * character against ch, stopping at the terminating 0 (not found) or at the
 * first match (found). Callers (parse_string, draw_string.obj) use this to
 * test a decoded character against small delimiter-set strings. */
bool unicode_string_contains_char(uint16_t ch, const char *str)
{
  int16_t cursor;
  uint16_t c;

  cursor = 0;
  do {
    c = unicode_cursor_forward(str, &cursor);
    if (c == 0)
      return 0;
  } while (c != ch);

  return 1;
}

/* 0x19d3c0 — Fetch one string out of a 'str#' string-list tag by element
 * index, or return the placeholder text when anything about the request is
 * out of range.
 *
 * Confirmed from the pristine-XBE disassembly of [0x19d3c0, 0x19d413):
 *   - `cmp eax,-1 / jz` on the first parameter: NONE tag index takes the
 *     fallback path immediately, before any call.
 *   - `push eax / push 0x73747223 / call 0x1ba140 / add esp,8`: cdecl
 *     tag_get('str#', index).  0x73747223 is 's','t','r','#' little-endian.
 *   - `mov cx,[ebp+0xc] / test cx,cx / jl` then `mov edx,[eax] / movsx
 *     ecx,cx / cmp ecx,edx / jge`: the int16 element index is bounds-checked
 *     signed against the dword at offset 0 of the tag (the tag_block count).
 *     The int16 load happens after the call in the reference; the value is
 *     unchanged by it, only the schedule differs.
 *   - `push 0x14 / push ecx / push eax / call 0x19b210 / add esp,0xc`: cdecl
 *     tag_block_get_element(tag, index, 0x14) — a 20-byte element, the
 *     tag_data shape (dword size at +0x00, data pointer at +0x0c).
 *   - `mov ecx,[eax] / test ecx,ecx / jle`: the element's size dword is
 *     loaded BEFORE the store below and reused for it, so the length is not
 *     re-read after the data pointer is taken; reproduced with a local.
 *   - `mov eax,[eax+0xc] / mov byte [ecx+eax-1],0`: force-terminates the
 *     string data in place at data[size - 1], then returns that pointer.
 *   - Every rejecting branch falls to `mov eax,esi`, where ESI was loaded
 *     with 0x2b4560 in the prologue — the address of the "<missing string>"
 *     literal.
 *
 * Unknown: whether offsets +0x04/+0x08/+0x10 of the element are used
 * elsewhere; this function touches only +0x00 and +0x0c, so the element is
 * accessed as raw dwords rather than through an invented struct.
 */
char *FUN_0019d3c0(int index, short param_2)
{
  int *tag;
  int *element;
  int size;
  char *data;

  if (index != NONE) {
    tag = (int *)tag_get(0x73747223, index);
    if (param_2 >= 0 && (int)param_2 < *tag) {
      element = (int *)tag_block_get_element(tag, (int)param_2, 0x14);
      size = *element;
      if (size > 0) {
        data = (char *)element[3];
        data[size - 1] = '\0';
        return data;
      }
    }
  }

  return "<missing string>";
}

/* Shared unsigned-compare bound for the u* buffer helpers below
 * (umemchr, umemcmp): both guard asserts name this identifier verbatim
 * ("count < MAXIMUM_MEMCMP_SIZE" / "(count >= 0) && (count <=
 * MAXIMUM_MEMCMP_SIZE)") and both compile to a single unsigned compare
 * against the literal 0x10000000 (CMP EDI,0x10000000 / JB resp. JBE) --
 * confirmed identical immediate at both call sites via direct XBE
 * disassembly. */
#define MAXIMUM_MEMCMP_SIZE 0x10000000

/* 0x19d480 — Validate buffer/count then forward to the CRT memchr.
 *
 * Confirmed: two guard asserts recovered verbatim from the binary, both
 *            attributed to c:\halo\SOURCE\text\unicode.c (a different
 *            original TU than the rest of this file's asserts, which is
 *            why the literal file string differs from
 *            international_strings.c below):
 *              line 0x54 "buffer"                      -> buffer != NULL
 *              line 0x55 "count < MAXIMUM_MEMCMP_SIZE"  -> unsigned compare
 *                (CMP EDI,0x10000000 / JB) against MAXIMUM_MEMCMP_SIZE.
 * Confirmed: no EAX fixup after the CALL — _memchr's return value (a
 *            pointer into buffer, or NULL) is this function's return value.
 * Confirmed: call args via disassembly PUSH order (PUSH count; PUSH c;
 *            PUSH buffer -> cdecl call _memchr(buffer, c, count)).
 */
void *umemchr(void *buffer, int c, size_t count)
{
  if (!(buffer)) {
    display_assert("buffer", "c:\\halo\\SOURCE\\text\\unicode.c", 0x54, 1);
    system_exit(-1);
  }
  if (!(count < MAXIMUM_MEMCMP_SIZE)) {
    display_assert("count < MAXIMUM_MEMCMP_SIZE",
                   "c:\\halo\\SOURCE\\text\\unicode.c", 0x55, 1);
    system_exit(-1);
  }

  return _memchr(buffer, c, count);
}

/* 0x19d4f0 — Validate dest/src/count/non-overlap then forward to csmemcpy.
 *
 * Confirmed via direct XBE disassembly (the fingerprinted Ghidra artifact
 * for this address carried no decompile/callees/call-site audit — every
 * field held a "Ghidra is not reachable" error — so the bytes below are
 * read straight from the pristine XBE over
 * tools/verify/function_bounds.json's [0x19d4f0, 0x19d584) span):
 *   0019d4f0 push ebp / mov ebp,esp / push ebx / mov ebx,[ebp+0xc]
 *            / push esi / mov esi,[ebp+8] / test esi,esi / push edi
 *            / je 0x19d504 ; test ebx,ebx / jne 0x19d521
 *              -> combined short-circuit: falls into the assert block
 *                 when dest==0 OR src==0.
 *   0019d504..0019d51e: display_assert("dest && src",
 *              "c:\\halo\\SOURCE\\text\\unicode.c", 0x60, 1);
 *              system_exit(-1);  (strings read at 0x2b4660 / 0x2b45b4)
 *   0019d521 mov edi,[ebp+0x10] / cmp edi,0x10000000 / jb 0x19d549
 *              -> single unsigned compare; note this site is JB (strict),
 *                 unlike umemmove's JBE at 0x19d631, and the assert text
 *                 at 0x2b4628 correspondingly reads "<" not "<=".
 *   0019d52c..0019d546: display_assert("(count >= 0) && (count < "
 *              "MAXIMUM_MEMCPY_MEMMOVE_SIZE)",
 *              "c:\\halo\\SOURCE\\text\\unicode.c", 0x61, 1);
 *              system_exit(-1);
 *   0019d549 lea eax,[ebx+edi] (src+count) / cmp eax,esi (dest)
 *            / jbe 0x19d574 ; lea ecx,[esi+edi] (dest+count)
 *            / cmp ecx,ebx (src) / jbe 0x19d574
 *              -> non-overlap check, src-side term evaluated first.
 *   0019d557..0019d571: display_assert(
 *              "(((char *)src+count) <= (char *)dest) || "
 *              "(((char *)dest+count) <= (char *)src)",
 *              "c:\\halo\\SOURCE\\text\\unicode.c", 0x62, 1);
 *              system_exit(-1);  (cond string read at 0x2b45d8)
 *   0019d574 push edi(count) / push ebx(src) / push esi(dest)
 *            / call 0x8e0b0 (csmemcpy, kb.json-confirmed cdecl
 *              void *csmemcpy(void *destination, void *source, size_t size))
 *            / add esp,0xc / pop edi,esi,ebx,ebp / ret.
 *
 * Unknown: the epilogue neither sets nor clears EAX, so whether the
 * original returned csmemcpy's pointer or was declared void is not
 * decidable from these bytes; declared void here to match the umemmove /
 * umemset siblings in this TU. Codegen is identical either way.
 */
void umemcpy(void *dest, const void *src, size_t count)
{
  if (!(dest && src)) {
    display_assert("dest && src", "c:\\halo\\SOURCE\\text\\unicode.c", 0x60, 1);
    system_exit(-1);
  }
  if (!(count < MAXIMUM_MEMCPY_MEMMOVE_SIZE)) {
    display_assert("(count >= 0) && (count < MAXIMUM_MEMCPY_MEMMOVE_SIZE)",
                   "c:\\halo\\SOURCE\\text\\unicode.c", 0x61, 1);
    system_exit(-1);
  }
  if (!((((const char *)src + count) <= (const char *)dest) ||
        (((char *)dest + count) <= (const char *)src))) {
    display_assert("(((char *)src+count) <= (char *)dest) || "
                   "(((char *)dest+count) <= (char *)src)",
                   "c:\\halo\\SOURCE\\text\\unicode.c", 0x62, 1);
    system_exit(-1);
  }

  csmemcpy(dest, (void *)src, count);
}

/* 0x19d590 — Validate buffer1/buffer2/count then forward to csmemcmp.
 *
 * Confirmed via direct XBE disassembly (Ghidra MCP unreachable this
 * session; artifact cache had no decompile/callees for this address, so
 * the disassembly below is read straight from the pristine XBE at
 * tools/verify/function_bounds.json's [0x19d590, 0x19d5f9) span):
 *   0019d590 push ebp / mov ebp,esp / push ebx / mov ebx,[ebp+0xc]
 *            / push esi / mov esi,[ebp+8] / test esi,esi / push edi
 *            / je 0x19d5a4 ; test ebx,ebx / jne 0x19d5c1
 *              -> combined short-circuit: falls into the assert block
 *                 when buffer1==0 OR buffer2==0.
 *   0019d5a4..0019d5be: display_assert("buffer1 && buffer2",
 *              "c:\\halo\\SOURCE\\text\\unicode.c", 0x6d, 1);
 *              system_exit(-1);  (string literals read at 0x2b469c /
 *              0x2b45b4, matching umemchr's file string above)
 *   0019d5c1 mov edi,[ebp+0x10] / cmp edi,0x10000000 / jbe 0x19d5e9
 *              -> single unsigned compare (the ">=0" half of the
 *                 assert text is a tautology for size_t and folds away)
 *   0019d5cc..0019d5e6: display_assert("(count >= 0) && (count <= "
 *              "MAXIMUM_MEMCMP_SIZE)", "c:\\halo\\SOURCE\\text\\unicode.c",
 *              0x6e, 1); system_exit(-1);  (cond string read at
 *              0x2b466c)
 *   0019d5e9 push edi(count) / push ebx(buffer2) / push esi(buffer1)
 *            / call 0x8da40 (csmemcmp, kb.json-confirmed cdecl
 *              int csmemcmp(const void *a, const void *b, int size))
 *            / add esp,0xc -> cdecl cleanup, no EAX fixup: csmemcmp's
 *              return value is this function's return value.
 */
int umemcmp(const void *buffer1, const void *buffer2, size_t count)
{
  if (!(buffer1 && buffer2)) {
    display_assert("buffer1 && buffer2", "c:\\halo\\SOURCE\\text\\unicode.c",
                   0x6d, 1);
    system_exit(-1);
  }
  if (!(count <= MAXIMUM_MEMCMP_SIZE)) {
    display_assert("(count >= 0) && (count <= MAXIMUM_MEMCMP_SIZE)",
                   "c:\\halo\\SOURCE\\text\\unicode.c", 0x6e, 1);
    system_exit(-1);
  }

  return csmemcmp(buffer1, buffer2, count);
}

/* 0x19d600 — Validate dest/src/count then forward to csmemmove.
 *
 * Confirmed via direct XBE disassembly (Ghidra MCP unreachable this
 * session; artifact cache had no decompile/callees for this address, so
 * the disassembly below is read straight from the pristine XBE at
 * tools/verify/function_bounds.json's [0x19d600, 0x19d669) span):
 *   0019d600 push ebp / mov ebp,esp / push ebx / mov ebx,[ebp+0xc]
 *            / push esi / mov esi,[ebp+8] / test esi,esi / push edi
 *            / je 0x19d614 ; test ebx,ebx / jne 0x19d631
 *              -> combined short-circuit: falls into the assert block
 *                 when dest==0 OR src==0.
 *   0019d614..0019d62e: display_assert("dest && src",
 *              "c:\\halo\\SOURCE\\text\\unicode.c", 0x79, 1);
 *              system_exit(-1);  (strings read at 0x2b4660 / 0x2b45b4)
 *   0019d631 mov edi,[ebp+0x10] / cmp edi,0x10000000 / jbe 0x19d659
 *              -> single unsigned compare against MAXIMUM_MEMCPY_MEMMOVE_SIZE
 *                 (the ">=0" half of the assert text is a tautology for
 *                 size_t and folds away)
 *   0019d63c..0019d656: display_assert("(count >= 0) && (count <= "
 *              "MAXIMUM_MEMCPY_MEMMOVE_SIZE)",
 *              "c:\\halo\\SOURCE\\text\\unicode.c", 0x7a, 1);
 *              system_exit(-1);  (cond string read at 0x2b46b0)
 *   0019d659 push edi(count) / push ebx(src) / push esi(dest)
 *            / call 0x8dae0 (csmemmove, kb.json-confirmed cdecl
 *              void csmemmove(void *dest, const void *src, unsigned int size))
 *            / add esp,0xc -> cdecl cleanup, void return (no EAX fixup).
 */
void umemmove(void *dest, const void *src, size_t count)
{
  if (!(dest && src)) {
    display_assert("dest && src", "c:\\halo\\SOURCE\\text\\unicode.c", 0x79, 1);
    system_exit(-1);
  }
  if (!(count <= MAXIMUM_MEMCPY_MEMMOVE_SIZE)) {
    display_assert("(count >= 0) && (count <= MAXIMUM_MEMCPY_MEMMOVE_SIZE)",
                   "c:\\halo\\SOURCE\\text\\unicode.c", 0x7a, 1);
    system_exit(-1);
  }

  csmemmove(dest, src, count);
}

/* 0x19d670 — Validate buffer/count then forward to csmemset.
 *
 * Confirmed via direct XBE disassembly (Ghidra MCP unreachable this
 * session; artifact cache had no decompile/callees for this address, so
 * the disassembly below is read straight from the pristine XBE at
 * tools/verify/function_bounds.json's [0x19d670, 0x19d6d9) span):
 *   0019d670 push ebp / mov ebp,esp / push esi / mov esi,[ebp+8]
 *            / test esi,esi / push edi / jne 0x19d69c
 *              -> falls into the assert block when buffer==0.
 *   0019d67c..0019d699: display_assert("buffer",
 *              "c:\\halo\\SOURCE\\text\\unicode.c", 0x85, 1);
 *              system_exit(-1);  (strings read at 0x267900 / 0x2b45b4)
 *   0019d69c mov edi,[ebp+0x10] / cmp edi,0x10000000 / jbe 0x19d6c7
 *              -> single unsigned compare against MAXIMUM_MEMSET_SIZE
 *                 (the ">=0" half of the assert text is a tautology for
 *                 size_t and folds away)
 *   0019d6a7..0019d6c4: display_assert("(count >= 0) && (count <= "
 *              "MAXIMUM_MEMSET_SIZE)", "c:\\halo\\SOURCE\\text\\unicode.c",
 *              0x86, 1); system_exit(-1);  (cond string read at 0x2b46e8
 *              -- a distinct macro name from umemcmp's MAXIMUM_MEMCMP_SIZE
 *              and umemmove's MAXIMUM_MEMCPY_MEMMOVE_SIZE, though all
 *              three share the same 0x10000000 literal)
 *   0019d6c7 mov eax,[ebp+0xc] / push edi(count) / push eax(c)
 *            / push esi(buffer) / call 0x8db80 (csmemset, kb.json-confirmed
 *              cdecl void *csmemset(void *buffer, int c, size_t size))
 *            / add esp,0xc -> cdecl cleanup, no EAX fixup: csmemset's
 *              return value (buffer) is this function's return value.
 */
#define MAXIMUM_MEMSET_SIZE 0x10000000

void *umemset(void *buffer, int c, size_t count)
{
  if (!(buffer)) {
    display_assert("buffer", "c:\\halo\\SOURCE\\text\\unicode.c", 0x85, 1);
    system_exit(-1);
  }
  if (!(count <= MAXIMUM_MEMSET_SIZE)) {
    display_assert("(count >= 0) && (count <= MAXIMUM_MEMSET_SIZE)",
                   "c:\\halo\\SOURCE\\text\\unicode.c", 0x86, 1);
    system_exit(-1);
  }

  return csmemset(buffer, c, count);
}

/* 0x19d6e0 — Validate source length and dest/src non-overlap, then forward to
 * the CRT wide-string copy.
 *
 * Confirmed via direct XBE disassembly (the fingerprinted Ghidra artifact for
 * this target carries only {"error": "Ghidra is not reachable ..."} in its
 * decompile/disassembly/callers/xrefs fields, so the evidence below is read
 * straight from the pristine XBE — md5 c7869590a1c64ad034e49a5ee0c02465 — over
 * tools/verify/function_bounds.json's [0x19d6e0, 0x19d75c) span):
 *   0019d6e0 push ebp / mov ebp,esp / push ebx / push esi / push edi
 *   0019d6e6 mov edi,[ebp+0xc] (src) / push edi / call 0x1db11e (_wcslen)
 *            / mov esi,eax (source_size) / add esp,4 -> cdecl 1-arg cleanup.
 *   0019d6f4 cmp esi,0x8000 / jb 0x19d71c
 *              -> single unsigned compare against MAXIMUM_STRING_SIZE; the
 *                 ">= 0" half of the assert text is a tautology for size_t
 *                 and folds away, exactly as in umemset above.
 *   0019d6fc..0019d719: display_assert("(source_size >= 0) && (source_size < "
 *              "MAXIMUM_STRING_SIZE)", "c:\\halo\\SOURCE\\text\\unicode.c",
 *              0x92, 1); system_exit(-1);  (cond string read at 0x2b4754,
 *              file string at 0x2b45b4)
 *   0019d71c mov ebx,[ebp+8] (dest) -- note dest is loaded only after the
 *              first assert block; that is scheduling, not a dependency.
 *   0019d71f lea eax,[edi+esi*2] (src + source_size, wchar_t arithmetic)
 *            / cmp eax,ebx / jb 0x19d74d      -> (src+source_size) < dest
 *   0019d726 lea ecx,[ebx+esi*2] (dest + source_size)
 *            / cmp ecx,edi / jb 0x19d74d      -> (dest+source_size) < src
 *              Both are JB (unsigned), and either one alone skips the assert,
 *              so the guard is the || form the assert string spells out.
 *   0019d72d..0019d74a: display_assert("((src+source_size) < dest) || ((dest +"
 *              " source_size) < src)", "c:\\halo\\SOURCE\\text\\unicode.c",
 *              0x93, 1); system_exit(-1);  (cond string read at 0x2b4718)
 *   0019d74d push edi(src) / push ebx(dest) / call 0x1db180 / add esp,8
 *              -> cdecl (dest, src). 0x1db180 disassembles as a plain
 *                 word-at-a-time copy loop returning [esp+4], i.e.
 *                 wchar_t *_wcscpy(wchar_t *dest, const wchar_t *src); its
 *                 EAX is discarded here (no fixup before the epilogue), so
 *                 this function returns void.
 */
/* UNRESOLVED: the assert text proves Bungie's macro here is spelled
 * MAXIMUM_STRING_SIZE, but the CMP at 0x19d6f4 proves its value in unicode.c
 * is 0x8000, while src/common.h's MAXIMUM_STRING_SIZE is 0x2000. Whether
 * unicode.c shadowed the common.h macro or the two are genuinely distinct
 * constants is not established by this function's bytes, so the local literal
 * is kept under a distinct name rather than silently redefining the shared
 * macro or bending this call site to 0x2000. */
#define UNICODE_C_MAXIMUM_STRING_SIZE 0x8000

void align_to_character(wchar_t *dest, const wchar_t *src)
{
  size_t source_size;

  source_size = _wcslen(src);
  if (!(source_size < UNICODE_C_MAXIMUM_STRING_SIZE)) {
    display_assert("(source_size >= 0) && (source_size < MAXIMUM_STRING_SIZE)",
                   "c:\\halo\\SOURCE\\text\\unicode.c", 0x92, 1);
    system_exit(-1);
  }
  if (!((src + source_size) < dest || (dest + source_size) < src)) {
    display_assert("((src+source_size) < dest) || ((dest + source_size) < src)",
                   "c:\\halo\\SOURCE\\text\\unicode.c", 0x93, 1);
    system_exit(-1);
  }

  _wcscpy(dest, src);
}

/* 0x19d760 — append `src` to `dest` after validating both strings.
 *
 * Confirmed from the pristine-XBE disassembly of [0x19d760, 0x19d801):
 *   - Both parameters are loaded into ESI ([ebp+8] = dest) and EDI
 *     ([ebp+0xc] = src) up front; the NULL test is `TEST ESI,ESI / JZ` then
 *     `TEST EDI,EDI / JNZ`, i.e. the short-circuit `dest && src`.
 *   - Each failing check calls display_assert(reason, file, line, 1)
 *     followed by `PUSH -1 / CALL 0x8e2f0` (system_exit), lines 0x9d/0x9e/0x9f
 *     of c:\halo\SOURCE\text\unicode.c.
 *   - Both length checks are `CALL 0x1db11e` (_wcslen) with a single cdecl
 *     push and `ADD ESP,4`, then `CMP EAX,0x8000 / JC` — an UNSIGNED
 *     compare, so the length is kept in a size_t. dest is measured first,
 *     src second; the same local is reused for both.
 *   - The tail is `PUSH EDI / PUSH ESI / CALL 0x1db156 / ADD ESP,8`, i.e.
 *     cdecl _wcscat(dest, src). Its EAX result is discarded with no fixup
 *     before the epilogue, so this function returns void.
 */
void ustrcat(wchar_t *dest, const wchar_t *src)
{
  size_t string_size;

  if (!dest || !src) {
    display_assert("dest && src", "c:\\halo\\SOURCE\\text\\unicode.c", 0x9d, 1);
    system_exit(-1);
  }

  string_size = _wcslen(dest);
  if (!(string_size < UNICODE_C_MAXIMUM_STRING_SIZE)) {
    display_assert("wcslen(dest) < MAXIMUM_STRING_SIZE",
                   "c:\\halo\\SOURCE\\text\\unicode.c", 0x9e, 1);
    system_exit(-1);
  }

  string_size = _wcslen(src);
  if (!(string_size < UNICODE_C_MAXIMUM_STRING_SIZE)) {
    display_assert("wcslen(src) < MAXIMUM_STRING_SIZE",
                   "c:\\halo\\SOURCE\\text\\unicode.c", 0x9f, 1);
    system_exit(-1);
  }

  _wcscat(dest, src);
}
