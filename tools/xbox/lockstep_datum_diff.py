#!/usr/bin/env python3
"""Compare object datums on the two lockstep xemu guests.

Monitors (HMP, virtual `x` — do not `stop` either VM):
  host   (pristine 10.0.0.24)  tcp:127.0.0.1:4446
  client (ours     10.0.0.21)  tcp:127.0.0.1:4444

    python3 tools/xbox/lockstep_datum_diff.py
    python3 tools/xbox/lockstep_datum_diff.py --watch
"""
from __future__ import annotations

import re
import socket
import struct
import subprocess
import sys
import time

HOST_HMP = ("127.0.0.1", 4446)
CLIENT_HMP = ("127.0.0.1", 4444)

OBJECT_TABLE_PTR = 0x5A8D50
GAME_TIME_GLOBALS_PTR = 0x45708C
RNG_SEED = 0x46E3F4
DATA_T_MAGIC = 0x64407440
DATA_T_HDR_LEN = 0x38
OBJECT_BODY_PTR_OFF = 0x08
HEAP_LO, HEAP_HI = 0x80000000, 0x84000000
KIND_NAME = {0: "biped", 1: "vehicle", 2: "weapon", 3: "equipment",
             4: "garbage", 5: "projectile"}

DUMP_LINE = re.compile(
    r"^\s*[0-9a-fA-F]+:\s*((?:0x[0-9a-fA-F]+\s*)+)$"
)


class HMP:
    def __init__(self, host, port, timeout=3.0):
        self.port = port
        self.s = socket.socket()
        self.s.settimeout(timeout)
        self.s.connect((host, port))
        self.buf = b""
        self._until_prompt()

    def _until_prompt(self, timeout=None):
        prompt = b"(qemu) "
        if timeout is None:
            timeout = self.s.gettimeout() or 3.0
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                chunk = self.s.recv(8192)
            except socket.timeout:
                break
            if not chunk:
                break
            self.buf += chunk
            idx = self.buf.rfind(prompt)
            if idx >= 0:
                out = self.buf[:idx]
                self.buf = self.buf[idx + len(prompt):]
                return out
        raise TimeoutError("HMP :%d no prompt (%r)" % (self.port, self.buf[:120]))

    def cmd(self, line):
        self.s.sendall(line.encode("ascii") + b"\n")
        return self._until_prompt().decode("latin1", errors="replace")

    def read_mem(self, addr, length):
        nwords = (length + 3) // 4
        # cap a single dump so the monitor stays responsive
        if nwords > 512:
            out = bytearray()
            off = 0
            while off < length:
                chunk = min(length - off, 2048)
                out += self.read_mem(addr + off, chunk)
                off += chunk
            return bytes(out[:length])
        raw = self.cmd("x /%dxw 0x%x" % (nwords, addr))
        words = []
        for line in raw.splitlines():
            m = DUMP_LINE.match(line.strip())
            if not m:
                continue
            for tok in m.group(1).split():
                words.append(int(tok, 16) & 0xffffffff)
        if len(words) < nwords:
            raise RuntimeError("short HMP read @0x%x want %d words got %d" % (
                addr, nwords, len(words)))
        return b"".join(struct.pack("<I", w) for w in words[:nwords])[:length]

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def u32(b, off=0):
    return struct.unpack_from("<I", b, off)[0]


def f32(b, off=0):
    return struct.unpack_from("<f", b, off)[0]


def snapshot_fast(hmp):
    """Tick + seed + live count only. Cheap enough to poll during lockstep."""
    tick_ptr = u32(hmp.read_mem(GAME_TIME_GLOBALS_PTR, 4))
    tick = -1
    if HEAP_LO <= tick_ptr < HEAP_HI:
        tick = u32(hmp.read_mem(tick_ptr + 0x0C, 4))
    seed = u32(hmp.read_mem(RNG_SEED, 4))
    tbl = u32(hmp.read_mem(OBJECT_TABLE_PTR, 4))
    n_live = 0
    error = None
    if not (HEAP_LO <= tbl < HEAP_HI):
        error = "no object table"
    else:
        hdr = hmp.read_mem(tbl, DATA_T_HDR_LEN)
        magic = u32(hdr, 0x28)
        max_c, es = struct.unpack_from("<hh", hdr, 0x20)
        cur = struct.unpack_from("<h", hdr, 0x2E)[0]
        if magic != DATA_T_MAGIC:
            error = "bad magic 0x%08x" % magic
        else:
            n_live = cur if 0 < cur <= max_c else 0
    return {"port": hmp.port, "tick": tick, "seed": seed,
            "n_live": n_live, "error": error}


