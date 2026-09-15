/* 0x1b9930 — tag_loaded: linear search for a loaded tag by group/name.
 * Returns tag index from tag instance +0x0c on match; otherwise -1.
 * Requires cache tags to be available (byte flag at 0x4e4d00). If the
 * global tag-instance table pointer (0x5054f0) is NULL while tags are
 * enabled, asserts and exits. Comparison uses case-insensitive CRT
 * string compare (__stricmp). */
int tag_loaded(int group_tag, const char *name, ...)
{
  int tag_count;
  int *entry;
  short index;

  if (*(uint8_t *)0x4e4d00 == 0) {
    return -1;
  }

  if (*(int **)0x5054f0 == 0) {
    display_assert("global_tag_instances",
                   "c:\\halo\\SOURCE\\cache\\cache_files.c", 0x127, true);
    system_exit(-1);
  }

  tag_count = *(int *)(*(int *)0x4e5504 + 0xc);
  if (tag_count <= 0) {
    return -1;
  }

  index = 0;
  do {
    entry = (int *)((int)*(int **)0x5054f0 + ((int)index << 5));
    if (entry[0] == group_tag &&
        crt_stricmp(name, (const char *)entry[4]) == 0) {
      return entry[3];
    }

    index = (short)(index + 1);
  } while ((int)index < tag_count);

  return -1;
}

/* 0x1b9fa0 — FUN_001b9fa0: load the structure_bsp block described by a
 * scenario structure-bsp reference element and hand its geometry to D3D.
 *
 * Sole caller is scenario_switch_structure_bsp (xref 0018ec27,
 * unconditional call); returns true unconditionally (MOV AL,0x1 in the
 * epilogue).
 *
 * The element fields used here are the same record FUN_001ba0c0 disposes:
 *   +0x00  cache-file offset of the block
 *   +0x04  size in bytes
 *   +0x08  destination address, which is also the structure_bsp_header
 *          pointer stored into cache_file_globals.structure_bsp_header
 *          (the global at 0x4e5508, named by the line-0xad assert)
 *   +0x1c  tag index of the structure_bsp tag
 *
 * Before the read, the tail of the 22 MiB tag buffer is scrubbed with the
 * 0xcd uninitialized-fill pattern: the fill starts at
 * FUN_001bdd50() + cache_file_tag_data_size (ADD ECX,EAX after loading
 * [0x4e4d18]) and covers 0x1600000 - cache_file_tag_data_size bytes, i.e.
 * everything past the map's tag block. Both uses come from one load of
 * [0x4e4d18] (MOV ECX,[0x4e4d18] at 001b9faa).
 *
 * The read is asynchronous (async_flag 1) and its return is discarded;
 * completion is awaited by spinning on a byte out-param. The original
 * never initializes that byte (no store to [EBP+0xb] before the call) —
 * cache_file_read owns it. MSVC homed the byte in the dead parameter slot
 * (LEA EAX,[EBP+0xb], the high byte of the incoming pointer, which stays
 * live in ESI), which is why Ghidra renders it as in_stack_00000004._3_1_.
 * Unlike the plain SwitchToThread spin in FUN_001b9e70, this one pumps the
 * sound mixer whenever a single wait iteration exceeded 0x21 ms
 * (SUB EAX,EDI / CMP EAX,0x21 / JBE — unsigned).
 *
 * The final store re-reads the global rather than reusing the header
 * pointer (MOV ECX,[0x4e5508] / MOV EDX,[ECX] at 001ba0ac). */
bool FUN_001b9fa0(void *element)
{
  int tags_base;
  unsigned int tag_data_size;
  char completion_flag;
  int *header;
  int *entry;
  unsigned int render_time;

  tags_base = FUN_001bdd50();
  tag_data_size = *(unsigned int *)0x4e4d18;
  csmemset((void *)(tag_data_size + tags_base), 0xcd,
           0x1600000 - tag_data_size);

  cache_file_read(-1, *(int *)element, *(unsigned int *)((char *)element + 4),
                  *(int *)((char *)element + 8), &completion_flag, 1);

  while (completion_flag == 0) {
    SwitchToThread();
    render_time = sound_render_time();
    if (system_milliseconds() - render_time > 0x21u) {
      sound_idle();
    }
  }

  header = *(int **)((char *)element + 8);
  *(int **)0x4e5508 = header;
  if (header[5] != 0x73627370) {
    display_assert("cache_file_globals.structure_bsp_header->signature=="
                   "CACHE_FILE_STRUCTURE_BSP_HEADER_SIGNATURE",
                   "c:\\halo\\SOURCE\\cache\\cache_files.c", 0xad, true);
    system_exit(-1);
  }

  structure_bsp_header_register_vertex_buffers(header);

  entry = tag_instance_resolve(*(int *)((char *)element + 0x1c));
  if (entry[5] != 0) {
    display_assert("!tag_instance->base_address",
                   "c:\\halo\\SOURCE\\cache\\cache_files.c", 0xb7, true);
    system_exit(-1);
  }
  if (entry[0] != 0x73627370) {
    display_assert("tag_instance->group_tag==STRUCTURE_BSP_TAG",
                   "c:\\halo\\SOURCE\\cache\\cache_files.c", 0xb8, true);
    system_exit(-1);
  }

  entry[5] = **(int **)0x4e5508;
  return true;
}

/* 0x1ba0c0 — FUN_001ba0c0: dispose the currently-loaded structure_bsp tag's
 * geometry. Deregisters (waits idle on) the D3D vertex/index buffers for the
 * block pointer held in the global at 0x4e5508, resolves the tag instance
 * for the tag index at element+0x1c via tag_instance_resolve (0x1b9bf0),
 * asserts the instance has a base address and that its group tag is
 * STRUCTURE_BSP_TAG ('sbsp', 0x73627370 — same literal used for this group
 * in scenario.c), then clears the instance's base address (+0x14) and the
 * global block pointer. */
void FUN_001ba0c0(void *element)
{
  int *entry;

  structure_bsp_header_deregister_vertex_buffers(*(void **)0x4e5508);

  entry = tag_instance_resolve(*(int *)((char *)element + 0x1c));

  if (entry[5] == 0) {
    display_assert("tag_instance->base_address",
                   "c:\\halo\\SOURCE\\cache\\cache_files.c", 0xcd, true);
    system_exit(-1);
  }
  if (entry[0] != 0x73627370) {
    display_assert("tag_instance->group_tag==STRUCTURE_BSP_TAG",
                   "c:\\halo\\SOURCE\\cache\\cache_files.c", 0xce, true);
    system_exit(-1);
  }

  entry[5] = 0;
  *(void **)0x4e5508 = 0;
}

