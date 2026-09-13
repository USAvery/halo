/* 0xfad60 — weapon_place
 *
 * Applies a scenario weapon placement record to a freshly created weapon
 * object: clamps the placement's round counts against the first trigger
 * definition's magazine limits, applies the placement flags, and (when the
 * placement is not "initially at rest") nudges object field 0x14 by a fixed
 * float constant.
 *
 * Confirmed: cdecl, 2 stack args — [EBP+0x8] weapon_handle (EBX),
 * [EBP+0xc] placement (EDI). Confirmed: MOV EAX,EBX at 0xfae0e returns the
 * weapon handle; EBX is saved/restored solely to hold it across the calls.
 * Only reference is the object-placement table at 0x323f50 (no C callers).
 * Confirmed: PUSH 0x4 / PUSH EBX → object_get_and_verify_type at 0xfad6c.
 * Confirmed: MOV EAX,[ESI] / PUSH EAX / PUSH 0x77656170 → tag_get at 0xfad7b;
 * ADD ESP,0x10 cleans both cdecl calls at 0xfad8e.
 * Confirmed: MOV ECX,[EAX+0x4f0] / ADD EAX,0x4f0 / TEST ECX,ECX / JLE — the
 * trigger tag_block count gate at 0xfad80–0xfad93.
 * Confirmed: PUSH 0x70 / PUSH 0x0 / PUSH EAX → tag_block_get_element at
 * 0xfad9a (element size 0x70).
 * Confirmed: CMP CX,DX / JLE with MOVSX on both arms at 0xfadaa–0xfadb7 =
 * signed min(placement+0x48, trigger+0x8) stored to word [ESI+0x25e].
 * Confirmed: CMP CX,AX / JG at 0xfadc6–0xfadd1 = signed
 * min(placement+0x4a, trigger+0xa) stored to word [ESI+0x260].
 * Confirmed: TEST byte [EDI+0x4c],1 → OR/AND 0x20 on dword [ESI+0x4], then
 * OR byte [ESI+0x6],0x2 (same dword, byte 2) in that order.
 * Confirmed: TEST byte [EDI+0x4c],4 → JNZ clears 0x20 in [ESI+0x1a4],
 * else sets it.
 * Confirmed: TEST byte [EDI+0x4c],1 / JNZ skip → FLD [ESI+0x14] /
 * FADD [0x2533e8] / FSTP [ESI+0x14].
 */
int weapon_place(int weapon_handle, void *placement)
{
  char *weapon;
  char *place;
  char *weap_tag;
  char *trigger;
  short placed;
  short limit;

  place = (char *)placement;
  weapon = (char *)object_get_and_verify_type(weapon_handle, 4);
  weap_tag = (char *)tag_get(0x77656170, *(int *)weapon);

  if (*(int *)(weap_tag + 0x4f0) > 0) {
    trigger =
      (char *)tag_block_get_element((void *)(weap_tag + 0x4f0), 0, 0x70);

    placed = *(int16_t *)(place + 0x48);
    limit = *(int16_t *)(trigger + 0x8);
    *(int16_t *)(weapon + 0x25e) = (int16_t)(placed > limit ? limit : placed);

    placed = *(int16_t *)(place + 0x4a);
    limit = *(int16_t *)(trigger + 0xa);
    *(int16_t *)(weapon + 0x260) = (int16_t)(placed > limit ? limit : placed);
  }

  if ((*(uint8_t *)(place + 0x4c) & 1) != 0) {
    *(int *)(weapon + 0x4) |= 0x20;
  } else {
    *(int *)(weapon + 0x4) &= ~0x20;
  }
  *(uint8_t *)(weapon + 0x6) |= 2;

  if ((*(uint8_t *)(place + 0x4c) & 4) == 0) {
    *(int *)(weapon + 0x1a4) |= 0x20;
  } else {
    *(int *)(weapon + 0x1a4) &= ~0x20;
  }

  if ((*(uint8_t *)(place + 0x4c) & 1) == 0) {
    *(float *)(weapon + 0x14) =
      *(float *)(weapon + 0x14) + *(const float *)0x2533e8;
  }
  return weapon_handle;
}

/* 0xfae30 — weapon_preprocess_node_orientations
 *
 * Prefetches the weapon's animation graph block element for node orientation
 * processing. Resolves the weapon tag, finds the 'antr' tag, and calls
 * tag_block_get_element on the first animation element if the block is
 * non-empty. The result is discarded; the call likely primes an internal cache.
 *
 * Confirmed: cdecl, 1 stack arg (weapon_handle).
 * Confirmed: CALL object_get_and_verify_type(weapon_handle, 4).
 * Confirmed: CALL tag_get(0x77656170, *obj) → weap_tag.
 * Confirmed: CALL tag_get(0x616e7472, *(weap_tag+0x44)) → antr.
 * Confirmed: CMP *(int *)(antr+0x18), 0; JZ exit.
 * Confirmed: CALL tag_block_get_element(antr+0x18, 0, 0x1c) (result unused).
 */
void weapon_preprocess_node_orientations(int weapon_handle)
{
  int *obj;
  int tag;
  int antr;

  obj = (int *)object_get_and_verify_type(weapon_handle, 4);
  tag = (int)tag_get(0x77656170, *obj);
  antr = (int)tag_get(0x616e7472, *(int *)(tag + 0x44));
  if (*(int *)(antr + 0x18) != 0) {
    tag_block_get_element((void *)(antr + 0x18), 0, 0x1c);
  }
}

/* 0xfae80 — weapon_get_label */
char *weapon_get_label(int weapon_handle)
{
  char *result = (char *)0x25386f;
  int *obj;
  if (weapon_handle != -1) {
    obj = (int *)object_get_and_verify_type(weapon_handle, 4);
    result = (char *)tag_get(0x77656170, *obj) + 0x30c;
  }
  return result;
}

/* 0xfaeb0 — weapon_set_integrated_light_power */
void weapon_set_integrated_light_power(int weapon_handle, int light_power)
{
  char *weapon_obj = (char *)object_get_and_verify_type(weapon_handle, 4);
  *(int *)(weapon_obj + 0x1f8) = light_power;
}

/* 0xfaed0 — weapon_estimate_time_to_target
 *
 * Given a weapon object handle, a trigger index, and a float value, returns
 * the result of evaluating the projectile charge function for that trigger.
 *
 * Steps:
 *   1. Verify weapon object and get its 'weap' tag (first dword of object
 *      data is the tag index).
 *   2. Validate trigger_index against the triggers block at tag+0x4fc.
 *   3. Get the trigger element (element size 0x114) at the given index.
 *   4. Read the 'proj' (projectile) tag reference from trigger+0xa0.
 *   5. Call projectile_estimate_time_to_target(proj_tag, param_3) — returns a
 * float ratio.
 *   6. Default return value is 0.0f (loaded from 0x2533c0 which holds 0.0f).
 *
 * Confirmed: PUSH 0x4 / PUSH EAX → object_get_and_verify_type at 0xfaed6.
 * Confirmed: MOV ECX,[EAX] / PUSH ECX / PUSH 0x77656170 → tag_get at 0xfaedf.
 * Confirmed: FLD [0x2533c0] (0.0f default) at 0xfaeeb.
 * Confirmed: MOV CX,[EBP+0xc] (trigger_index) at 0xfaef1.
 * Confirmed: ADD ESP,0x10 cleans 4 cdecl args (both calls) at 0xfaef5.
 * Confirmed: MOV EDX,[EAX+0x4fc] / ADD EAX,0x4fc block count/ptr at 0xfaefd.
 * Confirmed: PUSH 0x114 / PUSH ECX / PUSH EAX → tag_block_get_element at
 * 0xfaf0f. Confirmed: MOV EDX,[EBP+0x10] (param_3) at 0xfaf1d. Confirmed: MOV
 * EAX,[EAX+0xa0] (proj tag ref) at 0xfaf20. Confirmed: PUSH EDX / PUSH EAX /
 * PUSH 0x70726f6a → tag_get('proj') at 0xfaf29. ADD ESP,0x8 cleans 2 args; EDX
 * (param_3) already on stack for projectile_estimate_time_to_target. Confirmed:
 * PUSH EAX / CALL projectile_estimate_time_to_target at 0xfaf38; ADD ESP,0x8
 * cleans 2.
 */