def snapshot(hmp, kinds=None):
    tick_ptr = u32(hmp.read_mem(GAME_TIME_GLOBALS_PTR, 4))
    tick = -1
    if HEAP_LO <= tick_ptr < HEAP_HI:
        tick = u32(hmp.read_mem(tick_ptr + 0x0C, 4))
    seed = u32(hmp.read_mem(RNG_SEED, 4))
    tbl = u32(hmp.read_mem(OBJECT_TABLE_PTR, 4))
    if not (HEAP_LO <= tbl < HEAP_HI):
        return {"port": hmp.port, "tick": tick, "seed": seed,
                "error": "no object table"}
    hdr = hmp.read_mem(tbl, DATA_T_HDR_LEN)
    magic = u32(hdr, 0x28)
    max_c, es = struct.unpack_from("<hh", hdr, 0x20)
    cur = struct.unpack_from("<h", hdr, 0x2E)[0]
    data = u32(hdr, 0x34)
    if magic != DATA_T_MAGIC or es <= 0 or not (HEAP_LO <= data < HEAP_HI):
        return {"port": hmp.port, "tick": tick, "seed": seed,
                "error": "bad magic 0x%08x" % magic}
    n = cur if 0 < cur <= max_c else max_c
    n = min(n, 512)
    blob = hmp.read_mem(data, n * es)
    objs = []
    for slot in range(n):
        rec = blob[slot * es:(slot + 1) * es]
        salt = struct.unpack_from("<H", rec, 0)[0]
        if salt == 0:
            continue
        kind = rec[3] if len(rec) > 3 else 0xFF
        if kinds is not None and kind not in kinds:
            continue
        body = u32(rec, OBJECT_BODY_PTR_OFF) if len(rec) >= 12 else 0
        handle = (salt << 16) | slot
        pos = (None, None, None)
        if HEAP_LO <= body < HEAP_HI:
            raw = hmp.read_mem(body + 0x50, 12)
            pos = (f32(raw, 0), f32(raw, 4), f32(raw, 8))
        objs.append({"handle": handle, "kind": kind, "pos": pos})
    return {"port": hmp.port, "tick": tick, "seed": seed, "magic_ok": True,
            "n_live": len(objs), "objects": objs}


def fmt_pos(p):
    if p[0] is None:
        return "----"
    return "%.4f,%.4f,%.4f" % p


def diff_pair(a, b):
    am = {o["handle"]: o for o in a.get("objects", [])}
    bm = {o["handle"]: o for o in b.get("objects", [])}
    only_a = sorted(set(am) - set(bm))
    only_b = sorted(set(bm) - set(am))
    diffs = []
    for h in sorted(set(am) & set(bm)):
        pa, pb = am[h]["pos"], bm[h]["pos"]
        ka, kb = am[h]["kind"], bm[h]["kind"]
        pos_diff = pa != pb and None not in pa and None not in pb
        if ka != kb or pos_diff:
            dx = (pb[0] - pa[0]) if pos_diff else 0.0
            dy = (pb[1] - pa[1]) if pos_diff else 0.0
            dz = (pb[2] - pa[2]) if pos_diff else 0.0
            diffs.append((h, ka, kb, pa, pb, dx, dy, dz))
    return only_a, only_b, diffs


def print_snap(label, s):
    err = s.get("error")
    print("%s :%s tick=%s seed=%08x live=%s%s" % (
        label, s.get("port"), s.get("tick"), s.get("seed", 0) & 0xffffffff,
        s.get("n_live"), (" ERR " + err) if err else ""))


def print_diff(a, b):
    only_a, only_b, diffs = diff_pair(a, b)
    print("only host %d  only client %d  both %d" % (
        len(only_a), len(only_b),
        len(set(o["handle"] for o in a.get("objects", []))
            & set(o["handle"] for o in b.get("objects", [])))))
    if only_a[:8]:
        print("  host-only", ["%08x" % h for h in only_a[:8]])
    if only_b[:8]:
        print("  client-only", ["%08x" % h for h in only_b[:8]])
    for row in diffs[:16]:
        h, ka, kb, pa, pb, dx, dy, dz = row
        print("DIFF %08x kind %s/%s H(%s) C(%s) d=%.4f,%.4f,%.4f" % (
            h, KIND_NAME.get(ka, ka), KIND_NAME.get(kb, kb),
            fmt_pos(pa), fmt_pos(pb), dx, dy, dz))
    print("pos/kind diffs:", len(diffs))
    print("tick delta C-H:", (b.get("tick") or 0) - (a.get("tick") or 0))
    print("seed match:", a.get("seed") == b.get("seed"))
    return only_a, only_b, diffs