/* 0x1ba140 — tag_get: resolve a tag handle and return its base/data
 * pointer. Calls 0x1b9bf0 (tag_instance_resolve) with the 16-bit tag
 * index in EDI (hidden register param); that helper returns a pointer
 * to the tag instance record. The record stores the tag's own group
 * at +0, parent group at +4, grandparent group at +8, and data pointer
 * at +0x14. The group check accepts a match at any of the three levels
 * (supports parent-group lookups). Asserts if the group doesn't match
 * or if the data pointer is NULL. */
void *tag_get(int group_tag, int tag_index)
{
  int *entry;

  entry = tag_instance_resolve(tag_index);

  if (entry[0] != group_tag && entry[1] != group_tag && entry[2] != group_tag) {
    error(2, "expected tag group %08x but got %08x for datum %08x", group_tag,
          entry[0], tag_index);
    display_assert("expected tag group mismatch",
                   "c:\\halo\\SOURCE\\cache\\cache_files.c", 0xf7, true);
    system_exit(-1);
  }
  if (entry[5] == 0) {
    display_assert("can't get() a tag with a base address!",
                   "c:\\halo\\SOURCE\\cache\\cache_files.c", 0xfb, true);
    system_exit(-1);
  }
  return (void *)entry[5];
}

/* 0x1ba1f0 — tag_get_name: return the name string for a tag by index.
 * Calls tag_instance_resolve (0x1b9bf0) with the tag index in EDI,
 * then reads the name pointer at offset +0x10 of the tag instance record. */
const char *tag_get_name(int tag_index)
{
  int *entry;

  entry = tag_instance_resolve(tag_index);
  return (const char *)entry[4];
}

/* 0x1ba210 — tag_get_group_tag: return the primary group tag (4CC class
 * identifier) for a tag by index. Calls tag_instance_resolve (0x1b9bf0)
 * with the tag index in EDI, then reads the group tag at offset +0x00
 * of the tag instance record. */
int tag_get_group_tag(int tag_index)
{
  int *entry;

  entry = tag_instance_resolve(tag_index);
  return entry[0];
}

/* 0x1ba250 — FUN_001ba250: record a boolean in the cache globals block and
 * set the cache-copy worker thread's priority from it.
 *
 * Disassembly (0x1ba250..0x1ba28f, 22 instructions) takes one stack byte
 * argument at [EBP+0x8]: MOV AL,[EBP+8] / TEST AL,AL / MOV ECX,[0x0032ea98]
 * / MOV [ECX+0x988],AL / JZ. Both arms then load the same dword and call
 * SetThreadPriority (0x1cf999, __stdcall): PUSH 0x1 / PUSH [ECX+0x95c] on
 * the non-zero arm, PUSH 0x0 / PUSH [ECX+0x95c] on the zero arm. The globals
 * pointer is loaded ONCE into ECX and reused by both arms, which the single
 * local below reproduces.
 *
 *   +0x95c  thread handle passed to SetThreadPriority. Written by
 *           FUN_001bc280 (cache_files_windows.c) as the copy-worker thread
 *           handle, which is where the "cache-copy worker" role comes from.
 *   +0x988  byte written unconditionally with the argument itself, before
 *           the branch. Meaning beyond "mirrors this argument" is unproven.
 *
 * Both arms end with MOV EAX,0x512000 / POP EBP / RET, so the function
 * returns a constant address. 0x512000 is outside the code range and is not
 * a named symbol in kb.json, so its meaning is UNKNOWN; it is reproduced as
 * an opaque pointer constant purely to preserve the returned value. */
void *FUN_001ba250(char raise_priority)
{
  unsigned char *globals;

  globals = *(unsigned char **)0x32ea98;
  globals[0x988] = raise_priority;
  if (raise_priority != 0) {
    SetThreadPriority(*(int *)(globals + 0x95c), 1);
  } else {
    SetThreadPriority(*(int *)(globals + 0x95c), 0);
  }
  return (void *)0x512000;
}

/* 0x1ba290 — FUN_001ba290: same shape as FUN_001ba250 above, minus the
 * returned constant.
 *
 * Disassembly (0x1ba290..0x1ba2c5, 18 instructions) takes one stack byte
 * argument at [EBP+0x8]: MOV AL,[EBP+8] / TEST AL,AL / MOV ECX,[0x0032ea98]
 * / MOV [ECX+0x988],AL / JZ. Both arms load the same dword from the globals
 * block and call SetThreadPriority (0x1cf999, __stdcall): PUSH 0x1 /
 * PUSH [ECX+0x95c] on the non-zero arm, PUSH 0x0 / PUSH [ECX+0x95c] on the
 * zero arm. The globals pointer is loaded ONCE into ECX and reused by both
 * arms, which the single local below reproduces. Both arms end POP EBP / RET
 * with no MOV EAX, so the function returns nothing.
 *
 *   +0x95c  thread handle passed to SetThreadPriority (written by
 *           FUN_001bc280 in cache_files_windows.c as the copy-worker
 *           thread handle).
 *   +0x988  byte written unconditionally with the argument itself, before
 *           the branch. Meaning beyond "mirrors this argument" is unproven.
 *
 * There are no xrefs to this function in the artifact, so the caller-side
 * meaning of the argument is UNKNOWN; the name follows the identical
 * priority selection (1 vs 0) at the two call sites. */
void FUN_001ba290(char raise_priority)
{
  unsigned char *globals;

  globals = *(unsigned char **)0x32ea98;
  globals[0x988] = raise_priority;
  if (raise_priority != 0) {
    SetThreadPriority(*(int *)(globals + 0x95c), 1);
  } else {
    SetThreadPriority(*(int *)(globals + 0x95c), 0);
  }
}

