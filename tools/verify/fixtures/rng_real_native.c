/* Native binary regression test; all possible high 16 bits of the next seed.
 * Driven by tools/verify/rng_real_native.py on Linux i386/x86-64. */
typedef unsigned int u32;
typedef union { float f; u32 u; } single;
typedef union { long double f; unsigned char bytes[12]; } extended;
extern void run_reference(u32 *, extended *);
extern void run_before(u32 *, extended *);
extern void run_after(u32 *, extended *);
extern void control(u32);
extern void output(const char *, u32);
static void number(u32 n) {
    char b[12]; u32 i = 12;
    do { b[--i] = '0' + n % 10; n /= 10; } while (n);
    output(b + i, 12 - i); output(" ", 1);
}
int main(void) {
    u32 modes[2] = {0x37f, 0x27f};
    u32 mode, n, i, sr, sb, sa, b32, b80, a32, a80, db, da;
    extended r, b, a; single rf, bf, af;
    for (mode = 0; mode < 2; ++mode) {
        b32 = b80 = a32 = a80 = 0;
        for (n = 0; n < 65536; ++n) {
            sr = sb = sa = ((n << 16) - 0x3c6ef35fU) * 0xfee058c5U;
            control(modes[mode]); run_reference(&sr, &r);
            control(modes[mode]); run_before(&sb, &b);
            control(modes[mode]); run_after(&sa, &a);
            if (sr != sb || sr != sa || sr != (n << 16)) return 2;
            rf.f = r.f; bf.f = b.f; af.f = a.f;
            b32 += rf.u != bf.u; a32 += rf.u != af.u;
            db = da = 0;
            for (i = 0; i < 10; ++i) { db |= r.bytes[i] != b.bytes[i]; da |= r.bytes[i] != a.bytes[i]; }
            b80 += db; a80 += da;
        }
        number(modes[mode]); number(b32); number(b80); number(a32); number(a80); output("\n", 1);
    }
    return 0;
}