def main():
    watch = "--watch" in sys.argv
    hh = HMP(*HOST_HMP)
    ch = HMP(*CLIENT_HMP)
    try:
        if watch:
            print("HMP host :4446  client :4444")
            print("watching tick+seed; STOP both on first seed mismatch")
            deadline = time.monotonic() + 900
            last = (-1, -1)
            armed = False
            saw_load = False
            mismatch_streak = 0
            log_path = "artifacts/rng_trace/ds31_watch.jsonl"
            while time.monotonic() < deadline:
                try:
                    a = snapshot_fast(hh)
                    b = snapshot_fast(ch)
                except Exception as exc:
                    print("retry:", exc)
                    time.sleep(0.2)
                    continue
                ta, tb = a.get("tick") or 0, b.get("tick") or 0
                na, nb = a.get("n_live") or 0, b.get("n_live") or 0
                seed_ok = a.get("seed") == b.get("seed")
                if (ta, tb) != last:
                    print("tick H=%s C=%s live %s/%s seed %s" % (
                        ta, tb, na, nb,
                        "match" if seed_ok else "MISMATCH"))
                    last = (ta, tb)
                    try:
                        with open(log_path, "a") as fh:
                            fh.write("%s %s %08x %s %s %08x\n" % (
                                ta, na, a.get("seed", 0) & 0xffffffff,
                                tb, nb, b.get("seed", 0) & 0xffffffff))
                    except OSError:
                        pass
                loading = (ta < 15 and tb < 15)
                if loading:
                    if not saw_load:
                        print("armed: both ticks < 15 (fresh load)")
                    saw_load = True
                    armed = True
                in_game = (not a.get("error") and not b.get("error")
                           and ta >= 8 and tb >= 8 and na >= 4 and nb >= 4
                           and abs(ta - tb) <= 40)
                if in_game and seed_ok and not armed:
                    print("armed: seeds still match in gameplay")
                    armed = True
                if in_game and seed_ok:
                    mismatch_streak = 0
                if in_game and not seed_ok and not armed:
                    time.sleep(0.15)
                    continue
                if in_game and not seed_ok:
                    mismatch_streak += 1
                    if mismatch_streak < 3:
                        time.sleep(0.15)
                        continue
                    mismatch_streak = 0
                    print("STOPPING both guests NOW")
                    try:
                        hh.cmd("stop")
                    except Exception as exc:
                        print("stop host failed:", exc)
                    try:
                        ch.cmd("stop")
                    except Exception as exc:
                        print("stop client failed:", exc)
                    try:
                        a = snapshot_fast(hh)
                        b = snapshot_fast(ch)
                    except Exception as exc:
                        print("paused re-read failed:", exc)
                    ta, tb = a.get("tick") or 0, b.get("tick") or 0
                    if a.get("seed") == b.get("seed") and abs(ta - tb) <= 3:
                        print("false alarm: paused seeds match at H=%s C=%s; resuming" % (ta, tb))
                        try:
                            hh.cmd("cont")
                        except Exception:
                            pass
                        try:
                            ch.cmd("cont")
                        except Exception:
                            pass
                        time.sleep(0.2)
                        continue
                    print("FIRST SEED DIVERGENCE tick H=%s C=%s (paused)" % (ta, tb))
                    print_snap("host", a)
                    print_snap("client", b)
                    try:
                        ha = snapshot(hh, kinds={0, 5})
                        hb = snapshot(ch, kinds={0, 5})
                        print_diff(ha, hb)
                    except Exception as exc:
                        print("biped dump failed:", exc)
                    try:
                        with open("artifacts/rng_trace/ds31_hmp_done.txt", "w") as fh:
                            fh.write("tick H=%s C=%s seed H=%08x C=%08x\n" % (
                                ta, tb,
                                a.get("seed", 0) & 0xffffffff,
                                b.get("seed", 0) & 0xffffffff))
                    except OSError:
                        pass
                    try:
                        hh.close()
                    except Exception:
                        pass
                    try:
                        ch.close()
                    except Exception:
                        pass
                    hh = None
                    ch = None
                    print("dumping rings while paused ...")
                    subprocess.call([
                        sys.executable, "tools/xbox/rng_trace_dump.py",
                        "--hmp-port", "4444",
                        "--out", "artifacts/rng_trace/ds31_trace.json",
                        "--timeout", "20",
                    ])
                    subprocess.call([
                        sys.executable, "tools/xbox/rng_trace_dump.py",
                        "--hmp-port", "4446",
                        "--out", "artifacts/rng_trace/ds31_host_trace.json",
                        "--timeout", "20",
                        "--pe", "artifacts/rng_trace/session_symbols.pe",
                    ])
                    print("DONE paused; leaving both stopped")
                    return 0
                time.sleep(0.15)
            print("FAILED timeout waiting for seed divergence")
            return 1
        print("HMP host :4446  client :4444  (no stop)")
        a = snapshot(hh)
        b = snapshot(ch)
        print_snap("host", a)
        print_snap("client", b)
        print_diff(a, b)
        return 0
    finally:
        if hh is not None:
            hh.close()
        if ch is not None:
            ch.close()


if __name__ == "__main__":
    raise SystemExit(main())
