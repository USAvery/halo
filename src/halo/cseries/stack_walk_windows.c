/* Stack walker and linker map symbol table loader for Xbox.
 *
 * Walks the x86 EBP chain to collect return addresses, resolves them
 * against a parsed MSVC linker .map file, and dumps the stack trace
 * to a file or the error log.
 *
 * The map file path is hardcoded in stack_walk_initialize() — change it there
 * to control which .map file is loaded (e.g. when running off HDD). */

/* Globals in the stack-walk/profiler data region.  The first three are real
 * data symbols in kb.json (stack_walk_windows.obj) rather than absolute pointer
 * casts: MSVC models a store through a constant-address cast as a store through
 * an unknown pointer that may alias the stack, so it will not schedule an
 * argument PUSH across one.  With declared globals it hoists the PUSH the way
 * the reference does -- +16.7pp on stack_walk_dispose, and +10pp operand-level
 * on stack_walk_initialize.
 *
 *   0x2ee780: int32_t    stack_walk_bias        — map RVA -> runtime VA bias
 *   0x2ee784: uint8_t    stack_walk_load_failed — 1 if map load failed
 *   0x2ee788: int32_t[3] stack_walk_symbols     — {count, names, entries}
 *   0x449ef8: uint32_t * g_sw_frame             — current frame ptr in walk
 *   0x449efc: uint32_t * g_sw_base              — stack base (floor) in walk */

/* Cross-TU helpers in profile.obj (called by name; kb.json decls drive
 * the halo.xbe.def export names that the linker resolves against). */

/* Parse a hex digit; returns 0-15 or -1 on non-hex. */
/* -----------------------------------------------------------------------
 * FUN_00092370 (0x92370) — walk EBP chain and collect return addresses.
 *
 * __fastcall: ECX = skip (frames to skip), EDX = frames[] output array.
 * Additional args on stack: max = capacity of frames[], count = out ptr.
 *
 * Captures the caller's EBP via __builtin_frame_address(0), saves it to
 * the profiler globals so walk_ebp_chain can bound the walk, then
 * delegates to the profile.obj helper at 0x922a0.
 * ----------------------------------------------------------------------- */
void __fastcall FUN_00092370(int skip, int32_t *frames, uint32_t max,
                             uint32_t *count)
{
  uint32_t *frame;
  uint32_t return_address;
  uint32_t i;

#if defined(_MSC_VER) && !defined(__clang__)
  frame = (uint32_t *)((char *)_AddressOfReturnAddress() - 4);
#else
  frame = (uint32_t *)__builtin_frame_address(0);
#endif
  *(uint32_t **)0x449ef8 = frame;
  *(uint32_t **)0x449efc = (uint32_t *)((char *)frame - 0xc);

  if (((uint32_t)frame & 3) != 0 ||
      (uint32_t)frame >= (uint32_t)*(uint32_t **)0x449efc) {
    frame = NULL;
    *(uint32_t **)0x449ef8 = frame;
  }

  while (skip != 0) {
    skip--;
    if (skip == 0)
      break;
    if (frame != NULL) {
      frame = (uint32_t *)*frame;
      *(uint32_t **)0x449ef8 = frame;
      if (((uint32_t)frame & 3) != 0 ||
          (uint32_t)frame >= (uint32_t)*(uint32_t **)0x449efc) {
        frame = NULL;
        *(uint32_t **)0x449ef8 = frame;
      }
      *(uint32_t **)0x449efc = frame;
    }
  }

  i = 0;
  if (max != 0) {
    for (;;) {
      return_address = 0;
      if (frame != NULL) {
        return_address = frame[1];
        frame = (uint32_t *)frame[0];
        *(uint32_t **)0x449ef8 = frame;
        if (((uint32_t)frame & 3) != 0 ||
            (uint32_t)frame >= (uint32_t)*(uint32_t **)0x449efc) {
          frame = NULL;
          *(uint32_t **)0x449ef8 = frame;
        }
        *(uint32_t **)0x449efc = frame;
      }
      frames[i] = (int32_t)return_address;
      if (return_address == 0)
        break;
      i++;
      if (i >= max)
        break;
    }
  }
  *count = i;
}

/* -----------------------------------------------------------------------
 * stack_walk_dispose (0x92440) — reset the symbol table to "not loaded" state.
 *
 * The reference is 29 bytes and CALLs symbol_table_dispose (0x92090) with
 * &symtab; MSVC hoists the `PUSH 0x2ee788` above the two global stores and
 * cleans with `POP ECX`.  The previous lift open-coded the three zero stores
 * that symbol_table_dispose ends with and dropped the call, which LEAKED both
 * of the symbol table's heap allocations: 0x92090 debug_free()s symtab[1]
 * (line 0x227) and symtab[2] (line 0x228) when non-NULL before zeroing.
 * Found by the SHAPE-WARN call-count census (ours 0 vs reference 1) -- see
 * docs/lift-learnings.md section 49.
 *
 * Reference instruction order is `push 0x2ee788` FIRST, then the two global
 * stores, then the call.  Reaching the globals through absolute pointer casts
 * blocked that hoist and cost 1 of 6 LCS positions (83.3%); the real data
 * symbols restore it (100.0%).
 *
 * Reference instruction order is `push 0x2ee788` FIRST, then the two global
 * stores, then the call; VC71 emits the push after the stores from this
 * source form, which costs 1 of 6 LCS positions (83.3%).
 * ----------------------------------------------------------------------- */
