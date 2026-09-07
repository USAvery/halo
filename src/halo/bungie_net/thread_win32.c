/*
 * bungie_net/thread_win32.c — lightweight thread pool for Xbox
 * XBE source: c:\halo\SOURCE\bungie_net\common\thread_win32.c
 *
 * Re-implemented functions (by XBE address, ascending):
 *   0x81170  FUN_00081170 (from public_key_crypt.c — same COFF object)
 *   0x81250  FUN_00081250 (from public_key_crypt.c — same COFF object)
 *   0x81630  thread_new
 *   0x81720  thread_is_done
 *   0x81770  thread_close
 *   0x81870  mutex_acquire (take_mutex)
 *   0x818d0  mutex_release (release_mutex)
 */

#include "common.h"

#define MAXIMUM_THREADS 32

/* XDK XAPI imports — called by name through kb.json __stdcall decls
 * (CreateThread 0x1cfd8c, SetThreadPriority 0x1cf999, ResumeThread 0x1cfaec,
 * CloseHandle 0x1cf900, GetExitCodeThread 0x1cfbbd, WaitForSingleObject
 * 0x1d0336, ReleaseMutex 0x1d0099). The former raw fn-pointer casts hid the
 * calling convention from kb.json audits (lift-learnings §30). */

/* WaitForSingleObject return codes */
#define WAIT_OBJECT_0 0x00
#define WAIT_ABANDONED 0x80

#define STILL_ACTIVE 0x103

typedef struct {
  int handle;
  char in_use;
  char pad[3];
} thread_slot_t;

#define g_thread_slots ((thread_slot_t *)0x334990)

/*
 * FUN_00081170 — generate the 2-dword modulus/base/generator triple.
 *
 * TU: c:\halo\SOURCE\bungie_net\common\public_key_crypt.c (confirmed by the
 * __FILE__ string at 0x265da0 pushed by both asserts below); it links into
 * the same COFF object as the thread_win32 routines.
 *
 * For each of the two dwords: draw p[i] = rand(0xffff) * rand(0xffff) + 2
 * until it is >= 0xffffff, then draw x[i] in [0xff, p[i]-2] and
 * g[i] in [0xff, p[i]-1].
 *
 * Confirmed: assert "x->dwords[i] < (p->dwords[i] - 2)" at line 0xa2,
 * assert "g->dwords[i] < (p->dwords[i] - 1)" at line 0xa3.
 * Confirmed: FUN_00080eb0 called twice with 0xffff; FUN_00081410 called as
 * (0xff, p[i]-2) then (0xff, p[i]-1) — first PUSH is the last argument.
 * Confirmed: the retry compare is JC (unsigned) against 0xffffff.
 * Unknown: the semantic names of the function and of FUN_00080eb0 /
 * FUN_00081410 (no string or symbol evidence); parameter names p/x/g come
 * from the assert strings.
 */
void FUN_00081170(unsigned int *p, unsigned int *x, unsigned int *g)
{
  int i;

  for (i = 0; i < 2; i++) {
    do {
      p[i] = FUN_00080eb0(0xffff) * FUN_00080eb0(0xffff) + 2;
    } while (p[i] < 0xffffff);

    x[i] = (unsigned int)FUN_00081410(0xff, (int)(p[i] - 2));
    g[i] = (unsigned int)FUN_00081410(0xff, (int)(p[i] - 1));

    if (x[i] >= p[i] - 2) {
      display_assert("x->dwords[i] < (p->dwords[i] - 2)",
                     "c:\\halo\\SOURCE\\bungie_net\\common\\public_key_crypt.c",
                     0xa2, 1);
      system_exit(-1);
    }
    if (g[i] >= p[i] - 1) {
      display_assert("g->dwords[i] < (p->dwords[i] - 1)",
                     "c:\\halo\\SOURCE\\bungie_net\\common\\public_key_crypt.c",
                     0xa3, 1);
      system_exit(-1);
    }
  }
}

