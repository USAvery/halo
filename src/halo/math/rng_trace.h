/* RNG draw trace -- DIAGNOSTIC INSTRUMENTATION, NOT recovered game code.
 *
 * Compiled only when HALO_RNG_TRACE is defined (tools/build/build.py
 * --rng-trace).  Used to find which lifted function desyncs a system-link game
 * against a pristine build-2276 instance: both sides record every draw from the
 * GLOBAL random seed into a ring buffer, and tools/xbox/rng_trace_dump.py
 * diffs the two traces.  See docs/rng-trace.md.
 *
 * Every source file that includes this header inserts its instrumentation
 * behind a `#line` directive that restores the original physical line
 * numbering: assert_halt() stamps __LINE__ into the binary, so an unrestored
 * line shift would change codegen (and the VC71 score) of every function below
 * the insertion point.  With the flag off the emitted objects are
 * byte-identical to the uninstrumented build.
 */
#ifndef HALO_MATH_RNG_TRACE_H
#define HALO_MATH_RNG_TRACE_H

#define RNG_TRACE_MAGIC             0x54474e52u /* 'R','N','G','T' little-endian */
#define RNG_TRACE_VERSION           1u
#define RNG_TRACE_CAPACITY          65536u      /* must stay a power of two */
#define RNG_TRACE_GLOBAL_SEED_ADDR  0x46e3f4u   /* global seed; 0x46e3f8 is local */

/* Event kind, packed into the top 8 bits of rng_trace_record_t.tick.
 * The trailing "steps" count is how many LCG iterations the event advances the
 * global seed; rng_trace_dump.py uses it to verify draw-sequence continuity
 * (seed[n+1] == lcg(seed[n]) applied `steps` times). */
#define RNG_TRACE_KIND_REAL          0u /* random_math_real            steps 1 */
#define RNG_TRACE_KIND_REAL_RANGE    1u /* random_real_range           steps 1 */
#define RNG_TRACE_KIND_SEED_STEP     2u /* random_seed_step            steps 1 */
#define RNG_TRACE_KIND_RANGE         3u /* random_range                steps 1 */
#define RNG_TRACE_KIND_DIRECTION3D   4u /* random_seed_get_direction3d steps 1 */
#define RNG_TRACE_KIND_ORIENTATION   5u /* seed_random_orientation     steps 3 */
#define RNG_TRACE_KIND_DIR3D_INLINE  6u /* random_direction3d          steps 1 */
#define RNG_TRACE_KIND_SET_SEED      7u /* set_random_seed             reseed   */
#define RNG_TRACE_KIND_NET_SET_SEED  8u /* network_game_set_random_seed  info   */
#define RNG_TRACE_KIND_MAP_SEED      9u /* game_initialize_for_new_map   reseed */
#define RNG_TRACE_KIND_PERIODIC_SEED 10u /* periodic_functions_initialize reseed */

/* 16 bytes. */
typedef struct {
  uint32_t tick;        /* (kind << 24) | (game tick & 0x00ffffff) */
  uint32_t seed_before; /* global seed BEFORE the draw.  For the two setter
                         * kinds this is instead the seed value being
                         * installed, i.e. the post-event state. */
  uint32_t caller;      /* __builtin_return_address(0) of the primitive */
  uint32_t caller2;     /* reserved (always 0): the EBP-chain hop was dropped
                         * because an unframed caller can leave an aligned
                         * garbage saved-EBP that faults on dereference */
} rng_trace_record_t;

typedef struct {
  uint32_t magic;       /* RNG_TRACE_MAGIC once the first record is written */
  uint32_t version;
  uint32_t capacity;
  uint32_t write_index; /* total records ever written; ring slot = idx % cap */
  rng_trace_record_t records[RNG_TRACE_CAPACITY];
} rng_trace_buffer_t;

/* Defined in src/halo/math/random_math.c; exported so tools/xbox
 * symbolization can resolve its runtime VA from the appended PE exports. */
__declspec(dllexport) extern rng_trace_buffer_t halo_rng_trace;

/* Records one event.  Ignores every seed pointer other than the global seed. */
void rng_trace_note(const void *seed, unsigned int kind,
                    unsigned int seed_before, void *caller, void *frame);

/* Call from inside the primitive itself so that __builtin_return_address(0)
 * names the game function that asked for the draw. */
#define RNG_TRACE(seed_ptr, kind, before)                                  \
  rng_trace_note((const void *)(seed_ptr), (kind), (unsigned int)(before), \
                 __builtin_return_address(0), __builtin_frame_address(0))

#endif /* HALO_MATH_RNG_TRACE_H */