float weapon_estimate_time_to_target(int weapon_handle, int16_t trigger_index,
                                     float param_3)
{
  int *weapon_data = (int *)object_get_and_verify_type(weapon_handle, 4);
  void *weap_tag = tag_get(0x77656170, weapon_data[0]);
  float result = 0.0f;

  if (trigger_index >= 0) {
    int *trig_block = (int *)((char *)weap_tag + 0x4fc);
    int trigger_count = trig_block[0];
    int trigger_idx = (int)trigger_index;
    if (trigger_idx < trigger_count) {
      void *trig_elem = tag_block_get_element(trig_block, trigger_idx, 0x114);
      int proj_ref = *(int *)((char *)trig_elem + 0xa0);
      void *proj_tag = tag_get(0x70726f6a, proj_ref);
      result = projectile_estimate_time_to_target(proj_tag, param_3);
    }
  }

  return result;
}

/* 0xfb090 — weapon_must_be_readied
 *
 * Returns non-zero if the weapon's 'must be readied' flag is set
 * (bit 3 of the weapon definition flags at tag+0x308).
 *
 * Confirmed: cdecl, 1 stack arg (weapon_handle).
 * Confirmed: CALL object_get_and_verify_type(weapon_handle, 4).
 * Confirmed: CALL tag_get(0x77656170, *obj).
 * Confirmed: SHR EAX,3; AND EAX,1 on *(uint *)(tag+0x308).
 */
int weapon_must_be_readied(int weapon_handle)
{
  int *obj = (int *)object_get_and_verify_type(weapon_handle, 4);
  int tag = (int)tag_get(0x77656170, *obj);
  return (*(uint32_t *)(tag + 0x308) >> 3) & 1;
}

/* 0xfb0c0 — weapon_is_flag */
bool weapon_is_flag(int object_index)
{
  int *obj = (int *)object_get_and_verify_type(object_index, 4);
  uint32_t *tag = (uint32_t *)tag_get(0x77656170, *obj);
  return (tag[0x308 / 4] >> 3) & 1;
}

/* 0xfb0f0 — weapon_prevents_grenade_throwing
 *
 * Returns 1 if weapon_handle == -1 (no weapon), 1 if the weapon's animation
 * state at +0x1e8 is in range (4, 11), or the flag bit 6 of tag+0x308 is set.
 *
 * Confirmed: cdecl, 1 stack arg (param_1 = weapon_handle).
 * Confirmed: returns 1 immediately if param_1 == -1.
 * Confirmed: CALL object_get_and_verify_type(param_1, 4).
 * Confirmed: CALL tag_get(0x77656170, *obj).
 * Confirmed: SHR EAX,6; AND EAX,1 on *(uint *)(tag+0x308).
 * Confirmed: range check on *(char *)(obj+0x1e8): returns 1 if in (4,11).
 */
int weapon_prevents_grenade_throwing(int weapon_handle)
{
  int *obj;
  int tag;
  char result;

  result = 1;
  if (weapon_handle != -1) {
    obj = (int *)object_get_and_verify_type(weapon_handle, 4);
    tag = (int)tag_get(0x77656170, *obj);
    result = (char)((*(uint32_t *)(tag + 0x308) >> 6) & 1);
    if (*(char *)((char *)obj + 0x1e8) >= 5 &&
        *(char *)((char *)obj + 0x1e8) <= 10) {
      result = 1;
    }
  }
  return result;
}

/* 0xfb140 — weapon_get_animation_frame
 *
 * Looks up a weapon's animation graph and returns a frame count for
 * the requested animation slot. param_2 selects the field (0 -> offset
 * 0x22, 1 -> offset 0x34), param_3 is the animation index used to look
 * up into the first tag block element, param_4 selects a variant when
 * the weapon type at tag+0x4e2 is 1 (dual-wield).
 *
 * Confirmed: cdecl, 4 stack args.
 * Confirmed: CALL object_get_and_verify_type(weapon_handle, 4) at 0xfb14c.
 * Confirmed: CALL tag_get(0x77656170, *obj) at 0xfb159.
 * Confirmed: tag+0x478 is antr tag_index, tag+0x4e2 is weapon type.
 * Confirmed: tag_block at antr+0x48 element size 0x1c.
 * Confirmed: tag_block at antr+0x74 element size 0xb4.
 * Confirmed: switch on param_2: case 0 reads +0x22, case 1 reads +0x34.
 * Confirmed: assert at weapons.c line 0x634 for invalid param_2.
 * Confirmed: dual-wield branch reads indices 0x17(+0x2e), 0x18(+0x30),
 *   0x19(+0x32) from first element's index array.
 */
int16_t weapon_get_animation_frame(int weapon_handle, int16_t param_2,
                                   int16_t param_3, int16_t param_4)
{
  uint32_t *weapon_data;
  int weap_tag;
  int16_t result;
  int antr;
  int elem0;
  int16_t anim_index;
  void *antr_block;
  int anim_elem;
  weapon_data = (uint32_t *)object_get_and_verify_type(weapon_handle, 4);
  weap_tag = (int)tag_get(0x77656170, weapon_data[0]);
  result = 0;

  if (*(int *)(weap_tag + 0x478) == -1)
    return result;

  antr = (int)tag_get(0x616e7472, *(int *)(weap_tag + 0x478));

  if (*(int *)(antr + 0x48) == 0)
    return result;

  elem0 = (int)tag_block_get_element((void *)(antr + 0x48), 0, 0x1c);
  if (elem0 == 0)
    return result;

  if (param_3 < 0 || (int)param_3 >= *(int *)(elem0 + 0x10))
    return result;

  anim_index = *(int16_t *)(*(int *)(elem0 + 0x14) + param_3 * 2);
  if (anim_index == -1)
    return result;

  antr_block = (void *)(antr + 0x74);
  anim_elem = (int)tag_block_get_element(antr_block, (int)anim_index, 0xb4);

  switch (param_2) {
  case 0:
    result = *(int16_t *)(anim_elem + 0x22);
    break;
  case 1:
    result = *(int16_t *)(anim_elem + 0x34);
    break;
  default:
    display_assert(0, "c:\\halo\\SOURCE\\items\\weapons.c", 0x634, 1);
    system_exit(-1);
    break;
  }

  /* Dual-wield variant override: weapon type == 1 and param_2 == 0 */
  if (param_2 == 0 && *(int16_t *)(weap_tag + 0x4e2) == 1) {
    int idx_a;
    int elem_a;
    int idx_b;
    int idx_c;
    if (*(int *)(elem0 + 0x10) <= 0x17) {
      idx_a = -1;
    } else {
      idx_a = (int)*(int16_t *)(*(int *)(elem0 + 0x14) + 0x2e);
    }
    elem_a = (int)tag_block_get_element(antr_block, idx_a, 0xb4);

    if (*(int *)(elem0 + 0x10) <= 0x18) {
      idx_b = -1;
    } else {
      idx_b = (int)*(int16_t *)(*(int *)(elem0 + 0x14) + 0x30);
    }
    tag_block_get_element(antr_block, idx_b, 0xb4);

    if (*(int *)(elem0 + 0x10) <= 0x19) {
      idx_c = -1;
    } else {
      idx_c = (int)*(int16_t *)(*(int *)(elem0 + 0x14) + 0x32);
    }
    tag_block_get_element(antr_block, idx_c, 0xb4);

    switch (param_4) {
    case 0:
      result = *(int16_t *)(elem_a + 0x22);
      break;
    case 2:
      result = *(int16_t *)(elem_a + 0x22);
      return result;
    }
  }

  return result;
}

/* 0xfb2f0 — weapon_overcharged
 *
 * Returns 1 if the weapon's trigger state at +0x211 is 2 (overcharge) or
 * 3 (overcharge-releasing), 0 otherwise.
 *
 * Confirmed: cdecl, 1 stack arg (weapon_handle).
 * Confirmed: CALL object_get_and_verify_type(weapon_handle, 4).
 * Confirmed: CMP BYTE PTR [EAX+0x211], 2 / JE; CMP ..., 3 / JE.
 * Confirmed: returns 1 if state == 2 or 3, else 0.
 */
int weapon_overcharged(int weapon_handle)
{
  int obj = (int)object_get_and_verify_type(weapon_handle, 4);
  if (*(char *)(obj + 0x211) != 2 && *(char *)(obj + 0x211) != 3)
    return 0;
  return 1;
}