/* 0x1ba2f0 — FUN_001ba2f0: arm the cache-copy worker for one copy session:
 * validate the request, publish its parameters into the cache globals block,
 * clear the per-session state and signal copy_start_event.
 *
 * Assert TU is cache_files_decompress_windows.c (same block as the zlib
 * allocator pair below), lines 0x20c..0x234; the strings are reproduced
 * verbatim because __FILE__/__LINE__ are match-visible.
 *
 * Globals block (reached through the POINTER global at 0x32ea98, built by
 * FUN_001bc280). Every offset below is named by its own assert string except
 * the four stores, which are UNPROVEN beyond "this parameter is published
 * here":
 *   +0x000  destination of csstrcpy(globals, source_file_name) -- the source
 *           file-name character buffer at the head of the block
 *           (0x1ba529 PUSH EDX / 0x1ba537 PUSH EAX / CALL 0x8dff0).
 *   +0x104  0x800 bytes zeroed by csmemset (0x1ba57e PUSH 0x800 /
 *           0x1ba583 ADD EAX,0x104 / 0x1ba588 PUSH 0x0).
 *   +0x904  cleared to 0 before the copy starts; meaning unproven.
 *   +0x928  zlib_stream.zalloc   (asserted non-null, line 0x219)
 *   +0x92c  zlib_stream.zfree    (asserted non-null, line 0x21a)
 *   +0x94c  copy_start_event     (SetEvent, last side effect)
 *   +0x950  copy_stop_event      (ResetEvent)
 *   +0x954  copy_complete_event  (polled with a zero timeout at entry, then
 *                                 ResetEvent)
 *   +0x958  progress_update_event
 *   +0x95c  copy_thread
 *   +0x960  written with the caller's buffer
 *   +0x98c  written with destination_file
 *   +0xaa0  cleared to 0; meaning unproven.
 *
 * The entry poll is WaitForSingleObject(copy_complete_event, 0): PUSH 0x0 is
 * the timeout and PUSH ECX the handle (0x1ba2fe..0x1ba301), so any result
 * other than WAIT_OBJECT_0 (== 0) means the previous session is still
 * running. That assert block lives at the tail of the function in the
 * original (0x1ba5a9) -- a cold-block placement, not a different order.
 *
 * `size` and `destination_file_size` are used ONLY by their asserts; neither
 * is stored. The size floor 0x512000 is the TOTAL_BUFFER_SIZE named by the
 * assert string.
 *
 * Globals-pointer loads follow the original exactly: one load for the entry
 * poll (0x1ba2f3), one load at 0x1ba3db kept live in EAX across every field
 * assert and the three stores around csstrcpy, then a fresh reload before
 * each of the remaining four call sites (0x1ba543 / 0x1ba567 / 0x1ba579 /
 * 0x1ba590).
 *
 * The dest_file assert exits with the handle register itself (0x1ba355 PUSH
 * EDI, where EDI is destination_file and the branch already proved it -1),
 * not with a fresh -1 immediate like the other assert arms. */
void FUN_001ba2f0(int buffer, int size, int dest_file, int dest_file_size,
                  const char *source_file_name)
{
  unsigned char *globals;

  if (WaitForSingleObject(*(int *)(*(unsigned char **)0x32ea98 + 0x954), 0) !=
      0) {
    display_assert("previous copy session did not complete",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x234, true);
    system_exit(-1);
  }

  if (source_file_name == (const char *)0) {
    display_assert("source_file_name",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x20c, true);
    system_exit(-1);
  }

  if (dest_file == -1) {
    display_assert("destination_file!=INVALID_HANDLE_VALUE",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x20d, true);
    system_exit(dest_file);
  }

  if (buffer == 0) {
    display_assert("buffer",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x20e, true);
    system_exit(-1);
  }

  if (size < 0x512000) {
    display_assert("size>= TOTAL_BUFFER_SIZE",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x20f, true);
    system_exit(-1);
  }

  if (dest_file_size != (int)GetFileSize(dest_file, (unsigned int *)0)) {
    display_assert("destination_file_size==GetFileSize(destination_file, NULL)",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x210, true);
    system_exit(-1);
  }

  globals = *(unsigned char **)0x32ea98;

  if (*(int *)(globals + 0x954) == 0) {
    display_assert("global_self->copy_complete_event",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x212, true);
    system_exit(-1);
  }

  if (*(int *)(globals + 0x950) == 0) {
    display_assert("global_self->copy_stop_event",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x213, true);
    system_exit(-1);
  }

  if (*(int *)(globals + 0x94c) == 0) {
    display_assert("global_self->copy_start_event",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x214, true);
    system_exit(-1);
  }

  if (*(int *)(globals + 0x958) == 0) {
    display_assert("global_self->progress_update_event",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x215, true);
    system_exit(-1);
  }

  if (*(int *)(globals + 0x95c) == 0) {
    display_assert("global_self->copy_thread",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x217, true);
    system_exit(-1);
  }

  if (*(int *)(globals + 0x928) == 0) {
    display_assert("global_self->zlib_stream.zalloc",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x219, true);
    system_exit(-1);
  }

  if (*(int *)(globals + 0x92c) == 0) {
    display_assert("global_self->zlib_stream.zfree",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x21a, true);
    system_exit(-1);
  }

  *(int *)(globals + 0x904) = 0;
  *(int *)(globals + 0x960) = buffer;
  csstrcpy((char *)globals, source_file_name);

  globals = *(unsigned char **)0x32ea98;
  *(int *)(globals + 0x98c) = dest_file;
  *(int *)(globals + 0xaa0) = 0;
  ResetEvent(*(void **)(globals + 0x954));

  ResetEvent(*(void **)(*(unsigned char **)0x32ea98 + 0x950));

  csmemset(*(unsigned char **)0x32ea98 + 0x104, 0, 0x800);

  SetEvent(*(void **)(*(unsigned char **)0x32ea98 + 0x94c));
}

