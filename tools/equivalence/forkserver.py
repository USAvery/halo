#!/usr/bin/env python3
"""A pre-warmed `unicorn_diff` process that forks one child per target.

WHY
---
A batch sweep spends most of its wall clock NOT emulating.  Profiling one
50-seed run of a small function: ~1.1 s of Python import machinery (730
`posix.stat` + 175 `lstat` + 121 `open_code` calls, all against the 9p/drvfs
mount the repo lives on), ~0.4 s parsing the pristine XBE, kb.json, the leaf
cache and the bounds table -- against ~0.006 s per seed of actual Unicorn
work.  `batch_verify` used to pay that whole fixed cost once per target via
`subprocess.Popen([sys.executable, "unicorn_diff.py", ...])`, i.e. ~1.5 s x
4000 targets of pure startup.

This server pays it ONCE.  It imports `unicorn_diff`, warms the caches that
are pure functions of on-disk inputs, and then `fork()`s a child per target.
The child inherits the loaded interpreter and the parsed data copy-on-write
and runs `unicorn_diff.main()` directly.

ISOLATION
---------
The server itself never runs a diff, so no child ever sees another child's
mutations: every fork starts from the same pristine warmed image.  Each child
gets its own session (`setsid`) and process group, so `batch_verify`'s
existing timeout / RSS watchdog can `killpg` it exactly as it killed a
`Popen` child, even though the child is a grandchild it cannot `waitpid` on.
The server does that `waitpid` and reports the status back.

Deliberately single-threaded: `fork()` from a multithreaded process copies
locks in whatever state the other threads left them.  `batch_verify` drives
its workers with a `ThreadPoolExecutor`, which is exactly the situation that
would make forking from IT unsafe -- hence a separate, thread-free server.

PROTOCOL
--------
One request per connection on a UNIX socket, newline-delimited JSON:

    -> {"argv": ["FUN_00012090", "--seeds", "50", ...]}
    <- {"pid": 12345}                 as soon as the child exists
    <- {"exit": 0}                    when it terminates (or is killed)

`{"error": "..."}` replaces either line on a server-side failure.
"""
import json
import os
import signal
import socket
import sys
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _warm():
    """Import `unicorn_diff` and pre-compute every cache a child would rebuild.

    Everything touched here is a pure function of committed on-disk inputs, so
    sharing it across children changes no result -- it only stops each child
    re-reading the same 3.4 MB image and re-parsing the same JSON.
    """
    import unicorn_diff

    # The pristine XBE (3.4 MB read + section table) and the BSS-seed filter
    # derived from it -- the two costliest non-import steps of a small run.
    img = unicorn_diff._xbe_globals_image()
    if img is not None:
        unicorn_diff._bss_seed_entries(*img)
    for warm in ("_load_symbol_addrs",):
        fn = getattr(unicorn_diff, warm, None)
        if fn is not None:
            try:
                fn()
            except Exception:
                pass                      # a cold cache is slow, not wrong
    return unicorn_diff


def _run_child(unicorn_diff, argv):
    """Child side of a fork: become a session leader, run one diff, _exit."""
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    if devnull > 2:
        os.close(devnull)
    # Restore default disposition: the server ignores SIGINT so a Ctrl-C in the
    # batch does not race it, but a child must die like any other subprocess.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    code = 1
    try:
        sys.argv = ["unicorn_diff.py"] + list(argv)
        rc = unicorn_diff.main()
        code = 0 if rc is None else int(rc)
    except SystemExit as e:
        code = 0 if e.code is None else (e.code if isinstance(e.code, int) else 1)
    except BaseException:
        code = 1
    finally:
        # os._exit, never sys.exit: a normal exit would run atexit handlers and
        # flush buffers inherited from the server, duplicating its side effects
        # once per child.
        os._exit(code)


def _serve(sock_path: str):
    unicorn_diff = _warm()

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass
    srv.bind(sock_path)
    srv.listen(64)
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    # Tell the launcher we are ready only after the caches are warm, so the
    # first target does not race the warm-up and pay for it anyway.
    sys.stdout.write("READY\n")
    sys.stdout.flush()

    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        try:
            _handle(conn, unicorn_diff)
        except Exception:
            traceback.print_exc(file=sys.stderr)
        finally:
            try:
                conn.close()
            except OSError:
                pass


def _handle(conn, unicorn_diff):
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(65536)
        if not chunk:
            return
        buf += chunk
    req = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    if req.get("op") == "shutdown":
        conn.sendall(b'{"exit": 0}\n')
        os._exit(0)

    argv = req.get("argv") or []
    pid = os.fork()
    if pid == 0:
        try:
            conn.close()
        except OSError:
            pass
        _run_child(unicorn_diff, argv)
        os._exit(1)                        # unreachable; _run_child never returns

    conn.sendall((json.dumps({"pid": pid}) + "\n").encode("utf-8"))
    _, status = os.waitpid(pid, 0)
    if os.WIFEXITED(status):
        code = os.WEXITSTATUS(status)
    elif os.WIFSIGNALED(status):
        code = -os.WTERMSIG(status)
    else:
        code = 1
    conn.sendall((json.dumps({"exit": code}) + "\n").encode("utf-8"))


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: forkserver.py <unix-socket-path>\n")
        return 2
    _serve(sys.argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