/* 0xfb320 — weapon trigger-block accessor (weapons.c:1639 assert)
 *
 * Confirmed: register args — weapon_obj in EDI, trigger_index in SI
 *   (MOV EAX,[EDI] / TEST SI,SI / MOVSX ECX,SI).
 * Confirmed: PUSH EAX (= *(int *)weapon_obj) / PUSH 0x77656170 /
 *   CALL tag_get / ADD ESP,8 — cdecl, tag_index is the first dword of the
 *   weapon object.
 * Confirmed: bounds check TEST SI,SI / JL and MOVSX ECX,SI /
 *   CMP ECX,[EAX+0x4fc] / JL — signed compare against the tag-data
 *   triggers count at +0x4fc.
 * Confirmed: assert path PUSH 1 / PUSH 0x667 (line 1639) /
 *   PUSH filepath / PUSH reason / CALL display_assert, then
 *   PUSH -1 / CALL system_exit.
 * Confirmed: return LEA EDX,[EAX+EAX*8] ; LEA EAX,[EDI+EDX*4+0x210]
 *   => weapon_obj + 0x210 + trigger_index * 36 (stride 36 = 9*4).
 * Unknown: the weapon-object struct layout at +0x210 and the tag-data
 *   layout at +0x4fc are not modelled; raw offsets retained.
 */
void *FUN_000fb320(void *weapon_obj, int16_t trigger_index)
{
  int *tag_data = (int *)tag_get(0x77656170, *(int *)weapon_obj);

  assert_halt_msg(trigger_index >= 0 &&
                    trigger_index < *(int *)((char *)tag_data + 0x4fc),
                  "trigger_index>=0 && "
                  "trigger_index<weapon_definition->weapon.triggers.count");

  return (void *)((char *)weapon_obj + 0x210 + trigger_index * 36);
}

void *FUN_000fb370(void *weapon_obj, int16_t magazine_index)
{
  int *tag_data = (int *)tag_get(0x77656170, *(int *)weapon_obj);

  assert_halt(magazine_index >= 0 &&
              magazine_index < *(int *)((char *)tag_data + 0x4f0));

  return (void *)((char *)weapon_obj + (magazine_index + 50) * 12);
}

/* 0xfb3c0 — weapon_has_activity
 *
 * Returns true if the weapon has any active triggers, magazines, or
 * pending activity. Checks five fields in the weapon data.
 *
 * Confirmed: regparm, weapon_handle in EAX.
 * Confirmed: PUSH 4 / PUSH EAX / CALL object_get_and_verify_type.
 * Confirmed: checks offsets 0x211, 0x235, 0x258, 0x264, 0x1e8.
 * Confirmed: returns 0 (false) only if ALL five are zero/null.
 */
bool weapon_has_activity(int weapon_handle)
{
  char *weapon_data = (char *)object_get_and_verify_type(weapon_handle, 4);

  if (*(char *)(weapon_data + 0x211) != 0 ||
      *(char *)(weapon_data + 0x235) != 0 ||
      *(int16_t *)(weapon_data + 0x258) != 0 ||
      *(int16_t *)(weapon_data + 0x264) != 0 ||
      *(char *)(weapon_data + 0x1e8) != 0) {
    return true;
  }

  return false;
}

/* 0xfb510 — weapon trigger charge fraction
 *
 * Returns a float in ST(0) describing the trigger's charge state.
 *
 * Confirmed: register args — weapon_handle in EAX (PUSH 0x4 / PUSH EAX /
 *   CALL object_get_and_verify_type), trigger_index in CX
 *   (MOV ESI,ECX at 0xfb51a; later MOVSX EDX,SI).
 * Confirmed: call order is object_get_and_verify_type(handle, 4) ->
 *   FUN_000fb320(EDI=object, SI=trigger_index) -> tag_get(0x77656170,
 *   *(int *)object) -> tag_block_get_element(tag_data+0x4fc,
 *   (int)(int16_t)trigger_index, 0x114). All four calls run
 *   unconditionally before the state test (ADD ESP,0x1c at 0xfb54f).
 * Confirmed: MOVSX ECX,byte ptr [EBX+0x1] / SUB ECX,2 / JZ (state 2)
 *   / DEC ECX / JZ (state 3); EBX is the FUN_000fb320 trigger pointer,
 *   so the byte is the trigger state at weapon_object+0x211.
 * Confirmed state 2: MOVSX ECX,word ptr [EBX+0x2] / FILD dword /
 *   FMUL [0x2546a4] / FDIV [EAX+0x48] / FSUBR [0x2533c8]
 *   => *(float *)0x2533c8 - (ticks * *(float *)0x2546a4) /
 *      *(float *)(trigger_definition + 0x48).
 *   EAX at the FDIV is the tag_block_get_element result.
 * Confirmed state 3: FLD [0x2533c8]. Default: FLD [0x2533c0].
 * Unknown: the 0x114-byte trigger definition layout (+0x48) and the
 *   weapon-object trigger layout (+0x1/+0x2) are not modelled.
 */
float FUN_000fb510(int weapon_handle, int16_t trigger_index)
{
  int *weapon_obj;
  char *trigger;
  char *trigger_defn;
  float charge;
  int weapon_defn;

  weapon_obj = (int *)object_get_and_verify_type(weapon_handle, 4);
  trigger = (char *)FUN_000fb320(weapon_obj, trigger_index);
  weapon_defn = (int)tag_get(0x77656170, *weapon_obj);
  trigger_defn = (char *)tag_block_get_element(
    (void *)((char *)weapon_defn + 0x4fc), trigger_index, 0x114);

  switch (trigger[1]) {
  case 2:
    charge = (float)*(int16_t *)(trigger + 2) * *(float *)0x2546a4;
    return *(float *)0x2533c8 - charge / *(float *)(trigger_defn + 0x48);
  case 3:
    return *(float *)0x2533c8;
  }
  return *(float *)0x2533c0;
}

/* 0xfb6e0 — weapon_start_effect
 *
 * Starts an effect or sound associated with a weapon trigger. Resolves
 * the parent object, determines the tag group of the trigger effect
 * (0x65666665='effe' or 0x736e6421='snd!'), and dispatches accordingly.
 *
 * Confirmed: regparm, weapon_handle in EAX, 3 stack args.
 * Confirmed: MOV ESI, EAX at 0xfb6e8 saves weapon_handle.
 * Confirmed: if trigger_effect == -1, returns -1 immediately.
 * Confirmed: object byte+4 bit 0 checked; if set and +0xcc != -1,
 *   parent_handle = object+0xcc.
 * Confirmed: second object_get_and_verify_type call to get unit handle.
 * Confirmed: tag_get_group_tag returns tag group; dispatches on effe/snd!.
 * Confirmed: assert at weapons.c line 0x9d2 for unknown tag group.
 * Confirmed: snd! branch reads globals [0x31fc1c] and [0x31fc3c].
 * Confirmed: effe branch calls FUN_0009ec30 with 8 args.
 */
int weapon_start_effect(int trigger_effect, float scale, float param_3,
                        int weapon_handle)
{
  char *weapon_data;
  int parent_handle;
  char *weapon_data2;
  int object_handle;
  int tag_group;

  if (trigger_effect != -1) {
    weapon_data = (char *)object_get_and_verify_type(weapon_handle, 4);
    parent_handle = weapon_handle;
    if ((*(uint8_t *)(weapon_data + 4) & 1) != 0 &&
        *(int *)(weapon_data + 0xcc) != -1) {
      parent_handle = *(int *)(weapon_data + 0xcc);
    }

    weapon_data2 = (char *)object_get_and_verify_type(weapon_handle, 4);
    object_handle = -1;
    if (*(int *)(weapon_data2 + 0xcc) != -1) {
      if (object_try_and_get_and_verify_type(*(int *)(weapon_data2 + 0xcc),
                                             3) != 0) {
        object_handle = *(int *)(weapon_data2 + 0xcc);
      }
    }

    tag_group = tag_get_group_tag(trigger_effect);
    switch (tag_group) {
    case 0x65666665:
      return (int)FUN_0009ec30(trigger_effect, object_handle, parent_handle, -1,
                               scale, param_3, 0, 0);
    case 0x736e6421: {
      float *position = *(float **)0x31fc1c;
      float *forward = *(float **)0x31fc3c;
      object_impulse_sound_new(object_handle, trigger_effect, -1, position,
                               forward, scale);
      return -1;
    }
    default:
      display_assert(0, "c:\\halo\\SOURCE\\items\\weapons.c", 0x9d2, 1);
      system_exit(-1);
      break;
    }
  }

  return -1;
}