/* 0x1ba5d0 — FUN_001ba5d0: if the cache-copy worker is currently busy,
 * signal the "queue end" event.
 *
 * Sole caller is cache_files_precache_map_queue_end (xref 0x1bc719,
 * unconditional call), which owns the copy_in_progress assert; this
 * function itself has no assert and no stack frame (disassembly starts
 * at MOV EAX,[0x0032ea98] and ends at RET, 12 instructions total).
 *
 * The globals block is reached through the POINTER global at 0x32ea98,
 * the same block written by FUN_001bc280 in cache_files_windows.c. The
 * original reloads that pointer after the first call (MOV EAX,[0x32ea98]
 * at 0x1ba5d0 and MOV EDX,[0x32ea98] at 0x1ba5e7), which the repeated
 * deref below reproduces.
 *
 *   +0x954  event polled with a zero timeout (WaitForSingleObject with
 *           PUSH 0x0 as the timeout, PUSH ECX as the handle). Created by
 *           FUN_001bc280 as manual-reset, initially SIGNALED -- inferred
 *           role "worker idle" from those CreateEventA arguments, not
 *           from an assert string.
 *   +0x950  event signalled when the poll returns non-zero, i.e. when
 *           +0x954 was NOT signalled (WAIT_OBJECT_0 == 0 is the only
 *           value skipped by TEST EAX,EAX / JZ). Created by FUN_001bc280
 *           as manual-reset, initially non-signaled.
 *
 * Type asymmetry between the two calls follows the kb decls:
 * WaitForSingleObject takes an int handle, SetEvent takes void *. */
void FUN_001ba5d0(void)
{
  if (WaitForSingleObject(*(int *)(*(unsigned char **)0x32ea98 + 0x954), 0) !=
      0) {
    SetEvent(*(void **)(*(unsigned char **)0x32ea98 + 0x950));
  }
}

/* 0x1ba660 - cache_copy_compressed_alloc: zlib allocator over the cache
 * globals' fixed decompression scratch buffer (a bump allocator).
 *
 * Installed as a DATA reference at 0x1bc333 inside FUN_001bc280 (the same
 * function that builds the globals block reached through the POINTER
 * global at 0x32ea98), i.e. it is a zlib alloc_func slot and is never
 * reached by a direct CALL -- callers[] is empty and xrefs_to reports
 * only that one [DATA] reference.
 *
 * Signature is read off the frame, not off a call site: the body reads
 * [EBP+0xc] and [EBP+0x10] (item count and item size, multiplied with a
 * signed IMUL) and leaves the PREVIOUS next_allocation in EAX, while
 * [EBP+0x8] is never read -- the unused first slot is zlib's opaque
 * cookie. Marked void * because the returned value is a buffer pointer;
 * the field arithmetic itself is done in int to keep the original's
 * signed SUB/CMP/JL shape.
 *
 * Globals block offsets, all named by the assert string:
 *   +0x940  zlib_buffer       (base of the scratch buffer)
 *   +0x944  zlib_buffer_size  (its length)
 *   +0x948  next_allocation   (bump cursor)
 *
 * The original loads the globals POINTER once (MOV ECX,[0x0032ea98]) and
 * reuses ECX for all four field accesses, so a single local holds it here
 * -- unlike FUN_001ba5d0 above, which genuinely reloads it.
 *
 * The assert compares the POST-bump cursor (EAX still holds the stored
 * value when SUB/CMP run), and the TU stamped in the assert string is
 * cache_files_decompress_windows.c even though the kb.json object mapping
 * places this address in tags.obj -- the string is reproduced verbatim
 * because __FILE__/__LINE__ are match-visible. */
void *cache_copy_compressed_alloc(void *opaque, int items, int size)
{
  unsigned char *globals;
  int previous;
  int next;
  int *zlib_buffer;

  globals = *(unsigned char **)0x32ea98;
  previous = *(int *)(globals + 0x948);
  next = items * size + previous;
  *(int *)(globals + 0x948) = next;
  zlib_buffer = (int *)(globals + 0x940);

  if (next - *zlib_buffer >= *(int *)(globals + 0x944)) {
    display_assert("global_self->next_allocation-global_self->zlib_buffer<"
                   "global_self->zlib_buffer_size",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x2bd, true);
    system_exit(-1);
  }

  return (void *)previous;
}

/* 0x1ba6c0 - cache_copy_compressed_free: the zlib free_func counterpart to
 * cache_copy_compressed_alloc above; it rewinds the bump cursor to the
 * released address.
 *
 * Like the allocator, this is never reached by a direct CALL -- callers[]
 * is empty and the only xref is the [DATA] reference at 0x1bc33f inside
 * FUN_001bc280, which stores it into the cache globals block at +0x92c,
 * the slot immediately after the alloc_func slot at +0x928. That pairing
 * plus the frame shape is what types the signature: the body reads only
 * [EBP+0xc] (MOV ESI,[EBP+0xc]) and never touches [EBP+0x8], so the
 * unused first slot is zlib's opaque cookie and the second is the address
 * being freed -- the name the assert string itself uses.
 *
 * The globals POINTER at 0x32ea98 is loaded ONCE (MOV EAX,[0x0032ea98] at
 * 0x1ba6c3) and EAX is still live for the store at 0x1ba6fb, so a single
 * local holds it here rather than a repeated deref.
 *
 * The compare is UNSIGNED (CMP ESI,ECX / JBE at 0x1ba6d2), unlike the
 * allocator's signed SUB/CMP/JL, so the cursor is read as unsigned int on
 * this path.
 *
 *   +0x948  next_allocation (bump cursor), named by the assert string.
 *
 * The assert TU is cache_files_decompress_windows.c even though kb.json's
 * object mapping places this address in tags.obj -- reproduced verbatim
 * because __FILE__/__LINE__ are match-visible. */
void cache_copy_compressed_free(void *opaque, void *address)
{
  unsigned char *globals;
  void *held_address;

  (void)opaque;

  globals = *(unsigned char **)0x32ea98;
  /* The address is latched into its own local and used as the LEFT compare
   * operand to reproduce the original's MOV ESI,[EBP+0xc] / CMP ESI,ECX /
   * JBE shape (the value stays live in a callee-saved register across the
   * cold arm and is reused by the store). Same unsigned comparison, just
   * written from the other side. */
  held_address = address;
  if ((unsigned int)held_address > *(unsigned int *)(globals + 0x948)) {
    display_assert("(byte*)address<=global_self->next_allocation",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x2c6, true);
    system_exit(-1);
  }

  *(unsigned int *)(globals + 0x948) = (unsigned int)held_address;
}