/*
 * 0x81250 - compute a Diffie-Hellman public key from (p, x, g) and dump all
 * four 64-bit values through error() at severity 2.
 *
 * From public_key_crypt.c (same COFF object as FUN_00081170 above), cdecl
 * with four stack params: p@[EBP+8], x@[EBP+0xc], g@[EBP+0x10],
 * public_key@[EBP+0x14].
 *
 * Confirmed: the loop runs exactly 2 iterations ([EBP-8] initialised to 2,
 * DEC/JNZ). The original walks x with a single cursor and reaches the other
 * three arrays through precomputed byte deltas (p-x, g-x, public_key-x)
 * folded into MOV [EDX+ECX*1]; that is a strength-reduction of the plain
 * per-array index used here.
 * Confirmed by register order at 0x81290-0x81298: EDI = g[i], EBX = x[i],
 * ESI = p[i], matching FUN_00081090's declared p@esi / x@ebx / g@edi.
 * Confirmed: EAX from that call is stored to public_key[i] (MOV
 * [EDX+ECX*1],EAX at 0x812a3) -- FUN_00081090 tail-calls FUN_00080fc0,
 * which returns the low dword of its accumulator in EAX.
 * Confirmed: the error() varargs are pushed high-to-low as public_key[1],
 * public_key[0], g[1], g[0], x[1], x[0], p[1], p[0], format, 2 -- first
 * PUSH is the last argument, so the printed order matches the format
 * string at 0x265e2c (read verbatim from the pristine XBE .rdata).
 * Unknown: the semantic name of this function and of FUN_00081090 (no
 * string or symbol evidence); parameter names come from the format string.
 */
void FUN_00081250(unsigned int *p, unsigned int *x, unsigned int *g,
                  unsigned int *public_key)
{
  int i;
  unsigned int result[2];

  for (i = 0; i < 2; i++) {
    result[i] = FUN_00081090(p[i], x[i], g[i]);
    public_key[i] = result[i];
  }

  error(2, "p= %8lX%8lX\nx= %8lX%8lX\ng= %8lX%8lX\npublic key= %8lX%8lX\n\n",
        p[0], p[1], x[0], x[1], g[0], g[1], public_key[0], public_key[1]);
}

/*
 * thread_new — allocate a thread slot and create an Xbox thread.
 *
 * Searches the 32-slot pool for an unused entry, creates a suspended thread
 * via CreateThread, sets its priority based on priority_flags bits, then
 * resumes it. Returns true on success.
 *
 * Confirmed: assert "function" at line 0x6b, "thread_reference" at line 0x6c.
 * Confirmed: CREATE_SUSPENDED (0x4) flag, stack size 0x4000.
 * Confirmed: priority_flags bit 0x2 = below-normal (-1), bit 0x4 = above-normal
 * (+1).
 */
bool thread_new(int priority_flags, void *function, int param,
                void **thread_reference)
{
  void **ref = thread_reference;
  thread_slot_t *slot = NULL;
  int i;
  int handle;
  int priority;

  if (function == NULL) {
    display_assert("function",
                   "c:\\halo\\SOURCE\\bungie_net\\common\\thread_win32.c", 0x6b,
                   1);
    system_exit(-1);
  }
  if (ref == NULL) {
    display_assert("thread_reference",
                   "c:\\halo\\SOURCE\\bungie_net\\common\\thread_win32.c", 0x6c,
                   1);
    system_exit(-1);
  }

  for (i = 0; i < MAXIMUM_THREADS; i++) {
    if (g_thread_slots[i].in_use == 0) {
      slot = &g_thread_slots[i];
      slot->handle = 0;
      slot->in_use = 1;
      break;
    }
  }

  if (slot != NULL) {
    handle =
      (int)CreateThread(NULL, 0x4000, function, (void *)param, 4, (int *)&function);
    slot->handle = handle;
    if (handle != 0) {
      priority = 0;
      if ((priority_flags & 2) != 0) {
        priority = -1;
      } else if ((priority_flags & 4) != 0) {
        priority = 1;
      }
      if (SetThreadPriority(handle, priority) != 0 &&
          ResumeThread(slot->handle) != -1) {
        *ref = slot;
        return true;
      }
      CloseHandle(slot->handle);
      *ref = NULL;
      return false;
    }
  }

  *ref = slot;
  return false;
}