/* 0xfb7d0 — weapon stop/detach effect helper
 *
 * Sibling of weapon_start_effect (0xfb6e0): same parent-resolution
 * preamble, but dispatches to FUN_0009eb40 with three -1 shorts.
 *
 * Confirmed: no prologue; EBX and ESI are live on entry (only EDI is
 *   saved/restored via PUSH EDI / POP EDI at 0xfb7d6 / 0xfb831).
 * Confirmed: OR EAX,0xffffffff at 0xfb7d0 and 0xfb833 => int return of -1
 *   on both early-exit paths; the taken path returns FUN_0009eb40's EAX.
 * Confirmed: CMP EBX,-0x1 / JZ 0xfb836 guards the whole body.
 * Confirmed: PUSH 0x4 / PUSH ESI / CALL 0x13d680 (twice) =>
 *   object_get_and_verify_type(esi, 4); ESI is the weapon handle.
 * Confirmed: MOV EDI,ESI then TEST CL,0x1 on byte [EAX+4]; if set and
 *   [EAX+0xcc] != -1, EDI = [EAX+0xcc] (parent handle).
 * Confirmed: second lookup's [EAX+0xcc], if != -1, is passed as
 *   object_try_and_get_and_verify_type(handle, 3) with the result
 *   discarded (no test of EAX after ADD ESP,0x8 at 0xfb819).
 * Confirmed: PUSH -1 / -1 / -1 / EDI / EBX / CALL 0x9eb40 => args
 *   (ebx, parent_handle, -1, -1, -1); ADD ESP,0x14.
 * Unknown: meaning of the EBX argument and of FUN_0009eb40; the
 *   object fields +0x4 (flags byte) and +0xcc (parent handle) follow the
 *   0xfb6e0 sibling's usage.
 */
int FUN_000fb7d0(int param_1, int weapon_handle)
{
  char *weapon_data;
  int parent_handle;
  char *weapon_data2;

  if (param_1 != -1) {
    weapon_data = (char *)object_get_and_verify_type(weapon_handle, 4);
    parent_handle = weapon_handle;
    if ((*(uint8_t *)(weapon_data + 4) & 1) != 0 &&
        *(int *)(weapon_data + 0xcc) != -1) {
      parent_handle = *(int *)(weapon_data + 0xcc);
    }

    weapon_data2 = (char *)object_get_and_verify_type(weapon_handle, 4);
    if (*(int *)(weapon_data2 + 0xcc) != -1) {
      object_try_and_get_and_verify_type(*(int *)(weapon_data2 + 0xcc), 3);
    }

    if (parent_handle != -1) {
      return FUN_0009eb40(param_1, parent_handle, -1, -1, -1);
    }
  }

  return -1;
}

/* 0xfb880 — weapon_trigger_release_charge
 *
 * Sets a weapon trigger's state byte and its accompanying 16-bit charge
 * value. Resolves the weapon object, bounds-checks the trigger index and
 * the new state, then writes both fields of the trigger record.
 *
 * Confirmed: register args — weapon_handle in EAX (PUSH 4 / PUSH EAX /
 *   CALL object_get_and_verify_type / ADD ESP,8 at 0xfb887), trigger_index
 *   in SI (TEST SI,SI / CMP SI,0x2), new_state in BX (TEST BX,BX /
 *   CMP BX,0x9). One 16-bit stack arg at [EBP+8] (MOV DX,[EBP+8]).
 * Confirmed: object lookup happens BEFORE both asserts (result kept in EDI).
 * Confirmed: assert paths PUSH 1 / PUSH 0xa11 (resp. 0xa12) / PUSH filepath
 *   / PUSH reason / CALL display_assert then PUSH -1 / CALL system_exit.
 * Confirmed: address form MOVSX EAX,SI / LEA ECX,[EAX+EAX*8] /
 *   LEA EAX,[EDI+ECX*4] => weapon_data + trigger_index * 36.
 * Confirmed: store order — byte BL to +0x211 first, then word DX to +0x212.
 * Confirmed: trigger stride 36 and base +0x210 agree with FUN_000fb320;
 *   +0x211 is the state byte read as trigger[1] by FUN_000fb510 and
 *   +0x212 the int16 read as *(int16_t *)(trigger + 2).
 * Unknown: the meaning of the stack-passed 16-bit value beyond its use as
 *   the trigger's charge/tick field; raw offsets retained to match the
 *   sibling accessors.
 */
void weapon_trigger_release_charge(int16_t charge_ticks, int weapon_handle,
                                   int16_t trigger_index, int16_t new_state)
{
  char *weapon_data;

  weapon_data = (char *)object_get_and_verify_type(weapon_handle, 4);

  assert_halt_msg_at("trigger_index>=0 && "
                     "trigger_index<MAXIMUM_NUMBER_OF_TRIGGERS_PER_WEAPON",
                     "c:\\halo\\SOURCE\\items\\weapons.c", 0xa11,
                     trigger_index >= 0 &&
                       trigger_index < MAXIMUM_NUMBER_OF_TRIGGERS_PER_WEAPON);
  assert_halt_msg_at("new_state>=0 && new_state<NUMBER_OF_TRIGGER_STATES",
                     "c:\\halo\\SOURCE\\items\\weapons.c", 0xa12,
                     new_state >= 0 && new_state < NUMBER_OF_TRIGGER_STATES);

  *(char *)(weapon_data + trigger_index * 36 + 0x211) = (char)new_state;
  *(int16_t *)(weapon_data + trigger_index * 36 + 0x212) = charge_ticks;
}

/* 0xfb910 — weapon trigger "charge ready" latch
 *
 * Resolves the weapon object and its trigger definition, then — when the
 * definition's +0xa4 float exceeds *(float *)0x2533c0 and the definition's
 * 0x80 flag agrees with the boolean stack argument — stores 1.0f into the
 * trigger record at +0x14.
 *
 * Confirmed: register args — weapon_handle in EAX (PUSH 0x4 / PUSH EAX /
 *   CALL object_get_and_verify_type at 0xfb91b), trigger_index in CX
 *   (MOV ESI,ECX at 0xfb919, later MOVSX EDX,SI at 0xfb936). One byte
 *   stack arg at [EBP+8] (MOV AL,byte ptr [EBP+8]).
 * Confirmed: call order object_get_and_verify_type(handle, 4) ->
 *   tag_get(0x77656170, *(int *)object) -> FUN_000fb320(EDI=object,
 *   SI=trigger_index) -> tag_block_get_element(tag_data+0x4fc,
 *   (int)(int16_t)trigger_index, 0x114). Note this differs from
 *   FUN_000fb510, which calls FUN_000fb320 before tag_get.
 *   All four run unconditionally; one ADD ESP,0x1c at 0xfb94f cleans
 *   the 7 pushed dwords of all three cdecl calls.
 * Confirmed: FLD [ECX+0xa4] / FCOMP [0x2533c0] / FNSTSW AX /
 *   TEST AH,0x41 / JNZ end => continue only when definition+0xa4 >
 *   *(float *)0x2533c0 (C3|C0 set means <= or unordered).
 * Confirmed: MOV ECX,[ECX] / AND ECX,0x80 then the two-arm test at
 *   0xfb96d..0xfb97f stores only when (flag != 0 && arg != 0) or
 *   (flag == 0 && arg == 0).
 * Confirmed: store target is EDI, the FUN_000fb320 return (trigger
 *   record), offset +0x14, immediate 0x3f800000 = 1.0f.
 * Unknown: the 0x114-byte trigger definition layout (+0x0 flags, +0xa4
 *   float) and the trigger record field at +0x14 are not modelled;
 *   the meaning of the byte stack argument beyond its boolean use.
 */
void FUN_000fb910(char param_1, int weapon_handle, int16_t trigger_index)
{
  int *weapon_obj;
  int weapon_defn;
  char *trigger;
  char *trigger_defn;

  weapon_obj = (int *)object_get_and_verify_type(weapon_handle, 4);
  weapon_defn = (int)tag_get(0x77656170, *weapon_obj);
  trigger = (char *)FUN_000fb320(weapon_obj, trigger_index);
  trigger_defn = (char *)tag_block_get_element(
    (void *)((char *)weapon_defn + 0x4fc), trigger_index, 0x114);

  if (*(float *)(trigger_defn + 0xa4) > *(float *)0x2533c0) {
    if (((*(uint32_t *)trigger_defn & 0x80) != 0 && param_1 != 0) ||
        ((*(uint32_t *)trigger_defn & 0x80) == 0 && param_1 == 0)) {
      *(float *)(trigger + 0x14) = 1.0f;
    }
  }
}