/* 0x1ba710 — lay out the eight 128KB decompression buffers, poison the whole
 * 5MB region with 0xfd, and reset the write bookkeeping.
 *
 * self arrives implicitly in EAX (0x1ba718 MOV ESI,EAX with no prior write to
 * EAX), so kb.json carries `char *self@<eax>`. Sole caller is
 * simple_cache_copy_thread (unconditional call at 0x1bbefe), not yet ported.
 *
 * The buffer-pointer table is written through the globals POINTER at
 * 0x32ea98 (0x1ba712 MOV EDI,[0x0032ea98], then LEA ECX,[EDI+0x964]) while
 * the base address is read from self+0x960 — two different bases in the
 * original, reproduced verbatim rather than folded together.
 *
 *   globals+0x964..+0x980  eight buffer pointers, base + i*0x20000
 *   globals+0x984          one past the last buffer (base + 8*0x20000)
 *   self+0x960             base address of the 0x512000-byte region
 *   self+0x944             0x12000 — an IMMEDIATE dword store
 *                          (0x1ba792 MOV dword ptr [ESI+0x944],0x12000);
 *                          Ghidra renders it as the code pointer
 *                          FUN_00012000, which is an address-as-value
 *                          artifact, not a function pointer.
 *   self+0x940 / +0x948    both set to base + 0x500000 (0x1ba782 MOV EAX,
 *                          [ESI+0x960] / ADD EAX,0x500000, one computation
 *                          reused by both stores)
 *   self+0x990             0x1c0 bytes poisoned with 0xfa
 *
 * physical_memory_protect is __stdcall (no ADD ESP after CALL 0x001d371d);
 * pushes are 4, 0x512000, base -> (base, 0x512000, 4) and 2, 0x500000, base
 * -> (base, 0x500000, 2). Both csmemset calls are 3-push cdecl, each cleaned
 * by its own ADD ESP,0xc. Each of the four calls re-reads self+0x960 from
 * memory in the original; the reloads are reproduced.
 */
void FUN_001ba710(char *self)
{
  unsigned char *globals;
  unsigned int buffer_base;
  int *slot;
  int remaining;
  unsigned int region_end;

  globals = *(unsigned char **)0x32ea98;
  buffer_base = *(unsigned int *)(self + 0x960);
  slot = (int *)(globals + 0x964);
  remaining = 8;
  do {
    *slot = (int)buffer_base;
    buffer_base += 0x20000;
    slot++;
    remaining--;
  } while (remaining != 0);
  *(unsigned int *)(globals + 0x984) = buffer_base;

  physical_memory_protect(*(void **)(self + 0x960), 0x512000, 4);
  csmemset(*(void **)(self + 0x960), 0xfd, 0x500000);
  physical_memory_protect(*(void **)(self + 0x960), 0x500000, 2);

  region_end = *(unsigned int *)(self + 0x960) + 0x500000;
  *(int *)(self + 0x944) = 0x12000;
  *(unsigned int *)(self + 0x940) = region_end;
  *(unsigned int *)(self + 0x948) = region_end;

  csmemset(self + 0x990, 0xfa, 0x1c0);
}

/* 0x1ba7c0 — cache_copy_initialize_and_fill_with_garbage: open the source
 * cache file, latch its size into the three remaining-bytes counters, clear
 * the decompression header/state block, and poison the read-buffer bookkeeping
 * fields with -1 ("garbage").
 *
 * self arrives implicitly in ESI: the first instruction sequence pushes ESI
 * directly as CreateFileA's lpFileName (0x1ba7d2 PUSH ESI / 0x1ba7d3 CALL
 * 0x001d1d85) with no preceding write to ESI, so self points at the
 * per-file decompression state block whose first member is the file-name
 * character buffer. Same block as FUN_001ba8b0/FUN_001ba930 below (+0x994
 * overlapped_in_use_flags). Sole caller is simple_cache_copy_thread
 * (xref 0x1bbf03, unconditional call), which is not yet ported.
 *
 * CreateFileA args come off the stack in reverse push order (0x1ba7c3..d2):
 *   lpFileName=self, dwDesiredAccess=0x80000000 (GENERIC_READ),
 *   dwShareMode=0, lpSecurityAttributes=0, dwCreationDisposition=3
 *   (OPEN_EXISTING), dwFlagsAndAttributes=0x60000000, hTemplateFile=0.
 * EDI is zeroed once at 0x1ba7c1 and reused as every 0 immediate; that is a
 * register-allocation detail, not a distinct value.
 *
 *   +0x990  source file handle
 *   +0xa8c / +0xa90 / +0xa94  all three set to the file size; +0xa94 is
 *           "read_bytes_left" per the assert string below
 *   +0x99c  0xdc-byte header/state block, zeroed
 *   +0x994 / +0x998  4-byte flag words, zeroed via csmemset (two separate
 *           3-push cdecl calls, cleaned together by ADD ESP,0x18 at
 *           0x1ba87b -- reproduced as two calls, not one 8-byte memset)
 *
 * The size compare re-reads the field (0x1ba809 MOV EAX,[ESI+0xa94]) rather
 * than reusing GetFileSize's return, and is UNSIGNED (CMP EAX,0x800 / JNC),
 * so the assert fires when fewer than 0x800 bytes are available.
 *
 * The -1 / 0 stores are dword and word writes in the disassembly
 * (MOV dword ptr [ESI+0xa78],EAX with EAX=OR EAX,0xffffffff; MOV word ptr
 * [ESI+0xa88],0xffff; MOV word ptr [ESI+0xab8],DI with DI=0) -- Ghidra's
 * decompile split them into per-byte stores, which is a decompiler artifact.
 *
 * Source: c:\halo\SOURCE\cache\cache_files_decompress_windows.c, assert at
 * line 0x3c4. The assert TU differs from kb.json's tags.obj mapping, same as
 * the neighbouring functions in this file; reproduced verbatim because
 * __FILE__/__LINE__ are match-visible.
 */
void cache_copy_initialize_and_fill_with_garbage(char *self)
{
  int file_handle;
  unsigned int file_size;

  file_handle = CreateFileA(self, 0x80000000, 0, 0, 3, 0x60000000, 0);
  *(int *)(self + 0x990) = file_handle;

  file_size = GetFileSize(file_handle, (unsigned int *)0);
  *(unsigned int *)(self + 0xa94) = file_size;
  *(unsigned int *)(self + 0xa8c) = file_size;
  *(unsigned int *)(self + 0xa90) = file_size;

  csmemset(self + 0x99c, 0, 0xdc);

  if (*(unsigned int *)(self + 0xa94) < 0x800) {
    display_assert("self->read_bytes_left>=sizeof(self->header)",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x3c4, 1);
    system_exit(-1);
  }

  csmemset(self + 0x994, 0, 4);
  csmemset(self + 0x998, 0, 4);

  *(int *)(self + 0xa78) = -1;
  *(int *)(self + 0xa7c) = -1;
  *(int *)(self + 0xa80) = -1;
  *(int *)(self + 0xa84) = -1;
  *(short *)(self + 0xa88) = -1;

  *(short *)(self + 0xab8) = 0;
  *(short *)(self + 0xaba) = 0;
  *(short *)(self + 0xabe) = 0;
  *(short *)(self + 0xac0) = 0;
  *(int *)(self + 0xab4) = 0;
  *(short *)(self + 0xabc) = -1;
}

