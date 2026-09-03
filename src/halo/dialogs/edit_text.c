/* Sets a device group's cached value and, if it actually changed, notifies
 * every live device attached to that group (0x96f20).
 *
 * Binary evidence (0x96f20..0x97032, cdecl, args at [EBP+8] int16, [EBP+0xc]
 * float; no other lift's kb decl matches -- this is a standalone entry):
 *
 * 1. Clamp value to [0,1] against the .rdata constants at 0x2533c0 (0.0f) and
 *    0x2533c8 (1.0f) -- same idiom and same addresses as
 *    device_group_set_actual_value below.
 * 2. If device_group_index (the int16 at [EBP+8]) is NONE (-1), return false
 *    (BL stays 0 from the XOR BL,BL at entry) without touching anything.
 * 3. Otherwise datum_get the device-group record (pool at 0x5aa8c8, same
 *    pool as device_group_set_actual_value/device_effect_new/
 *    device_group_get_value in devices.c). That record's +0x02 is a flags
 *    word and +0x04 is the cached float value -- confirmed by
 *    device_effect_new's seeding of the same two fields.
 * 4. If the new value equals the cached one, this is a no-op (return false).
 * 5. If both flag bits 0x1 and 0x2 are already set, this is also a no-op
 *    (matches the `(flags & 1) != 0 && (flags & 2) != 0` gate devices.c
 *    already uses for this same flags word).
 * 6. Otherwise: OR bit 0x2 into the flags, store the new value, set the
 *    return flag true, then walk every device object (object_iterator_new
 *    type_mask 0x380, same mask control_toggle/device_new use). The iterator
 *    buffer is a 16-byte/int[4] struct identical to the one
 *    device_group_set_actual_value and vehicles.c use; index [2] (byte
 *    offset 0x08) holds the current object's datum handle, confirmed by the
 *    vehicles.c comment on object_iterator_next.
 * 7. For each device object whose own group-index field at +0x1a8 (int16)
 *    equals device_group_index, resolve its 'devi' tag (tag_get(0x64657669,
 *    *(int*)object) -- object+0 is the tag index, same field device_new
 *    reads) and forward one of two definition-relative effect-tag fields to
 *    FUN_000967a0(object_handle, tag_index): +0x1fc when the (already
 *    clamped) value is > 0.0f, +0x1ec otherwise. FUN_000967a0 itself gates
 *    on tag_index != -1 and spawns the 'effe'/'snd!' effect, so no NONE
 *    check is needed here.
 *
 * Callers (xrefs_to): control_toggle (0x95874), FUN_00095c60 (0x95e87,
 * 0x95edd), FUN_00097220 (0x9724b, below), FUN_00097260 (0x97299, below),
 * FUN_000bfbc0 (0xbfbf1). */
char FUN_00096f20(int device_group_index, float value)
{
  int16_t index;
  char *device_group;
  unsigned short flags;
  char result;
  int iterator[4];
  char *object;
  char *definition;
  int object_handle;
  int tag_value;

  result = 0;
  index = (int16_t)device_group_index;

  if (value < *(float *)0x2533c0) {
    value = 0.0f;
  } else if (value > *(float *)0x2533c8) {
    value = 1.0f;
  }

  if (index != -1) {
    device_group = (char *)datum_get(*(data_t **)0x5aa8c8, index);
    if (*(float *)(device_group + 4) != value) {
      flags = *(unsigned short *)(device_group + 2);
      if ((flags & 1) == 0 || (flags & 2) == 0) {
        *(unsigned short *)(device_group + 2) = flags | 2;
        *(float *)(device_group + 4) = value;
        result = 1;

        object_iterator_new(iterator, 0x380, 0);
        object = (char *)object_iterator_next(iterator);
        while (object != NULL) {
          definition = (char *)tag_get(0x64657669 /* 'devi' */, *(int *)object);
          if (*(int16_t *)(object + 0x1a8) == index) {
            object_handle = iterator[2];
            if (value > *(float *)0x2533c0) {
              tag_value = *(int *)(definition + 0x1fc);
            } else {
              tag_value = *(int *)(definition + 0x1ec);
            }
            FUN_000967a0(object_handle, tag_value);
          }
          object = (char *)object_iterator_next(iterator);
        }
      }
    }
  }

  return result;
}