/* 0xfba20 — weapon_set_animation_state
 *
 * Sets the weapon's animation state by looking up the animation graph
 * and choosing a random animation for the given state. The state
 * parameter arrives in BX.
 *
 * Confirmed: regparm, state in BX. 2 stack args (weapon_handle, param_2).
 * Confirmed: PUSH 4 / PUSH [EBP+8] / CALL object_get_and_verify_type.
 * Confirmed: tag_get(0x77656170, *obj) for weapon tag.
 * Confirmed: param_2 == 0 && weapon_data+0x1e8 != 0 => priority check.
 * Confirmed: tag+0x44 is the animation graph tag_index (antr).
 * Confirmed: tag_block at antr+0x18, element size 0x1c.
 * Confirmed: switch on state (0..10) maps to animation indices.
 * Confirmed: model_animation_choose_random(1, tag+0x44, anim_index).
 * Confirmed: stores to weapon_data+0x80, +0x82, +0x1e8.
 * Confirmed: tail section resolves unit handle and calls
 * unit_handle_weapon_state_change. Confirmed: returns AL=1 on success, AL=0 on
 * early exit.
 */
int weapon_set_animation_state(int weapon_handle, char param_2, int16_t state)
{
  uint32_t *weapon_data =
    (uint32_t *)object_get_and_verify_type(weapon_handle, 4);
  int weap_tag = (int)tag_get(0x77656170, weapon_data[0]);
  int antr;
  int elem;
  int16_t anim_slot;
  uint16_t raw_index;
  int16_t chosen;

  /* Priority check: if param_2 is 0 and weapon has a current state,
   * only allow transitions from equal or higher priority */
  if (param_2 == 0) {
    int16_t current_state = (int16_t) * (char *)((char *)weapon_data + 0x1e8);
    if (current_state != 0) {
      if (current_state < 1)
        return 0;
      if (current_state > 2)
        return 0;
      if (state < current_state)
        return 0;
    }
  }

  /* Look up animation graph */
  if (*(int *)(weap_tag + 0x44) == -1)
    goto tail;

  antr = (int)tag_get(0x616e7472, *(int *)(weap_tag + 0x44));
  if (*(int *)(antr + 0x18) == 0)
    goto tail;

  elem = (int)tag_block_get_element((void *)(antr + 0x18), 0, 0x1c);
  if (elem == 0)
    goto tail;

  /* Map weapon animation state to animation block index */
  switch (state) {
  case 0:
    anim_slot = 0;
    break;
  case 1:
    anim_slot = 9;
    break;
  case 2:
    anim_slot = 10;
    break;
  case 3:
    anim_slot = 5;
    break;
  case 4:
    anim_slot = 6;
    break;
  case 5:
  case 6:
    anim_slot = 3;
    break;
  case 7:
  case 8:
    anim_slot = 8;
    break;
  case 9:
    anim_slot = 1;
    break;
  case 10:
    anim_slot = 2;
    break;
  default:
    goto tail;
  }

  /* Resolve animation index from the lookup table */
  if ((int)anim_slot < *(int *)(elem + 0x10)) {
    raw_index = *(uint16_t *)(*(int *)(elem + 0x14) + anim_slot * 2);
    if (raw_index == 0xffff) {
      if (state != 0)
        goto tail;
    }
  } else {
    raw_index = 0xffff;
    if (state != 0)
      goto tail;
  }

  /* Choose a random animation and set the weapon state */
  chosen = (int16_t)model_animation_choose_random(1, *(int *)(weap_tag + 0x44),
                                                  (int16_t)raw_index);
  *(int16_t *)((char *)weapon_data + 0x80) = chosen;
  *(int16_t *)((char *)weapon_data + 0x82) = 0;
  *(char *)((char *)weapon_data + 0x1e8) = (char)state;

tail:
  /* Resolve unit handle and notify sound system */
  {
    int unit_data;
    int unit_handle;
    int check;
    int check2;
    unit_data = (int)object_get_and_verify_type(weapon_handle, 4);
    unit_handle = -1;
    if (*(int *)(unit_data + 0xcc) != -1) {
      check =
        (int)object_try_and_get_and_verify_type(*(int *)(unit_data + 0xcc), 3);
      if (check != 0) {
        unit_handle = *(int *)(unit_data + 0xcc);
      }
    }
    check2 = (int)object_try_and_get_and_verify_type(unit_handle, 3);
    if (check2 != 0) {
      unit_handle_weapon_state_change(unit_handle, state);
    }
  }
  return 1;
}

/* weapon_set_total_rounds (0xfbbd0)
 *
 * Sets weapon magazine rounds from an input array. For each magazine,
 * clamps the input value to the magazine's maximum capacity, then clamps
 * the loaded rounds to not exceed the new total.
 */
void weapon_set_total_rounds(int weapon_handle, int16_t *rounds_array)
{
  int *weapon = (int *)object_get_and_verify_type(weapon_handle, 4);
  int tag_data = (int)tag_get(0x77656170, *weapon);
  int magazine_count;
  int16_t i;

  if (rounds_array == NULL) {
    display_assert("rounds_array", "c:\\halo\\SOURCE\\items\\weapons.c", 0xc0a,
                   1);
    system_exit(-1);
  }

  magazine_count = *(int *)(tag_data + 0x4f0);
  for (i = 0; (int)i < magazine_count; i++) {
    int check_tag;
    char *mag_def;
    int16_t max_rounds;
    int16_t input_rounds;
    int16_t new_total;
    char *magazine;
    int16_t current_loaded;
    int16_t new_loaded;
    check_tag = (int)tag_get(0x77656170, *weapon);
    if ((int16_t)i < 0 || (int)i >= *(int *)(check_tag + 0x4f0)) {
      display_assert("magazine_index>=0 && "
                     "magazine_index<weapon_definition->weapon.magazines.count",
                     "c:\\halo\\SOURCE\\items\\weapons.c", 0x672, 1);
      system_exit(-1);
    }
    mag_def =
      (char *)tag_block_get_element((int *)(tag_data + 0x4f0), (int)i, 0x70);
    max_rounds = *(int16_t *)(mag_def + 8);
    input_rounds = rounds_array[i];
    new_total = (input_rounds < max_rounds) ? input_rounds : max_rounds;
    magazine = (char *)weapon + ((int)i * 3 + 0x96) * 4;
    current_loaded = *(int16_t *)(magazine + 8);
    *(int16_t *)(magazine + 6) = new_total;
    new_loaded = (current_loaded <= new_total) ? current_loaded : new_total;
    *(int16_t *)(magazine + 8) = new_loaded;
  }
}

/* 0xfbea0 — weapon_delete
 *
 * Debug-build guard: when the game engine is running, a weapon that is a
 * flag (CTF flag / oddball style carried object) must never be deleted.
 * The whole body is the assert; there is no other work in this function.
 *
 * Confirmed: cdecl, 1 stack arg at [EBP+8] (weapon_index).
 * Confirmed: CALL game_engine_running (0xa8e30); TEST AL,AL; JZ exit.
 * Confirmed: PUSH 4 / PUSH [EBP+8] / CALL object_get_and_verify_type.
 * Confirmed: MOV ECX,[EAX] / PUSH ECX / PUSH 0x77656170 / CALL tag_get,
 *   then ADD ESP,0x10 clears both cdecl calls (2+2 stack args).
 * Confirmed: MOV EDX,[EAX+0x308]; SHR EDX,3; TEST DL,1 — the weapon_is_flag
 *   test is INLINED here (no CALL to 0xfb0c0), so it is spelled inline.
 * Confirmed: assert text "!weapon_is_flag(weapon_index)" at line 0xea,
 *   PUSH 1 (halt) / CALL display_assert then PUSH -1 / CALL system_exit.
 */
void weapon_delete(int weapon_index)
{
  int *obj;
  uint32_t *weap_tag;

  if (game_engine_running()) {
    obj = (int *)object_get_and_verify_type(weapon_index, 4);
    weap_tag = (uint32_t *)tag_get(0x77656170, *obj);
    assert_halt_msg_at("!weapon_is_flag(weapon_index)",
                       "c:\\halo\\SOURCE\\items\\weapons.c", 0xea,
                       ((weap_tag[0x308 / 4] >> 3) & 1) == 0);
  }
}

/* Transfer ammunition from a source object into a weapon's magazines (0xfc290).
 * For each magazine that's below initial capacity, tries to transfer rounds
 * from either the same weapon type or matching equipment. Deletes the source if
 * fully depleted. Returns true if any ammo source was matched. */