/* 0x1ba8b0 — FUN_001ba8b0: wait for the pending synchronous cache-copy
 * read to complete, then clear the "raw read in progress" bit.
 *
 * self arrives implicitly in ESI (first instruction reads [ESI+0x950]
 * with no preceding write to ESI) -- the same per-file decompression
 * read-state block documented at cache_files_windows.c's
 * cache_copy_initialize_read_data/FUN_001bb430/FUN_001bb8a0 (TU confirmed
 * via the __FILE__ assert strings below, matching
 * cache_files_decompress_windows.c). Sole caller is
 * cache_copy_initialize_read_data (xref 0x1bb84b, unconditional call),
 * where self is still live in ESI from that function's own @<eax> entry
 * parameter.
 *
 *   +0x950  manual-reset event handle (assert-proven role via
 *           FUN_001bc280's comment in cache_files_windows.c; passed here
 *           to WaitForSingleObjectEx)
 *   +0x994  overlapped_in_use_flags (assert-proven name, reused from
 *           FUN_001bb8a0's comment); bit 0x100 here is a distinct flag
 *           from the per-buffer bits 0-7 tested there -- the assert text
 *           names it "_raw_read_offset", so it is reproduced as a raw
 *           mask on the same field rather than folded into the per-buffer
 *           bit-vector helper.
 *
 * WaitForSingleObjectEx(handle, 5000, TRUE) is called first (disassembly:
 * 3 pushes -- 1, 0x1388, EAX -- immediately before CALL 0x1d00b9, cdecl
 * push order with no ADD ESP after, i.e. the callee-cleans WINAPI
 * convention already used by this TU's other Win32 wrapper thunks). Its
 * return value is compared against WAIT_IO_COMPLETION (0xc0) only after
 * the flag assert below, matching the disassembly order (Ghidra's own
 * decompile named the callee "WaitForSingleObjectEx()" but lost track of
 * the saved-EDI register holding the return value across the intervening
 * branch, misreporting the second compare as an "extraout_EAX" read --
 * disassembly's CMP EDI,0xc0 is authoritative).
 *
 * Source: c:\halo\SOURCE\cache\cache_files_decompress_windows.c, asserts
 * at lines 0x5ca, 0x5cb.
 */
void FUN_001ba8b0(char *self)
{
  unsigned int wait_result;

  wait_result = WaitForSingleObjectEx(*(void **)(self + 0x950), 0x1388, 1);

  if ((*(unsigned int *)(self + 0x994) & 0x100) != 0) {
    display_assert("!BIT_VECTOR_TEST_FLAG(self->overlapped_in_use_flags, "
                   "_raw_read_offset)",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x5ca, 1);
    system_exit(-1);
  }

  if (wait_result != 0xc0) {
    display_assert("wait_result==WAIT_IO_COMPLETION",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x5cb, 1);
    system_exit(-1);
  }

  *(unsigned int *)(self + 0x994) &= 0xfffffeff;
}

/* 0x1ba930 — FUN_001ba930: wait for the pending synchronous cache-copy
 * write to complete, then clear the "raw write in progress" bit.
 *
 * self arrives implicitly in ESI (first instruction reads [ESI+0x994]
 * with no preceding write to ESI) -- the same per-file decompression
 * read-state block used by FUN_001ba8b0 above. Sole caller is
 * cache_copy_initialize_read_data (xref 0x1bb806, unconditional call),
 * where self is passed explicitly from that function's own parameter
 * (see the updated call site and comment in cache_files_windows.c).
 *
 *   +0x950  manual-reset event handle (same field FUN_001ba8b0 waits on)
 *   +0x994  overlapped_in_use_flags; bit 0x400 here is "_raw_write_offset"
 *           per the assert text, distinct from FUN_001ba8b0's bit 0x100
 *           "_raw_read_offset"
 *
 * Unlike FUN_001ba8b0, this function asserts the write-in-progress bit
 * is SET before waiting (disassembly: TEST AH,0x4 / JNZ over the assert
 * -- fires when the bit is clear), then calls WaitForSingleObjectEx(handle,
 * 5000, TRUE), then asserts the bit is CLEAR (fires when still set), then
 * asserts wait_result==WAIT_IO_COMPLETION (0xc0), then clears the bit.
 * Disassembly saves the wait's return value across the intervening asserts
 * in EDI (PUSH EDI / ... / MOV EDI,EAX / ... / CMP EDI,0xc0 / POP EDI),
 * matching FUN_001ba8b0's own note about Ghidra losing track of this and
 * misreporting the compare as "extraout_EAX".
 *
 * Source: c:\halo\SOURCE\cache\cache_files_decompress_windows.c, asserts
 * at lines 0x5d6, 0x5da, 0x5db.
 */
void FUN_001ba930(char *self)
{
  unsigned int wait_result;

  if ((*(unsigned int *)(self + 0x994) & 0x400) == 0) {
    display_assert("BIT_VECTOR_TEST_FLAG(self->overlapped_in_use_flags, "
                   "_raw_write_offset)",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x5d6, 1);
    system_exit(-1);
  }

  wait_result = WaitForSingleObjectEx(*(void **)(self + 0x950), 0x1388, 1);

  if ((*(unsigned int *)(self + 0x994) & 0x400) != 0) {
    display_assert("!BIT_VECTOR_TEST_FLAG(self->overlapped_in_use_flags, "
                   "_raw_write_offset)",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x5da, 1);
    system_exit(-1);
  }

  if (wait_result != 0xc0) {
    display_assert("wait_result==WAIT_IO_COMPLETION",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x5db, 1);
    system_exit(-1);
  }

  *(unsigned int *)(self + 0x994) &= 0xfffffbff;
}