/* Forwards a value to the device group attached to a device-family object
 * (0x97040). Resolves object_handle as a device|control|machine object
 * (type_mask 0x380); if it has a device_group_index (int16_t at +0x1b4)
 * other than -1, calls device_group_set_actual_value with that index and
 * the given value. No-op if object_handle == -1, the object can't be
 * resolved, or there is no attached device group.
 * Callers: FUN_00095c10 (0x95c45), FUN_000bfb40 (0xbfb66). */
void FUN_00097040(int object_handle, float value)
{
  char *object;
  int16_t device_group_index;

  if (object_handle != -1) {
    object = (char *)object_get_and_verify_type(object_handle, 0x380);
    device_group_index = *(int16_t *)(object + 0x1b4);
    if (device_group_index != -1) {
      device_group_set_actual_value(device_group_index, value);
    }
  }
}

/* Check if the object's "front" marker faces away from the aim direction
 * (0x971a0). Returns false if the marker forward dot aim > 0 (facing towards
 * aim), true otherwise (facing away, or if the object/marker can't be
 * resolved). */
bool FUN_000971a0(int object_handle, float *position, float *aim_position)
{
  char *obj = (char *)object_try_and_get_and_verify_type(object_handle, 0x100);
  if (obj && (*(uint8_t *)(obj + 0x1c4) & 1) == 0) {
    char marker_buf[0x6c];
    int16_t count =
      object_get_markers_by_string_id(object_handle, "front", marker_buf, 1);
    if (count == 1) {
      float *fwd = (float *)(marker_buf + 0x3c);
      float dot = fwd[0] * aim_position[0] + fwd[1] * aim_position[1] +
                  fwd[2] * aim_position[2];
      if (dot > 0.0f) {
        return false;
      }
    }
  }
  return true;
}

/* Forwards a value to FUN_00096f20 keyed by the device-family object's
 * device_group_index (0x97220). Resolves object_handle (arg0) as a
 * device|control|machine object (type_mask 0x380, same resolve as
 * FUN_00097040 above); if its device_group_index (int16_t at +0x1b4) is not
 * -1, tail-calls FUN_00096f20(device_group_index, arg1) and returns its
 * result. Returns 0 (AL cleared) if object_handle == -1, the object can't be
 * resolved, or there is no attached device group -- FUN_00096f20 is never
 * reached on those paths.
 * arg1 is an opaque float forwarded byte-for-byte (PUSH of the raw dword at
 * [EBP+0xc], no FLD/FSTP) -- this function never interprets it.
 * Caller: FUN_000bfab0 (0xbfade), which resolves arg0/arg1 from a
 * hs_macro_function_evaluate() record. FUN_00096f20 is unported; declared in
 * kb.json as `char FUN_00096f20(int arg0, float arg1);`. */
char FUN_00097220(int arg0, float arg1)
{
  char *object;
  int16_t device_group_index;

  if (arg0 != -1) {
    object = (char *)object_get_and_verify_type(arg0, 0x380);
    device_group_index = *(int16_t *)(object + 0x1b4);
    if (device_group_index != -1) {
      return FUN_00096f20(device_group_index, arg1);
    }
  }
  return 0;
}

/* Sets a device-family object's "on" flag and cached value, then forwards
 * the value to FUN_00096f20 keyed by a second int16 field (0x97260).
 * Resolves object_handle as a device|control|machine object (type_mask
 * 0x380, same resolve as FUN_00097040/FUN_00097220 above). If resolved:
 * ORs bit 0x4 into the flags word at +0x1a4, stores value (raw dword, no
 * FPU conversion) at +0x1ac, then calls
 * FUN_00096f20(sign-extended int16 at +0x1a8, value).
 * No-op if object_handle == -1 or the object can't be resolved.
 * value is forwarded to FUN_00096f20 byte-for-byte (MOV, no FLD/FSTP).
 * Caller: FUN_000bfa30 (0xbfa56).
 * FUN_00096f20 is unported; declared in kb.json as
 * `char FUN_00096f20(int arg0, float arg1);`. */