bool weapon_handle_potential_inventory_item(int weapon_handle,
                                            int source_handle,
                                            uint16_t local_player_index,
                                            int16_t *rounds_out)
{
  int *weapon_data = (int *)object_get_and_verify_type(weapon_handle, 4);
  int tag_data = (int)tag_get(0x77656170, *weapon_data);
  int *source_data = (int *)object_get_and_verify_type(source_handle, 0x1c);
  int source_tag = *source_data;
  bool found = false;

  {
    int16_t i;
    for (i = 0; (int)i < *(int *)(tag_data + 0x4f0); i++) {
      int check_tag;
      int mag_offset;
      char *mag_def;
      int16_t *mag_rounds;
      int16_t transfer;
      check_tag = (int)tag_get(0x77656170, *weapon_data);
      if ((int16_t)i < 0 || (int)i >= *(int *)(check_tag + 0x4f0)) {
        display_assert(
          "magazine_index>=0 && "
          "magazine_index<weapon_definition->weapon.magazines.count",
          "c:\\halo\\SOURCE\\items\\weapons.c", 0x672, 1);
        system_exit(-1);
      }
      mag_offset = ((int)i * 3 + 0x96) * 4;
      mag_def =
        (char *)tag_block_get_element((char *)tag_data + 0x4f0, (int)i, 0x70);
      mag_rounds = (int16_t *)((char *)weapon_data + mag_offset + 6);
      transfer = 0;
      if (*mag_rounds < *(int16_t *)(mag_def + 8)) {
        int16_t need;
        need = *(int16_t *)(mag_def + 8) - *mag_rounds;
        if (*weapon_data == source_tag) {
          int *src_weap;
          int src_tag2;
          int16_t *src_rounds;
          src_weap = (int *)object_get_and_verify_type(source_handle, 4);
          src_tag2 = (int)tag_get(0x77656170, *src_weap);
          if ((int16_t)i < 0 || (int)i >= *(int *)(src_tag2 + 0x4f0)) {
            display_assert(
              "magazine_index>=0 && "
              "magazine_index<weapon_definition->weapon.magazines.count",
              "c:\\halo\\SOURCE\\items\\weapons.c", 0x672, 1);
            system_exit(-1);
          }
          src_rounds = (int16_t *)((char *)src_weap + mag_offset + 6);
          transfer = need;
          if (*src_rounds <= need) {
            transfer = *src_rounds;
          }
          if (transfer > 0) {
            *src_rounds = *src_rounds - transfer;
            if (*(int *)(tag_data + 0x49c) != -1 &&
                (int16_t)local_player_index != -1) {
              sound_impulse_start(*(int *)(tag_data + 0x49c), 1.0f);
            }
            if (*src_rounds == 0) {
              object_delete(source_handle);
            }
          }
          found = true;
        } else {
          int *equip_block;
          int16_t j;
          equip_block = (int *)(mag_def + 0x64);
          for (j = 0; (int)j < *equip_block; j++) {
            int16_t *entry;
            entry = (int16_t *)tag_block_get_element(equip_block, (int)j, 0x1c);
            if (*(int *)(entry + 0xc) == source_tag) {
              transfer = need;
              if (*entry <= need) {
                transfer = *entry;
              }
              if (transfer > 0) {
                if ((int16_t)local_player_index != -1) {
                  FUN_000f67f0(*(int *)(entry + 0xc));
                }
                object_delete(source_handle);
                found = true;
                break;
              }
            }
          }
        }
        *mag_rounds = *mag_rounds + transfer;
        *rounds_out = transfer;
      }
    }
  }
  return found;
}

/* weapon_owner_update (0xfc4b0)
 *
 * Validates the weapon object (type 4) and resolves its 'weap' tag, stores the
 * low 16 bits of a1 at weapon+0x1e0, then evaluates transition function type 4
 * over a2 and stores the result as the float at weapon+0x1e4
 * (weapon->weapon.primary_trigger, per the assert string). Asserts the stored
 * value is a valid real (exponent != 0xff).
 *
 * Confirmed: PUSH 4 / PUSH handle -> object_get_and_verify_type (0x13d680).
 * Confirmed: PUSH weapon[0] / PUSH 'weap' -> tag_get (0x1ba140), result unused.
 * Confirmed: MOV DX,[EBP+0xc]; MOV [ESI+0x1e0],DX — 16-bit store of a1.
 * Confirmed: PUSH [EBP+0x10] / PUSH 4 -> transition_function_evaluate
 * (0x10a710) = (function_type=4, t=a2); FST [ESI+0x1e4].
 * Confirmed: assert reads the int bits back from [ESI+0x1e4] for %08X and
 * passes the still-live ST0 as the %f double; line 0x4af. */
void weapon_owner_update(int weapon_handle, int a1, float a2)
{
  uint32_t *weapon_data;
  float value;

  weapon_data = (uint32_t *)object_get_and_verify_type(weapon_handle, 4);
  tag_get(0x77656170, weapon_data[0]);

  *(int16_t *)((int)weapon_data + 0x1e0) = (int16_t)a1;

  value = (*(float *)((int)weapon_data + 0x1e4) = transition_function_evaluate(4, a2));

  if ((*(uint32_t *)&value & 0x7f800000) == 0x7f800000) {
    display_assert(
      csprintf((char *)0x5ab100, "%s: assert_valid_real(0x%08X %f)",
               "weapon->weapon.primary_trigger",
               *(uint32_t *)((int)weapon_data + 0x1e4), (double)value),
      "c:\\halo\\SOURCE\\items\\weapons.c", 0x4af, 1);
    system_exit(-1);
  }
}

/* Begin a magazine reload cycle (0xfc990).
 * If the magazine state is idle (0) or post-reload (2), and the weapon is
 * not in an animation, starts the reload animation and effect. For dual-wield
 * weapons (tag+0x4e2 == 1), computes the animation variant from whether the
 * magazine is one round short of full.
 *
 * Confirmed: magazine_index in AX (register arg), 2 stack args (weapon_handle,
 * param_2). Confirmed: calls object_get_and_verify_type(weapon_handle, 4)
 * twice. Confirmed: calls FUN_000fb370(weapon_obj@<edi>, magazine_index@<si>).
 * Confirmed: calls weapon_set_animation_state(weapon_handle, 0,
 * magazine_index+5 @<bx>). Confirmed: calls weapon_start_effect(mag_def[0x44],
 * 0, 0, weapon_handle@<eax>). Confirmed: calls
 * first_person_weapon_message_from_weapon(weapon_handle, 9 or 10). Confirmed:
 * calls weapon_get_animation_frame(weapon_handle, 0, 7, iVar6). Confirmed:
 * clears bit 3 of weapon_obj[0x1dc] on non-early-exit path.
 */
void FUN_000fc990(int16_t magazine_index, int weapon_handle, int param_2)
{
  char *weapon_obj = (char *)object_get_and_verify_type(weapon_handle, 4);
  int16_t *magazine_state =
    (int16_t *)FUN_000fb370((void *)weapon_obj, magazine_index);
  int tag_data = (int)tag_get(0x77656170, *(int *)weapon_obj);
  char *mag_def = (char *)tag_block_get_element((char *)tag_data + 0x4f0,
                                                (int)magazine_index, 0x70);

  if (*magazine_state == 0 || *magazine_state == 2) {
    int iVar6 = (int)object_get_and_verify_type(weapon_handle, 4);
    if (*(char *)(iVar6 + 0x211) == 0 && *(char *)(iVar6 + 0x235) == 0 &&
        *(char *)(iVar6 + 0x1e8) == 0) {
      if (magazine_state[3] > 0 &&
          magazine_state[4] < *(int16_t *)(mag_def + 0xa)) {
        int16_t anim_variant = -1;
        int16_t frame;
        weapon_set_animation_state(weapon_handle, 0,
                                   (int16_t)(magazine_index + 5));
        weapon_start_effect(*(int *)(mag_def + 0x44), 0, 0, weapon_handle);
        first_person_weapon_message_from_weapon(weapon_handle,
                                                (magazine_state[4] != 0) + 9);

        if (*(int16_t *)(tag_data + 0x4e2) == 1) {
          int diff_is_one =
            ((int)*(int16_t *)(mag_def + 0xa) - (int)magazine_state[4]) == 1;
          if (param_2 == 0) {
            anim_variant = diff_is_one ? 1 : -1;
          } else {
            anim_variant = diff_is_one ? 2 : 0;
          }
        }

        *magazine_state = 1;
        frame = weapon_get_animation_frame(weapon_handle, 0, 7, anim_variant);
        magazine_state[1] = frame;
        magazine_state[2] = frame;
      }
      *(uint32_t *)(weapon_obj + 0x1dc) &= ~0x8u;
    }
  }
}

/* Complete a magazine reload cycle (0xfcaf0).
 * Transfers rounds from unloaded reserve to the loaded count, capped by
 * the tag's rounds-per-reload and maximum-rounds fields. Adjusts reserve
 * for dual-wield. Optionally starts the next reload cycle if rounds remain. */