/* 0x1ba9d0 — find the in-use overlapped slot whose id (short array at
 * self+0xa78, 8 entries) equals `key` AND whose in-use bit is set in the
 * bit vector at self+0x998, then mark that slot's 128KB buffer
 * (pointer array at self+0x964) read-only.
 *
 * Disassembly: MOVSX EDX,DI sign-extends the 16-bit index for both the
 * *2 element offset and the SAR EDX,5 word index of the bit vector; the
 * mask is 1 << (index & 0x1f). Loop is a do-while over i = 0..7
 * (CMP DI,0x8 / JL, signed). On no match EAX is zeroed (XOR EAX,EAX).
 * On match LEA EDI,[ESI + EAX*2 + 0xa78] is computed BEFORE the call and
 * moved to EAX afterwards, so the return value is a pointer to the
 * matching id slot, not the protected buffer.
 * physical_memory_protect is __stdcall (no ADD ESP after the CALL);
 * args pushed 2, 0x20000, buffer -> (buffer, 0x20000, 2). */
short *FUN_001ba9d0(char *self, short key)
{
  /* 0x1ba9d4 MOV BX,[EBP+0xc] / 0x1ba9e3 CMP WORD PTR [...],BX — the
   * comparison is 16-bit against the raw parameter; a narrower staged copy
   * would truncate ids >= 0x80. */
  short i;
  for (i = 0; i < 8; i++) {
    short *slot;
    if (((*((short *)((self + 0xa78) + (((int)i) * 2)))) == key) &&
        (((*((unsigned int *)((self + 0x998) + ((((int)i) >> 5) * 4)))) &
          ((unsigned int)(1 << (((int)i) & 0x1f)))) != 0)) {
      slot = (short *)((self + 0xa78) + (((int)i) * 2));
      physical_memory_protect(*((void **)((self + 0x964) + (((int)i) * 4))),
                              0x20000, 2);
      return slot;
    }
  }

  return (short *)0;
}

/* 0x1bab60 — get_write_buffer_size (kb name, PDB line-containment
 * probable; the body is a wait-for-idle + reset of the overlapped
 * bookkeeping, so treat the name as unproven): spin until every
 * overlapped structure is idle, then clear the in-use bit vector.
 *
 * self arrives implicitly in EAX (0x1bab62 MOV ESI,EAX with no prior
 * write to EAX), so kb.json carries `char *self@<eax>`. Both callers are
 * simple_cache_copy_thread (unconditional calls at 0x1bc1e5 and
 * 0x1bc22d). Same per-file decompression state block used by
 * FUN_001ba8b0 / FUN_001ba930 above.
 *
 *   +0x994  overlapped_in_use_flags (assert text below names it)
 *   +0x998  the in-use bit vector cleared at the end (4 bytes, via
 *           csmemset -- 3 pushes 4, 0, ESI then ADD ESP,0xc)
 *
 * Loop shape from the disassembly: EDI holds the retry counter,
 * initialized to 0xb. Each pass copies the counter into AX BEFORE
 * DEC EDI and tests that copy, so the break tests the pre-decrement
 * value (a post-decrement `retries--`). SleepEx(5000, TRUE) is
 * __stdcall (no ADD ESP after CALL 0x1d01c4); its EAX return is
 * compared against WAIT_IO_COMPLETION (0xc0) and the mismatch asserts
 * at line 0x69f. Ghidra's "extraout_EAX" is that same return value.
 *
 * Source: c:\halo\SOURCE\cache\cache_files_decompress_windows.c,
 * asserts at lines 0x69f, 0x6a3.
 */
void get_write_buffer_size(char *self)
{
  short retries;

  retries = 0xb;
  while (*(int *)(self + 0x994) != 0) {
    if (retries-- == 0) {
      break;
    }

    if (SleepEx(0x1388, 1) != 0xc0) {
      display_assert(
        "wait_result==WAIT_IO_COMPLETION",
        "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c", 0x69f, 1);
      system_exit(-1);
    }
  }

  if (*(int *)(self + 0x994) != 0) {
    display_assert("!any_bit_vector_flag_set(self->overlapped_in_use_flags, "
                   "BIT_VECTOR_SIZE_IN_LONGS(NUMBER_OF_OVERLAPPED_STRUCTURES))",
                   "c:\\halo\\SOURCE\\cache\\cache_files_decompress_windows.c",
                   0x6a3, 1);
    system_exit(-1);
  }

  csmemset(self + 0x998, 0, 4);
}

/* 0x1bac00 — FUN_001bac00: set one bit in the dword bit vector at +0x904 of
 * the cache globals block.
 *
 * Disassembly (0x1bac00..0x1bac21, 11 instructions) takes one stack byte
 * argument at [EBP+0x8]: MOV CL,[EBP+8] / MOV EAX,[0x0032ea98] /
 * MOV EDX,0x1 / SHL EDX,CL / MOV ECX,[EAX+0x904] / OR EDX,ECX /
 * MOV [EAX+0x904],EDX. Only CL is read, so the argument is a byte-sized
 * shift count; x86 SHL masks it to 5 bits, which is what Ghidra renders as
 * `& 0x1f` (that mask is the instruction's behavior, not a separate AND in
 * the code, so it is not reproduced here).
 *
 * The globals block is reached through the POINTER global at 0x32ea98, the
 * same block used by FUN_001ba250 / FUN_001ba290 / get_write_buffer_size
 * above and written by FUN_001bc280 in cache_files_windows.c. The pointer
 * is loaded ONCE into EAX and the +0x904 dword is read-modify-written
 * through it.
 *
 *   +0x904  dword treated as a bit vector; this function ORs in a single
 *           bit. Its meaning is UNKNOWN — the artifact reports no callers
 *           (xrefs_to is empty) and no callees, so nothing in the binary
 *           evidence names either the vector or the bit index.
 *
 * No callees, no asserts, no return value. */
void FUN_001bac00(unsigned char bit_index)
{
  unsigned char *globals;

  globals = *(unsigned char **)0x32ea98;
  *(unsigned int *)(globals + 0x904) =
    (1U << bit_index) | *(unsigned int *)(globals + 0x904);
}