void stack_walk_dispose(void)
{
  stack_walk_bias = -1;
  stack_walk_load_failed = 0;
  symbol_table_dispose(stack_walk_symbols);
}

/* -----------------------------------------------------------------------
 * stack_walk_with_context (0x92460) — dump stack trace to the error log.
 *
 * Collects up to 0x40 frames via FUN_00092370 (skipping `depth` frames),
 * resolves each return address via the profile.obj lookup helper, and
 * writes them to the error/debug output.
 * ----------------------------------------------------------------------- */
char stack_walk_with_context(int a1, int16_t depth, int a3)
{
  int32_t frames[0x40];
  uint32_t i;
  char *sym;
  int context;
  uint32_t trace_address;
  uint32_t eip_value;
  uint32_t eip_byte_3;
  char result;

  __builtin_memset(frames, 0, sizeof(frames));
  context = a3;

  if (a3 != 0) {
    *(int *)0x449efc = *(int *)((char *)a3 + 0x230);
    *(int *)0x449ef8 = *(int *)((char *)a3 + 0x220);
    FUN_000922a0((int)depth, frames, 0x40, (uint32_t *)&a3);
  } else {
    FUN_00092370((int)depth, frames, 0x40, (uint32_t *)&a3);
  }

  if (a1 == 0) {
    error(2, "Printing stuff for Mat's edification");
    i = (uint32_t)a3 - 1;
    while ((int32_t)i >= (int32_t)depth) {
      trace_address = (uint32_t)frames[i];
      trace_address += *(uint32_t *)(trace_address - 4);
      sym = NULL;
      if (*(int32_t *)0x2ee788 != 0 && *(unsigned char *)0x2ee784 == 0) {
        sym = FUN_00092110(trace_address, (int32_t *)0x2ee788);
      }
      if (sym == NULL)
        sym = "?????";
      error(2, "%08lX %s", trace_address, sym);
      result = 1;
      i--;
    }
  }

  if (context != 0) {
    eip_value = **(uint32_t **)(context + 0x224);
    eip_byte_3 = eip_value >> 24;
    error(2, "EAX: 0x%08lX", *(uint32_t *)(context + 0x21c));
    error(2, "EBX: 0x%08lX", *(uint32_t *)(context + 0x210));
    error(2, "ECX: 0x%08lX", *(uint32_t *)(context + 0x218));
    error(2, "EDX: 0x%08lX", *(uint32_t *)(context + 0x214));
    error(2, "EDI: 0x%08lX", *(uint32_t *)(context + 0x208));
    error(2, "ESI: 0x%08lX", *(uint32_t *)(context + 0x20c));
    error(2, "EBP: 0x%08lX", *(uint32_t *)(context + 0x220));
    error(2, "ESP: 0x%08lX", *(uint32_t *)(context + 0x230));
    sym = NULL;
    if (*(int32_t *)0x2ee788 != 0 && *(unsigned char *)0x2ee784 == 0) {
      sym = FUN_00092110(*(uint32_t *)(context + 0x224),
                         (int32_t *)0x2ee788);
    }
    if (sym == NULL)
      sym = "?????";
    error(2, "EIP: 0x%08lX, %02lX %02lX %02lX %02lX %s",
          *(uint32_t *)(context + 0x224),
          eip_value & 0xff,
          (eip_value >> 8) & 0xff,
          (eip_value >> 16) & 0xff,
          eip_byte_3,
          sym);
    result = 1;
  }

  i = (uint32_t)a3 - 1;
  while ((int32_t)i >= (int32_t)depth) {
    sym = NULL;
    if (*(int32_t *)0x2ee788 != 0 && *(unsigned char *)0x2ee784 == 0) {
      sym = FUN_00092110(frames[i], (int32_t *)0x2ee788);
    }
    if (sym == NULL)
      sym = "?????";
    if (a1 == 0) {
      error(2, "%08lX %s", (unsigned int)frames[i], sym);
      result = 1;
    } else {
      result = (char)crt_fprintf((void *)a1, "%08lX %s\n",
                                 (unsigned int)frames[i], sym);
    }
    i--;
  }

  return result;
}