void FUN_00097260(int object_handle, float value)
{
  char *object;

  if (object_handle != -1) {
    object = (char *)object_get_and_verify_type(object_handle, 0x380);
    *(uint32_t *)(object + 0x1a4) |= 4;
    *(float *)(object + 0x1ac) = value;
    FUN_00096f20((int)*(int16_t *)(object + 0x1a8), value);
  }
}

/* Clamp cursor and selection to valid range [0, strlen] (0x972b0).
 * If cursor == selection after clamping, cancels the selection.
 * Snaps both to valid character boundaries via unicode_snap_cursor. */
void edit_text_clamp_cursor(void *edit_text)
{
  int *et = (int *)edit_text;
  int16_t len;
  int16_t cursor;
  int16_t clamped_cursor;
  int16_t sel;
  int16_t clamped_sel;

  len = (int16_t)csstrlen((const char *)et[0]);

  cursor = *(int16_t *)((int)et + 6);
  if (cursor < 0) {
    clamped_cursor = 0;
  } else if (cursor > len) {
    clamped_cursor = len;
  } else {
    clamped_cursor = cursor;
  }

  sel = *(int16_t *)((int)et + 8);
  *(int16_t *)((int)et + 6) = clamped_cursor;
  if (sel < -1) {
    clamped_sel = -1;
  } else if (sel > len) {
    clamped_sel = len;
  } else {
    clamped_sel = sel;
  }

  *(int16_t *)((int)et + 8) = clamped_sel;
  if (clamped_cursor == clamped_sel) {
    *(int16_t *)((int)et + 8) = -1;
  }

  unicode_snap_cursor((const char *)et[0], (int16_t *)((int)et + 6));
  if (*(int16_t *)((int)et + 8) != -1) {
    unicode_snap_cursor((const char *)et[0], (int16_t *)((int)et + 8));
  }
}

/* Moves the text cursor to the end of the edit text buffer and
 * clears any active selection. Asserts that the edit_text struct
 * is valid (non-null, has buffer, max_length > 0, strlen <= max). */
void edit_text_set_cursor_to_end(void *edit_text)
{
  int *et = (int *)edit_text;
  int16_t len;

  if (et == NULL || et[0] == 0 || *(int16_t *)((int)et + 4) <= 0 ||
      (unsigned int)csstrlen((const char *)et[0]) >
        (unsigned int)(int)*(int16_t *)((int)et + 4)) {
    display_assert("valid_edit_text(edit)",
                   "c:\\halo\\SOURCE\\dialogs\\edit_text.c", 0x9f, 1);
    system_exit(-1);
  }

  edit_text_clamp_cursor(edit_text);

  len = (int16_t)csstrlen((const char *)et[0]);
  *(int16_t *)((int)et + 6) = len;
  *(int16_t *)((int)et + 8) = -1;
}

/* Get the selection range as ordered (min, max) of cursor and anchor (0x973a0).
 * Returns false if no selection is active (selection_start == -1). */
bool edit_text_get_selection_range(void *edit_text, int16_t *out_start,
                                   int16_t *out_end)
{
  int *et = (int *)edit_text;
  int16_t sel;
  int16_t cursor;

  if (et == NULL || et[0] == 0 || *(int16_t *)((int)et + 4) <= 0 ||
      (unsigned int)csstrlen((const char *)et[0]) >
        (unsigned int)(int)*(int16_t *)((int)et + 4)) {
    display_assert("valid_edit_text(edit)",
                   "c:\\halo\\SOURCE\\dialogs\\edit_text.c", 0xae, 1);
    system_exit(-1);
  }

  edit_text_clamp_cursor(edit_text);

  sel = *(int16_t *)((int)et + 8);
  if (sel == -1)
    return false;

  cursor = *(int16_t *)((int)et + 6);
  *out_start = (sel > cursor) ? cursor : sel;

  sel = *(int16_t *)((int)et + 8);
  cursor = *(int16_t *)((int)et + 6);
  *out_end = (sel > cursor) ? sel : cursor;

  return true;
}