/* 0x1bac70 — FUN_001bac70: stop one profiling timer and accumulate its
 * elapsed low-order tick count.
 *
 * Disassembly (0x1bac70..0x1bac9e, 14 instructions):
 *   PUSH EBP / MOV EBP,ESP / SUB ESP,0x8
 *   LEA EAX,[EBP-0x8] / PUSH EAX / CALL 0x1d33e6   (QueryPerformanceCounter)
 *   MOV EDX,[ESI*0x8 + 0x4e5638]
 *   MOV ECX,[EBP-0x8]
 *   MOV EAX,[ESI*0x4 + 0x4e5610]
 *   SUB ECX,EDX / ADD EAX,ECX / MOV [ESI*0x4+0x4e5610],EAX
 *
 * ESI is read without ever being written in this function, so the index
 * arrives implicitly in ESI; kb.json carries `int timer_index@<esi>`.
 * The artifact reports no callers (xrefs_to empty), so nothing in the
 * binary names the index domain — `timer_index` is a mechanical name for
 * "the subscript shared by the two tables", not a proven meaning.
 *
 * Two parallel tables are indexed by the same value:
 *   0x4e5638  stride 8 (LARGE_INTEGER-sized) — only the LOW dword is read;
 *             it holds the counter captured when the timer was started.
 *   0x4e5610  stride 4 — a 32-bit accumulator that this function advances.
 *
 * QueryPerformanceCounter is __stdcall (no ADD ESP after the CALL) and
 * writes 8 bytes, so the local must be a full 8-byte slot even though only
 * counter[0] is consumed. The subtraction direction is `now - start`
 * (SUB ECX,EDX with ECX = the fresh sample), and the accumulate is
 * `accum += delta`. No return value, no asserts.
 */
void FUN_001bac70(int timer_index)
{
  int counter[2];

  QueryPerformanceCounter(counter);
  ((int *)0x4e5610)[timer_index] =
    ((int *)0x4e5610)[timer_index] +
    (counter[0] - ((int *)0x4e5638)[timer_index * 2]);
}

/* 0x1baca0 — FUN_001baca0: dump the cache-file copy timing accumulators.
 *
 * Nine `error(2, ...)` calls: one bare header line, then eight lines each
 * formatting one accumulator as seconds. Every value is computed by
 *   FILD dword ptr [0x4e56xx] / FIDIV dword ptr [0x0032ea9c]
 * i.e. a signed 32-bit tick accumulator divided by the signed 32-bit
 * performance-counter frequency captured at 0x32ea9c (written as the low
 * dword of QueryPerformanceFrequency in cache_files_windows.c). The x87
 * result is stored with FSTP qword, so the varargs slot is a double.
 *
 * The eight accumulators are consecutive entries of the stride-4 table
 * based at 0x4e5610 that FUN_001bac70 advances (indices 1..8); their
 * per-index meanings come only from these format strings.
 *
 * MSVC reuses the just-called argument slots for the next FSTP (the
 * ADD ESP,8 for each call is scheduled after the following FILD), so the
 * stack bookkeeping differs from a naive lowering; the observable call
 * sequence and argument values are unchanged.
 *
 * No callers (xrefs_to empty), no return value, no asserts. */
void FUN_001baca0(void)
{
  error(2, "Timing for copying last cache file:");
  error(2, "    Total read file time: %.3f",
        (double)*(int *)0x4e5614 / (double)*(int *)0x32ea9c);
  error(2, "    Total write file time: %.3f",
        (double)*(int *)0x4e5618 / (double)*(int *)0x32ea9c);
  error(2, "    Total zlib time: %.3f",
        (double)*(int *)0x4e561c / (double)*(int *)0x32ea9c);
  error(2, "    Total zlib during write file time: %.3f",
        (double)*(int *)0x4e5620 / (double)*(int *)0x32ea9c);
  error(2, "    Total thread blocked time: %.3f",
        (double)*(int *)0x4e5624 / (double)*(int *)0x32ea9c);
  error(2, "    Total thread blocked on read time: %.3f",
        (double)*(int *)0x4e5628 / (double)*(int *)0x32ea9c);
  error(2, "    Total thread blocked on write time: %.3f",
        (double)*(int *)0x4e562c / (double)*(int *)0x32ea9c);
  error(2, "    Total copying time: %.3f",
        (double)*(int *)0x4e5630 / (double)*(int *)0x32ea9c);
}

/* 0x1baf50 — FUN_001baf50: wait for the cache-copy worker to go idle, then
 * optionally dump the copy timing accumulators.
 *
 * No stack frame and no asserts (disassembly runs from MOV EAX,[0x0032ea98]
 * at 0x1baf50 to RET at 0x1baf9b, 21 instructions).
 *
 * The globals block is reached through the POINTER global at 0x32ea98, the
 * same block built by FUN_001bc280 in cache_files_windows.c. The original
 * reloads that pointer before every use (MOV EAX,[0x32ea98] at 0x1baf50,
 * MOV EDX,[0x32ea98] at 0x1baf67, MOV ECX,[0x32ea98] at 0x1baf79), which the
 * repeated deref below reproduces.
 *
 *   +0x954  worker-idle event, first polled with a zero timeout
 *           (PUSH 0x0 timeout, PUSH ECX handle at 0x1baf5b/0x1baf5d). If the
 *           poll returns non-zero -- i.e. NOT WAIT_OBJECT_0, so the worker is
 *           busy -- the same event is then waited on with PUSH -0x1 (INFINITE)
 *           at 0x1baf85, blocking until the worker goes idle.
 *   +0x950  "queue end" event signalled between the two waits (0x1baf74), the
 *           same pairing FUN_001ba5d0 uses.
 *
 * 0x4e61d0 is an unknown byte flag tested with MOV AL / TEST AL,AL at
 * 0x1baf8d; the only binary evidence for it is that a non-zero value selects
 * the timing dump. It has no other reference in the recovered sources, so no
 * semantic name is claimed for it.
 *
 * 0x1baf96 is JMP 0x001baca0, a tail call to the void/void timing dump; it is
 * written as an ordinary call followed by return.
 *
 * Type asymmetry between the two APIs follows the kb decls: WaitForSingleObject
 * takes an int handle, SetEvent takes void *. */
void FUN_001baf50(void)
{
  if (WaitForSingleObject(*(int *)(*(unsigned char **)0x32ea98 + 0x954), 0) !=
      0) {
    SetEvent(*(void **)(*(unsigned char **)0x32ea98 + 0x950));
    WaitForSingleObject(*(int *)(*(unsigned char **)0x32ea98 + 0x954), -1);
  }
  if (*(char *)0x4e61d0 != 0) {
    FUN_001baca0();
  }
}