void FUN_000fcaf0(int weapon_handle, int magazine_index)
{
  char *weapon_obj;
  int16_t *magazine;
  void *tag_data;
  char *mag_def;
  int16_t rounds_unloaded;
  int16_t rounds_reload;
  int rounds_to_load;
  int total;

  weapon_obj = (char *)object_get_and_verify_type(weapon_handle, 4);
  magazine =
    (int16_t *)FUN_000fb370((void *)weapon_obj, (int16_t)magazine_index);
  tag_data = tag_get(0x77656170, *(int *)weapon_obj);
  mag_def = (char *)tag_block_get_element((char *)tag_data + 0x4f0,
                                          (int)(int16_t)magazine_index, 0x70);

  if ((*mag_def & 1) != 0) {
    magazine[4] = 0;
  }

  rounds_unloaded = magazine[3];
  rounds_reload = *(int16_t *)(mag_def + 0x18);
  rounds_to_load = rounds_unloaded;
  if (rounds_reload <= rounds_unloaded) {
    rounds_to_load = rounds_reload;
  }

  total = (int16_t)(magazine[4] + rounds_to_load);
  if (total > *(uint16_t *)(mag_def + 0xa)) {
    total = *(uint16_t *)(mag_def + 0xa);
  }

  if (*(char *)0x5aa892 == 0 && (*(uint8_t *)(weapon_obj + 0x1a4) & 2) != 0) {
    magazine[3] = (int16_t)(rounds_unloaded - total + magazine[4]);
  }

  magazine[4] = (int16_t)total;
  magazine[0] = 2;
  magazine[1] = 0;

  if (magazine[3] > 0 && total < *(int16_t *)(mag_def + 0xa) &&
      (*mag_def & 1) == 0 && (*(uint8_t *)(weapon_obj + 0x1e0) & 0x26) == 0) {
    FUN_000fc990((int16_t)magazine_index, weapon_handle, 0);
  }
}

/* 0xfcf20 — weapon_reset_state
 *
 * Resets all trigger and magazine states on a weapon. Iterates over
 * trigger entries and sets each trigger state byte to 8 and counter
 * to 0. Then iterates over magazine entries, checks for auto-reload
 * condition, and resets magazine state/counter fields.
 *
 * Confirmed: cdecl, 1 stack arg (weapon_handle).
 * Confirmed: CALL object_get_and_verify_type(weapon_handle, 4).
 * Confirmed: CALL tag_get(0x77656170, *obj) for weapon tag.
 * Confirmed: trigger loop: tag+0x4fc count, stride 0x24 (36 bytes)
 *   per trigger entry in weapon data starting at +0x210.
 * Confirmed: assert at weapons.c:0x667 for trigger_index bounds.
 * Confirmed: tag_block_get_element(tag+0x4fc, index, 0x114).
 * Confirmed: stores +0x211 = 8, +0x212 = 0 per trigger.
 * Confirmed: magazine loop: tag+0x4f0 count, stride 12 bytes per
 *   magazine entry starting at weapon_data + 0x258.
 * Confirmed: assert at weapons.c:0x672 for magazine_index bounds.
 * Confirmed: tag_block_get_element(tag+0x4f0, index, 0x70).
 * Confirmed: auto-reload check: magazine[0]==1 and frame*2 < count.
 * Confirmed: calls weapon_get_animation_frame(handle, 0, 7, -1).
 * Confirmed: calls FUN_000fcaf0(handle, magazine_index).
 * Confirmed: resets magazine[0] and magazine[1] to 0.
 */
void weapon_reset_state(int weapon_handle)
{
  uint32_t *weapon_data =
    (uint32_t *)object_get_and_verify_type(weapon_handle, 4);
  int weap_tag = (int)tag_get(0x77656170, weapon_data[0]);
  int magazine_int;
  int mag_tag_ptr;

  /* Reset trigger states */
  int16_t trigger_index = 0;
  if (0 < *(int *)(weap_tag + 0x4fc)) {
    int trigger_count_index = 0;
    do {
      int weap_tag2 = (int)tag_get(0x77656170, weapon_data[0]);
      char *trigger_entry;
      if (trigger_index < 0 ||
          trigger_count_index >= *(int *)(weap_tag2 + 0x4fc)) {
        display_assert("trigger_index>=0 && trigger_index<weapon_definition->"
                       "weapon.triggers.count",
                       "c:\\halo\\SOURCE\\items\\weapons.c", 0x667, 1);
        system_exit(-1);
      }

      /* Compute trigger entry pointer:
       * base + trigger_index * 9 * 4 + 0x210 */
      trigger_entry = (char *)weapon_data + trigger_count_index * 36 + 0x210;

      tag_block_get_element((void *)(weap_tag + 0x4fc), trigger_count_index,
                            0x114);

      trigger_index = trigger_index + 1;
      trigger_count_index = (int)trigger_index;

      *(char *)(trigger_entry + 1) = 8;
      *(int16_t *)(trigger_entry + 2) = 0;
    } while (trigger_count_index < *(int *)(weap_tag + 0x4fc));
  }

  /* Reset magazine states */
  magazine_int = 0;
  mag_tag_ptr = weap_tag + 0x4f0;
  if (0 < *(int *)(weap_tag + 0x4f0)) {
    int mag_count_index = 0;
    do {
      int weap_tag3 = (int)tag_get(0x77656170, weapon_data[0]);
      int16_t *mag_entry;
      if ((int16_t)magazine_int < 0 ||
          mag_count_index >= *(int *)(weap_tag3 + 0x4f0)) {
        display_assert("magazine_index>=0 && magazine_index<weapon_definition->"
                       "weapon.magazines.count",
                       "c:\\halo\\SOURCE\\items\\weapons.c", 0x672, 1);
        system_exit(-1);
      }

      /* Compute magazine entry pointer:
       * base + (magazine_index * 3 + 0x96) * 4 */
      mag_entry =
        (int16_t *)((char *)weapon_data + (mag_count_index * 3 + 0x96) * 4);

      tag_block_get_element((void *)mag_tag_ptr, mag_count_index, 0x70);

      if (mag_entry[0] == 1) {
        int16_t frame = weapon_get_animation_frame(weapon_handle, 0, 7, -1);
        if (mag_entry[1] * 2 < (int)frame) {
          FUN_000fcaf0(weapon_handle, magazine_int);
        }
      }

      magazine_int = magazine_int + 1;
      mag_count_index = (int)(int16_t)magazine_int;
      mag_entry[0] = 0;
      mag_entry[1] = 0;
    } while (mag_count_index < *(int *)(mag_tag_ptr));
  }
}

/* weapon_set_current_amount (0xfd180)
 *
 * Sets weapon ammo level based on a fraction. For battery-based weapons
 * (no magazines or has triggers with charging threshold), stores the charge
 * level at weapon+0x1f0. For magazine-based weapons, sets the loaded rounds
 * in the first magazine and adjusts the total accordingly.
 */
void weapon_set_current_amount(int weapon_handle, float ammo_fraction)
{
  int *weapon = (int *)object_get_and_verify_type(weapon_handle, 4);
  int tag_data = (int)tag_get(0x77656170, *weapon);

  bool is_battery = false;
  int magazine_count = *(int *)(tag_data + 0x4f0);

  if (magazine_count == 0) {
    is_battery = true;
  } else {
    int16_t i;
    int trigger_count = *(int *)(tag_data + 0x4fc);
    for (i = 0; (int)i < trigger_count; i++) {
      char *trigger =
        (char *)tag_block_get_element((int *)(tag_data + 0x4fc), (int)i, 0x114);
      if (*(float *)(trigger + 0xbc) > 0.0f) {
        is_battery = true;
        break;
      }
    }
  }

  if (ammo_fraction < 0.0f) {
    ammo_fraction = 0.0f;
  } else if (ammo_fraction > 1.0f) {
    ammo_fraction = 1.0f;
  }

  if (is_battery) {
    *(float *)((char *)weapon + 0x1f0) = 1.0f - ammo_fraction;
    return;
  }

  if (magazine_count > 0) {
    char *mag_def =
      (char *)tag_block_get_element((int *)(tag_data + 0x4f0), 0, 0x70);
    int check_tag = (int)tag_get(0x77656170, *weapon);
    int16_t max_rounds;
    int16_t new_loaded;
    int16_t current_loaded;
    if (*(int *)(check_tag + 0x4f0) < 1) {
      display_assert("magazine_index>=0 && "
                     "magazine_index<weapon_definition->weapon.magazines.count",
                     "c:\\halo\\SOURCE\\items\\weapons.c", 0x672, 1);
      system_exit(-1);
    }

    max_rounds = *(int16_t *)(mag_def + 0xa);
    new_loaded = (int16_t)(int)((float)max_rounds * ammo_fraction);
    current_loaded = *(int16_t *)((char *)weapon + 0x260);
    *(int16_t *)((char *)weapon + 0x260) = new_loaded;
    *(int16_t *)((char *)weapon + 0x25e) += (new_loaded - current_loaded);
  }
}