/* Validates the edit_text struct and initializes cursor state by
 * placing the cursor at the end of the current text. */
void edit_text_initialize(void *edit_text)
{
  int *et = (int *)edit_text;

  if (et == NULL || et[0] == 0 || *(int16_t *)((int)et + 4) <= 0 ||
      (unsigned int)csstrlen((const char *)et[0]) >
        (unsigned int)(int)*(int16_t *)((int)et + 4)) {
    display_assert("valid_edit_text(edit)",
                   "c:\\halo\\SOURCE\\dialogs\\edit_text.c", 0x19, 1);
    system_exit(-1);
  }

  edit_text_set_cursor_to_end(edit_text);
}

/* Processes a single key event for the edit_text widget. Handles:
 * - Character insertion (with or without active selection)
 * - Left/Right arrow keys for cursor movement
 * - Shift+arrow for extending selection
 * - Backspace/Delete for character or selection deletion
 * When a selection is active, typing replaces it. Backspace/Delete
 * remove the selection range. Arrow keys collapse the selection to
 * the appropriate end. All cursor changes are snapped to unicode
 * character boundaries via unicode_snap_cursor.
 *
 * key_event layout:
 *   offset 0: uint8_t flags (bit 0 = shift held)
 *   offset 1: uint8_t character code
 *   offset 2: int16_t key code (0x1d=backspace, 0x54=delete, 0x4f=left,
 * 0x50=right)
 *
 * edit_text layout:
 *   offset 0: char* text buffer pointer
 *   offset 4: int16_t max_length
 *   offset 6: int16_t cursor_pos
 *   offset 8: int16_t selection (-1 = no selection)
 */