/* The callback at 0x92060 orders symbol records by their value field.  Its
 * zero-value handling is unusual but is visible in the reference body: a
 * zero first value, a zero second value, or a first value greater than the
 * second all return 1; non-equal non-zero values return -1/1. */
static int compare_symbol_entries(const void *a, const void *b)
{
  uint32_t value_a;
  uint32_t value_b;

  value_a = *(const uint32_t *)((const char *)a + 4);
  if (value_a == 0)
    return 1;

  value_b = *(const uint32_t *)((const char *)b + 4);
  if (value_a > value_b)
    return 1;
  if (value_b != 0) {
    if (value_a < value_b)
      return -1;
    return 0;
  }
  return 1;
}

/* -----------------------------------------------------------------------
 * load_symbol_table (0x92710) — parse an MSVC linker .map file.
 *
 * The parser follows the reference's two-stage header walk ("Lib:Object",
 * timestamp, entry-point line, then "Static symbols") and stores each record
 * in the debug-reallocated 0x10-byte entry array.  The name and library
 * strings share the reference's growable storage pool; duplicate library
 * names reuse the previous pool offset.
 * ----------------------------------------------------------------------- */
int load_symbol_table(const char *map_path, int32_t *symtab_out,
                      const char *build_timestamp)
{
  typedef unsigned long (*strtoul_proc)(const char *, char **, int);
  void *f;
  char line[0x4000] = { 0 };
  char last_object[0x100];
  char object_name[0x100];
  char symbol_name[0x100];
  char *token;
  char *name_pool;
  int32_t *entries;
  int32_t *entry;
  int32_t *symtab;
  int entry_capacity;
  int string_storage_size;
  int string_storage_used;
  int name_length;
  int object_length;
  int last_object_offset;
  int value;
  int rva;

  line[0] = '\0';
  symtab = symtab_out;
  if (symtab == NULL) {
    display_assert("symbol_table",
                   "c:\\halo\\SOURCE\\cseries\\stack_walk_windows.c",
                   0x100, 1);
    system_exit(-1);
  }
  csmemset(symtab, 0, 0xc);
  f = crt_fopen(map_path, "r");
  if (f == NULL) {
    error(2, "Couldn't read map file '%s'", map_path);
    return 0;
  }

  if (crt_fgets(line, 0x4000, f) == NULL)
    goto finish;

  while (1) {
    if (crt_fgets(line, 0x4000, f) == NULL) {
      error(2, "map file appears corrupt");
      goto finish;
    }
    if (crt_strstr(line, "Lib:Object") != NULL)
      break;
    if (crt_strstr(line, "Timestamp") != NULL)
      (void)crt_strstr(line, build_timestamp);
  }

  name_pool = (char *)symtab[1];
  entries = (int32_t *)symtab[2];
  entry_capacity = 0;
  string_storage_size = 0;
  string_storage_used = 0;
  last_object_offset = -1;
  csstrcpy(last_object, "nothing");

  if (crt_fgets(line, 0x4000, f) == NULL)
    goto finish;

parse_line:
  token = csstrtok(line, ":");
  if (token == NULL || *token != ' ')
    goto corrupt;

  token = csstrtok(NULL, " \t\n\r");
  if (token == NULL)
    goto corrupt;
  value = (int)((strtoul_proc)strtoul)(
    token, (char **)&symtab_out, 16);

  token = csstrtok(NULL, " \t\n\r");
  if (token != NULL) {
    csstrncpy(symbol_name, token, 0xff);
    symbol_name[0xff] = '\0';
  } else {
    if (crt_strstr(line, "entry point at") == NULL)
      goto corrupt;
    (void)crt_fgets(line, 0x4000, f);
    if (!crt_isspace((int)line[0]))
      goto corrupt;
    (void)crt_fgets(line, 0x4000, f);
    if (crt_strstr(line, "Static symbols") == NULL)
      goto corrupt;
    (void)crt_fgets(line, 0x4000, f);
    if (!crt_isspace((int)line[0]))
      goto corrupt;
    (void)crt_fgets(line, 0x4000, f);
    token = csstrtok(line, ":");
    if (token == NULL || *token != ' ')
      goto corrupt;
    token = csstrtok(NULL, " \t\n\r");
    if (token == NULL)
      goto corrupt;
    value = (int)((strtoul_proc)strtoul)(
      token, (char **)&symtab_out, 16);
    token = csstrtok(NULL, " \t\n\r");
    if (token != NULL) {
      csstrncpy(symbol_name, token, 0xff);
      symbol_name[0xff] = '\0';
    }
  }

  token = csstrtok(NULL, " \t\n\r");
  if (token == NULL)
    goto corrupt;
  rva = (int)((strtoul_proc)strtoul)(
    token, (char **)&symtab_out, 16);
  if (csstrcmp(symbol_name, "_load_symbol_table") == 0)
    stack_walk_bias = rva - 0x92710;
  if (symtab_out == NULL)
    goto corrupt;

  token = csstrtok((char *)symtab_out + 5, " \t\n\r");
  if (token == NULL)
    goto corrupt;
  csstrncpy(object_name, token, 0xff);
  object_name[0xff] = '\0';

  if (symtab[0] >= entry_capacity) {
    entry_capacity += 0x1000;
    entries = (int32_t *)debug_realloc(
      entries, entry_capacity * 0x10,
      "c:\\halo\\SOURCE\\cseries\\stack_walk_windows.c", 0x1c6);
    if (entries == NULL)
      goto allocation_failure;
    symtab[2] = (int32_t)entries;
  }

  object_length = csstrlen(object_name);
  name_length = csstrlen(symbol_name);
  if (string_storage_used + object_length + name_length + 2 >=
      string_storage_size) {
    string_storage_size += 0x4000;
    name_pool = (char *)debug_realloc(
      name_pool, string_storage_size,
      "c:\\halo\\SOURCE\\cseries\\stack_walk_windows.c", 0x1d7);
    if (name_pool == NULL)
      goto allocation_failure;
    symtab[1] = (int32_t)name_pool;
  }
  if (!(string_storage_used + object_length + name_length + 2 <
        string_storage_size)) {
    display_assert(
      "string_storage_used + strlen(symbol_name) + 1 + strlen(library_object_file_name) + 1 < string_storage_size",
      "c:\\halo\\SOURCE\\cseries\\stack_walk_windows.c", 0x1e2, 1);
    system_exit(-1);
  }

  entry = entries + symtab[0] * 4;
  symtab[0] = symtab[0] + 1;
  entry[0] = value;
  entry[1] = rva;
  csstrcpy(name_pool + string_storage_used, symbol_name);
  entry[2] = string_storage_used;
  name_length = csstrlen(symbol_name);
  string_storage_used += name_length + 1;

  if (csstrcmp(object_name, last_object) == 0) {
    entry[3] = last_object_offset;
  } else {
    object_length = csstrlen(object_name);
    if (!(string_storage_used + object_length + 1 < string_storage_size)) {
      display_assert(
        "string_storage_used + strlen(library_object_file_name) + 1 < string_storage_size",
        "c:\\halo\\SOURCE\\cseries\\stack_walk_windows.c", 0x1f5, 1);
      system_exit(-1);
    }
    entry[3] = string_storage_used;
    csstrcpy(name_pool + string_storage_used, object_name);
    last_object_offset = entry[3];
    object_length = csstrlen(object_name);
    string_storage_used += object_length + 1;
  }
  csstrcpy(last_object, object_name);

  if (crt_fgets(line, 0x4000, f) != NULL)
    goto parse_line;

finish:
  crt_fclose(f);
  if (symtab[0] > 0) {
    qsort(entries, (size_t)symtab[0], 0x10, compare_symbol_entries);
    entry = entries + (symtab[0] - 1) * 4;
    if (entry[3] == 0) {
      do {
        symtab[0] = symtab[0] - 1;
        entry = entries + (symtab[0] - 1) * 4;
      } while (entry[3] == 0);
    }
  }
  return symtab[0] > 0;

allocation_failure:
  error(2, "could not allocate enough memory for map file");
  symbol_table_dispose(symtab);
  goto finish;
corrupt:
  error(2, "map file appears corrupt");
  symbol_table_dispose(symtab);
  goto finish;
}