/* weapon_activate — no binary address assigned.
 * Initializes a weapon after it becomes the active weapon for a unit.
 * Resets trigger/magazine state, sets the ready animation (state 9),
 * fires the initial effect from the weapon triggers tag block, and
 * stores the ready animation frame count into the weapon data. */
void weapon_activate(int weapon_handle)
{
  uint32_t *weapon_data =
    (uint32_t *)object_get_and_verify_type(weapon_handle, 4);
  int tag_data = (int)tag_get(0x77656170, weapon_data[0]);
  int16_t frame;

  weapon_reset_state(weapon_handle);
  weapon_set_animation_state(weapon_handle, 1, 9);
  first_person_weapon_message_from_weapon(weapon_handle, 0xc);
  weapon_start_effect(*(int *)(tag_data + 0x348), 0, 0, weapon_handle);

  frame = weapon_get_animation_frame(weapon_handle, 0, 10, -1);
  *(int16_t *)((int)weapon_data + 0x1ea) = frame;
}

/* weapon_try_place — no binary address assigned.
 * Attempts to place (holster/put-away) the current weapon. If flag is
 * zero and the weapon has active triggers or animations, the placement
 * is rejected. On success, sets the put-away animation (state 10),
 * resets trigger/magazine state, disposes any attached effect, and
 * starts the put-away effect sequence. */
bool weapon_try_place(int weapon_handle, int flag)
{
  volatile char result;
  uint32_t *weapon_data;

  weapon_data = (uint32_t *)object_get_and_verify_type(weapon_handle, 4);
  tag_get(0x77656170, weapon_data[0]);

  result = 0;
  if ((char)flag != 0 || !(char)weapon_has_activity(weapon_handle)) {
    if ((char)weapon_set_animation_state(weapon_handle, flag, 10)) {
      *(int16_t *)((int)weapon_data + 0x1e0) = 0;
      weapon_reset_state(weapon_handle);

      if (*(int *)((int)weapon_data + 0x274) != -1) {
        effect_delete(*(int *)((int)weapon_data + 0x274));
        *(int *)((int)weapon_data + 0x274) = -1;
      }

      first_person_weapon_message_from_weapon(weapon_handle, 0xb);
      return true;
    }
  }

  return result;
}

/* weapon_aim (0xfd400) — weapon_try_and_fire_projectile
 *
 * Attempts to fire a projectile for a given weapon trigger. Validates the
 * weapon object (type 4), resolves the 'weap' tag, then validates the
 * trigger_index is in range [0, triggers.count). If in range, fetches the
 * trigger block element (0x114 bytes each) at [weap_tag+0x4fc], reads the
 * 'proj' tag reference from [trigger_elem+0xa0], and resolves it via
 * tag_get('proj',...). Passes the resolved projectile tag along with the
 * remaining parameters to projectile_aim (projectile fire dispatcher), then
 * validates param_6 as a valid 3D unit normal using valid_real_normal3d. If
 * the vector is invalid, formats an assert message via csprintf and calls
 * display_assert + system_exit. Returns true (1) on success, false (0) if
 * trigger_index is out of range.
 *
 * Line number evidence: assert at line 0x515 (1301) in weapons.c.
 *
 * Confirmed: object_get_and_verify_type(weapon_handle, 4) at 0xfd40c.
 * Confirmed: tag_get(0x77656170, weapon_data[0]) at 0xfd41b; ADD ESP,0x10
 *   cleans both preceding calls (object_get + tag_get = 4 args).
 * Confirmed: TEST SI,SI / JL at 0xfd42a–0xfd42d guards trigger_index < 0.
 * Confirmed: MOV EDX,[EBX+0x4fc] / ADD EBX,0x4fc at 0xfd433–fd439 =
 *   trigger block count and pointer.
 * Confirmed: MOVSX ECX,SI / CMP ECX,EDX / JGE at 0xfd43f–0xfd447 guards
 *   trigger_index >= triggers.count.
 * Confirmed: CALL 0xfb320 at 0xfd44d is a debug assertion using caller ESI/EDI;
 *   not representable as a plain C call — elided (bounds already checked
 * above). Confirmed: tag_block_get_element(EBX, ECX, 0x114) at 0xfd45c where
 * EBX= &triggers_block ([weap_tag+0x4fc] after ADD EBX,0x4fc). Confirmed: MOV
 * EAX,[EAX+0xa0] at 0xfd475 reads proj tag reference from trigger element.
 * Confirmed: tag_get(0x70726f6a, proj_ref, ...) at 0xfd496 with 14 pushes;
 *   ADD ESP,0x8 cleans only tag_get's own 2 args.
 * Confirmed: projectile_aim called with proj_tag + 12 stale stack args at
 * 0xfd49f; receives proj_tag as arg1, then param_3..param_9 interleaved with
 * 0-padding. Confirmed: valid_real_normal3d(param_6) at 0xfd4a5 (PUSH ESI where
 * ESI=[EBP+0x1c]). Confirmed: csprintf(&DAT_005ab100, ...) assert-format at
 * 0xfd4e2; float args loaded FLD+FSTP double via MSVC push-then-fstp pattern.
 * Confirmed: CALL display_assert at 0xfd4eb; CALL system_exit at 0xfd4f2.
 * Confirmed: return 1 at 0xfd4fa; return 0 (XOR AL,AL) at 0xfd428 on early
 * exit.
 */
bool weapon_aim(int weapon_handle, int16_t trigger_index, void *param_3,
                void *param_4, int param_5, float *param_6, int param_7,
                void *param_8, void *param_9)
{
  int *weapon_data = (int *)object_get_and_verify_type(weapon_handle, 4);
  char *weap_tag = (char *)tag_get(0x77656170, weapon_data[0]);
  int trigger_count;
  int trig_idx;
  char *trigger_elem;
  int proj_ref;
  void *proj_tag;

  if (trigger_index < 0)
    return false;

  trigger_count = *(int *)(weap_tag + 0x4fc);
  trig_idx = (int)trigger_index;
  if (trig_idx >= trigger_count)
    return false;

  /* FUN_000fb320 assertion (trigger bounds) elided: reads caller ESI/EDI,
   * not representable as a plain C call. Bounds already checked above. */

  trigger_elem =
    (char *)tag_block_get_element((void *)(weap_tag + 0x4fc), trig_idx, 0x114);
  proj_ref = *(int *)(trigger_elem + 0xa0);

  /* tag_get + projectile_aim share a single stack cleanup.
   * Disassembly-verified push sequence at 0xfd46d–0xfd495 (14 pushes,
   * right-to-left): [P1] ECX=param_9, [P2] EDX=param_8, [P3] ECX=param_7, [P4]
   * 0, [P5] ESI=param_6, [P6] EDX=param_5, [P7-P10] 0,0,0,0, [P11] ECX=param_4
   * (ECX reloaded via MOV ECX,[EBP+0x14] at 0xfd47c), [P12] EDX=param_3, [P13]
   * EAX=proj_ref, [P14] 'proj' tag_get (at 0xfd496) uses P14+'proj' and
   * P13=proj_ref; ADD ESP,8 cleans them. PUSH EAX (proj_tag) at 0xfd49e, then
   * CALL projectile_aim at 0xfd49f: arg2=P12=param_3(origin),
   * arg3=P11=param_4(target), arg8=P6=param_5, arg9=P5=param_6(aim_vector),
   * arg11=P3=param_7, arg12=P2=param_8, arg13=P1=param_9. */
  proj_tag = tag_get(0x70726f6a, proj_ref);
  ((void (*)(void *, void *, void *, int, int, int, int, int, float *, int, int,
             void *, void *))0xf84d0)(proj_tag, param_3, param_4, 0, 0, 0, 0,
                                      param_5, param_6, 0, param_7, param_8,
                                      param_9);

  if (!((bool (*)(float *))0x21fb0)(param_6)) {
    display_assert(csprintf((char *)0x5ab100,
                            "%s: assert_valid_real_normal3d(%f, %f, %f)",
                            "result_aim_vector", (double)param_6[0],
                            (double)param_6[1], (double)param_6[2]),
                   "c:\\halo\\SOURCE\\items\\weapons.c", 0x515, 1);
    system_exit(-1);
  }

  return true;
}