void edit_text_process_key(void *edit_text, void *keystroke)
{
  int *et = (int *)edit_text;
  unsigned char *key = (unsigned char *)keystroke;
  int16_t sel_start, sel_end;
  int text;
  int len;
  int cursor_pos;
  int16_t key_code;

  if (et == NULL || et[0] == 0 || *(int16_t *)((int)et + 4) <= 0 ||
      (unsigned int)csstrlen((const char *)et[0]) >
        (unsigned int)(int)*(int16_t *)((int)et + 4)) {
    display_assert("valid_edit_text(edit)",
                   "c:\\halo\\SOURCE\\dialogs\\edit_text.c", 0x23, 1);
    system_exit(-1);
  }

  edit_text_clamp_cursor(edit_text);

  key_code = *(int16_t *)(key + 2);

  /* --- Backspace / Delete --- */
  if (key_code == 0x1d || key_code == 0x54) {
    /* If there is an active selection, delete the selected range */
    if (edit_text_get_selection_range(edit_text, &sel_end, &sel_start)) {
      text = et[0];
      len = csstrlen((const char *)(text + (int)sel_start));
      csmemmove((void *)(text + (int)sel_end),
                (const void *)(text + (int)sel_start), (unsigned int)(len + 1));
      *(int16_t *)((int)et + 6) = sel_end;
      *(int16_t *)((int)et + 8) = -1;
      unicode_snap_cursor((const char *)et[0], (int16_t *)((int)et + 6));
      return;
    }

    if (key_code == 0x1d) {
      /* Backspace: delete character before cursor */
      int16_t old_cursor = *(int16_t *)((int)et + 6);
      if (old_cursor > 0) {
        unicode_cursor_backward((const char *)et[0], (int16_t *)((int)et + 6));
        text = et[0];
        len = csstrlen((const char *)(text + (int)old_cursor));
        csmemmove((void *)(text + (int)*(int16_t *)((int)et + 6)),
                  (const void *)(text + (int)old_cursor),
                  (unsigned int)(len + 1));
      }
    } else {
      /* Delete: delete character at cursor */
      int16_t cur = *(int16_t *)((int)et + 6);
      int16_t temp_cursor;

      if ((unsigned int)(int)cur >= (unsigned int)csstrlen((const char *)et[0]))
        goto snap_and_return;

      temp_cursor = cur;
      unicode_cursor_forward((const char *)et[0], &temp_cursor);
      text = et[0];
      len = csstrlen((const char *)(text + (int)temp_cursor));
      csmemmove((void *)(text + (int)*(int16_t *)((int)et + 6)),
                (const void *)(text + (int)temp_cursor),
                (unsigned int)(len + 1));
    }
    goto snap_and_return;
  }

  /* --- Left / Right arrow --- */
  if (key_code == 0x4f || key_code == 0x50) {
    if ((key[0] & 1) == 0) {
      /* No shift: if selection active, collapse to appropriate end */
      if (edit_text_get_selection_range(edit_text, &sel_start, &sel_end)) {
        *(int16_t *)((int)et + 8) = -1;
        if (key_code == 0x4f) {
          /* Left: move cursor to selection start */
          *(int16_t *)((int)et + 6) = sel_start;
          unicode_snap_cursor((const char *)et[0], (int16_t *)((int)et + 6));
          return;
        }
        /* Right: move cursor to selection end */
        *(int16_t *)((int)et + 6) = sel_end;
        unicode_snap_cursor((const char *)et[0], (int16_t *)((int)et + 6));
        return;
      }
      if ((key[0] & 1) == 0)
        goto move_cursor;
    }

    /* Shift held (or shift re-check fell through): begin/extend selection */
    if (*(int16_t *)((int)et + 8) == -1) {
      *(int16_t *)((int)et + 8) = *(int16_t *)((int)et + 6);
    }

  move_cursor:
    if (key_code == 0x4f && *(int16_t *)((int)et + 6) > 0) {
      unicode_cursor_backward((const char *)et[0], (int16_t *)((int)et + 6));
    } else if (key_code == 0x50) {
      if ((unsigned int)(int)*(int16_t *)((int)et + 6) <
          (unsigned int)csstrlen((const char *)et[0])) {
        unicode_cursor_forward((const char *)et[0], (int16_t *)((int)et + 6));
      }
    }

    /* If selection collapsed (cursor == selection anchor), clear it */
    if (*(int16_t *)((int)et + 8) == *(int16_t *)((int)et + 6)) {
      *(int16_t *)((int)et + 8) = -1;
      unicode_snap_cursor((const char *)et[0], (int16_t *)((int)et + 6));
      return;
    }
    goto snap_and_return;
  }

  /* --- Character insertion --- */
  if (key[1] == 0 || key[1] == 0xff)
    goto snap_and_return;

  if (edit_text_get_selection_range(edit_text, &sel_start, &sel_end)) {
    /* Replace selection with typed character */
    text = et[0];
    len = csstrlen((const char *)(text + (int)sel_end));
    csmemmove((void *)(text + (int)sel_start + 1),
              (const void *)(text + (int)sel_end), (unsigned int)(len + 1));
    *(int16_t *)((int)et + 6) = sel_start;
    *(int16_t *)((int)et + 8) = -1;
    *(unsigned char *)((int)sel_start + et[0]) = key[1];
    *(int16_t *)((int)et + 6) = *(int16_t *)((int)et + 6) + 1;
    unicode_snap_cursor((const char *)et[0], (int16_t *)((int)et + 6));
    return;
  }

  /* No selection: insert at cursor if room */
  if ((unsigned int)csstrlen((const char *)et[0]) >=
      (unsigned int)(int)*(int16_t *)((int)et + 4))
    goto snap_and_return;

  cursor_pos = (int)*(int16_t *)((int)et + 6) + et[0];
  len = csstrlen((const char *)cursor_pos);
  csmemmove((void *)(cursor_pos + 1), (const void *)cursor_pos,
            (unsigned int)(len + 1));
  *(unsigned char *)((int)*(int16_t *)((int)et + 6) + et[0]) = key[1];
  *(int16_t *)((int)et + 6) = *(int16_t *)((int)et + 6) + 1;
  unicode_snap_cursor((const char *)et[0], (int16_t *)((int)et + 6));
  return;

snap_and_return:
  unicode_snap_cursor((const char *)et[0], (int16_t *)((int)et + 6));
  return;
}