/*
 * thread_is_done — check whether a thread has finished executing.
 *
 * Calls GetExitCodeThread and returns true if the thread exited (i.e. the
 * exit code is not STILL_ACTIVE).
 *
 * Confirmed: assert "thread_reference" at line 0x98.
 * Confirmed: compares exit code against 0x103 (STILL_ACTIVE).
 */
bool thread_is_done(void *thread_reference)
{
  thread_slot_t *slot = (thread_slot_t *)thread_reference;
  bool is_done = false;
  int exit_code;

  if (slot == NULL) {
    display_assert("thread_reference",
                   "c:\\halo\\SOURCE\\bungie_net\\common\\thread_win32.c", 0x98,
                   1);
    system_exit(-1);
  }

  if (GetExitCodeThread(slot->handle, &exit_code) != 0) {
    if (exit_code != STILL_ACTIVE) {
      is_done = true;
    }
  }

  return is_done;
}

/*
 * thread_close — close a thread handle and release its slot.
 *
 * Confirmed: assert "thread_reference" at line 0xa8.
 * Confirmed: assert "thread_reference->in_use" at line 0xa9.
 * Confirmed: calls CloseHandle, then zeroes handle and in_use.
 */
void thread_close(void *thread_reference)
{
  thread_slot_t *slot = (thread_slot_t *)thread_reference;

  if (slot == NULL) {
    display_assert("thread_reference",
                   "c:\\halo\\SOURCE\\bungie_net\\common\\thread_win32.c", 0xa8,
                   1);
    system_exit(-1);
  }
  if (slot->in_use == 0) {
    display_assert("thread_reference->in_use",
                   "c:\\halo\\SOURCE\\bungie_net\\common\\thread_win32.c", 0xa9,
                   1);
    system_exit(-1);
  }

  CloseHandle(slot->handle);
  slot->handle = 0;
  slot->in_use = 0;
}

/*
 * take_mutex — acquire a mutex with a timeout.
 *
 * Calls WaitForSingleObject(*mutex_reference, timeout_ms). Returns true if the
 * wait succeeded (WAIT_OBJECT_0 = 0) or the mutex was abandoned (WAIT_ABANDONED
 * = 0x80). Returns false on timeout or any other error.
 *
 * Confirmed: assert "mutex_reference" at line 0xd3.
 * Confirmed: WaitForSingleObject at 0x1d0336; success codes 0x00 and 0x80.
 */
bool take_mutex(int *mutex_reference, int timeout_ms)
{
  bool success = false;
  int result;

  if (mutex_reference == NULL) {
    display_assert("mutex_reference",
                   "c:\\halo\\SOURCE\\bungie_net\\common\\thread_win32.c", 0xd3,
                   1);
    system_exit(-1);
  }
  result = WaitForSingleObject(*mutex_reference, timeout_ms);
  if (result == WAIT_OBJECT_0 || result == WAIT_ABANDONED) {
    success = true;
  }
  return success;
}

/*
 * release_mutex — release a mutex.
 *
 * Calls ReleaseMutex(*mutex_reference) via the XDK thunk at 0x1d0099
 * (NtReleaseMutant wrapper). Returns void.
 *
 * Confirmed: assert "mutex_reference" at line 0xe6.
 * Confirmed: ReleaseMutex at 0x1d0099.
 */
void release_mutex(int *mutex_reference)
{
  if (mutex_reference == NULL) {
    display_assert("mutex_reference",
                   "c:\\halo\\SOURCE\\bungie_net\\common\\thread_win32.c", 0xe6,
                   1);
    system_exit(-1);
  }
  ReleaseMutex(*mutex_reference);
}