/* -----------------------------------------------------------------------
 * stack_walk_initialize (0x92d30) — initialize the stack walker.
 *
 * Loads the linker map from the hardcoded path, then normalizes the "not
 * loaded" sentinel: if the bias global is still -1 after the load attempt it
 * is forced to 0 (symbol resolution disabled but not treated as an error).
 *
 * The reference discards load_symbol_table's return value and never calls
 * stack_walk_dispose or sets g_sw_failed -- an earlier lift did both, which
 * cost 4 of 10 LCS positions.  Verified against the 10-insn reference at
 * 0x92d30..0x92d5c; the three pushes are "d:\\cachebeta.map" (0x268e1c),
 * &g_sw_symtab (0x2ee788) and the build timestamp (0x268e30).
 * Change "d:\\cachebeta.map" to load from HDD or a custom location.
 * ----------------------------------------------------------------------- */
void stack_walk_initialize(void)
{
  load_symbol_table("d:\\cachebeta.map", stack_walk_symbols,
                    "Thu Aug 23 16:11:48 2001");
  if (stack_walk_bias == -1)
    stack_walk_bias = 0;
}

/* Invoke the stack trace logger. The depth parameter is incremented
 * by 1 to skip this wrapper's own frame. */
char stack_walk(int16_t a1)
{
  return stack_walk_with_context(0, (int)a1 + 1, 0);
}
