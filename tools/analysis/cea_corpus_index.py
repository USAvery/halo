#!/usr/bin/env python3
"""Index the Halo CEA Xbox 360 decompiled C corpus.

The CEA corpus (Halo: Combat Evolved Anniversary's Xbox 360 build, decompiled
to C) ships as three trees under src/engine:

  blam/    one function per .c file  (~7,300 files)
  data/    one global per .c file    (~1,600 files)
  headers/ struct/enum/type headers  (~2,900 files, some in havok/hcex/ws
           subdirectories)

This script walks all three trees, extracts a best-effort structural index
(names, original-Xbox addresses, callees, string literals, divergence
markers, and struct layouts), and writes it out as one JSON file so other
tooling can query the corpus without re-parsing it every time.

File conventions observed in the corpus (not guaranteed, hence "best effort"
throughout):
  - The first comment line of a .c file is usually
        /* <name> @0x8XXXXXXX -- ... */
    or
        /* <name> @ 0x8XXXXXXX ... */
    giving the function/global's address in the original Xbox 360 binary.
    Some files (inline-only helpers, address-less bss globals) have no
    address at all.
  - DEVIATION / PORT DEVIATION / CORRECTED TO THE ORIGINAL XBOX mark a note
    that this file's behavior differs from the literal CEA-360 decompile.
    OWNER-DIRECTED / OWNER DECISION mark a divergence that was deliberately
    requested by the project owner (a subset of the above).
  - Headers annotate struct fields with a trailing offset comment such as
    `/* 0x1EC */` or `/* @0x004 */`, and the struct's closing brace sometimes
    carries the total size as `/* 0x724 = 1828 bytes */` or just `/* 0x204 */`.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time

_ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))
CORPUS_PATH_FILE = os.path.join(_ANALYSIS_DIR, "cea_corpus.path")

DEFAULT_OUT = "artifacts/cea_corpus/index.json"


def resolve_corpus_root(cli_value=None):
    """Resolve the CEA corpus root (src/engine of the decompiled Xbox 360
    tree) without hardcoding a machine-specific path in the source.

    Resolution order:
      1. --corpus on the command line (the cli_value argument here).
      2. The HALO_CEA_CORPUS environment variable.
      3. The first non-empty, non-comment line of
         tools/analysis/cea_corpus.path, a gitignored local file (see
         tools/analysis/cea_corpus.path.example for the format).
    Raises SystemExit with a message naming all three options if none of
    them resolve.
    """
    candidates = []
    if cli_value:
        candidates.append(("--corpus", cli_value))
    env_value = os.environ.get("HALO_CEA_CORPUS")
    if env_value:
        candidates.append(("HALO_CEA_CORPUS", env_value))
    if os.path.exists(CORPUS_PATH_FILE):
        with open(CORPUS_PATH_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    candidates.append(("tools/analysis/cea_corpus.path", line))
                    break
    for _source, candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    detail = "\n".join(f"  {source}: {path}" for source, path in candidates)
    raise SystemExit(
        "Could not resolve an existing CEA corpus root (expected a directory "
        "containing blam/, data/, and headers/). Provide it one of three ways:\n"
        "  1. --corpus <path to src/engine of the CEA decompile tree>\n"
        "  2. export HALO_CEA_CORPUS=<path>\n"
        "  3. Create tools/analysis/cea_corpus.path (gitignored) containing "
        "the path on its first line -- see tools/analysis/cea_corpus.path.example.\n"
        + ("Configured candidates:\n" + detail if detail else "No configured candidates found.")
    )


def validate_corpus_layout(corpus_root):
    missing = [sub for sub in ("blam", "data", "headers")
               if not os.path.isdir(os.path.join(corpus_root, sub))]
    if missing:
        raise SystemExit(
            f"CEA corpus root is missing required directories: {', '.join(missing)}: "
            f"{corpus_root}"
        )
    return corpus_root


# Keep path resolution and layout validation separate so callers can report a
# useful distinction between an unconfigured path and a malformed corpus.
# Index generation validates both before walking any files.


def compute_corpus_signature(corpus_root):
    """The authoritative staleness fingerprint for the corpus, recorded into
    index.json at generation time: file count plus a sha256 over the sorted
    "relpath:size" list of every file under blam/, data/, and headers/.

    Also carries "fast_hash", a sha256 over the sorted relpath list alone
    (no per-file os.path.getsize() calls). On this repo's corpus (11,901
    files over a /mnt/g 9p mount) the getsize() calls dominate the cost of
    this function (~31s measured), because each is a separate round trip to
    the host filesystem, while just walking the tree and hashing paths costs
    well under a second. Downstream per-invocation tools (cea_body.py,
    cea_propagate_names.py) cannot afford the full walk+getsize cost on
    every run, so they compare against fast_hash via
    compute_corpus_fast_signature() below instead of recomputing this
    function. fast_hash catches files added, removed, or renamed (the
    common case: someone points --corpus at a different or updated checkout)
    but will not catch an in-place edit that changes a file's size without
    renaming it — that's the deliberate tradeoff for keeping the check fast
    enough to run unconditionally."""
    entries = []
    relpaths = []
    for sub in ("blam", "data", "headers"):
        sub_dir = os.path.join(corpus_root, sub)
        for path in walk_files(sub_dir, (".c", ".h")):
            relpath = rel(path, corpus_root)
            relpaths.append(relpath)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = -1
            entries.append(f"{relpath}:{size}")
    entries.sort()
    relpaths.sort()
    digest = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()
    fast_digest = hashlib.sha256("\n".join(relpaths).encode("utf-8")).hexdigest()
    return {"file_count": len(entries), "hash": digest, "fast_hash": fast_digest}


def compute_corpus_fast_signature(corpus_root):
    """The cheap half of compute_corpus_signature(): file count plus
    fast_hash only, from a walk that never calls os.path.getsize(). Measured
    at well under a second on this repo's corpus, versus ~31s for the full
    signature. This is what per-invocation tools should call to compare
    against the "fast_hash" recorded by compute_corpus_signature()."""
    relpaths = []
    for sub in ("blam", "data", "headers"):
        sub_dir = os.path.join(corpus_root, sub)
        for path in walk_files(sub_dir, (".c", ".h")):
            relpaths.append(rel(path, corpus_root))
    relpaths.sort()
    fast_digest = hashlib.sha256("\n".join(relpaths).encode("utf-8")).hexdigest()
    return {"file_count": len(relpaths), "fast_hash": fast_digest}

# C89 keywords plus the MSVC/decompiler pseudo-keywords that show up in this
# corpus, so they never get reported as "callees".
C_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if", "int",
    "long", "register", "return", "short", "signed", "sizeof", "static",
    "struct", "switch", "typedef", "union", "unsigned", "void", "volatile",
    "while",
    "__int8", "__int16", "__int32", "__int64", "__cdecl", "__stdcall",
    "__fastcall", "__forceinline", "__inline", "inline", "__declspec",
    "__asm", "__try", "__except", "__finally", "__leave", "__based",
}

DEVIATION_MARKERS = ("DEVIATION", "CORRECTED TO THE ORIGINAL XBOX")
OWNER_MARKERS = ("OWNER-DIRECTED", "OWNER DECISION")

ADDR_RE = re.compile(r"\b0x(8[0-9A-Fa-f]{5,7})\b")
STRING_RE = re.compile(r'"((?:\\.|[^"\\])*)"')
# (?<!\.) and (?<!->) keep both of these from matching a struct/union member
# access (foo.index, bar->index) as if it were a reference to the global
# "index" or "bar" -- data-array names are short and common enough (index,
# count, state) to collide with ordinary field names across the corpus. This
# guard was previously only on IDENT_RE; without it on IDENT_CALL_RE too,
# a member-access method call like shader->effect->lpVtbl->SetVector(...)
# was reported as a callee "SetVector", "effect", or "lpVtbl" depending on
# where the chain broke -- a phantom callee that no CEA function named
# SetVector/effect/lpVtbl anywhere in the corpus.
IDENT_CALL_RE = re.compile(r"(?<!\.)(?<!->)\b([A-Za-z_]\w*)\s*\(")
IDENT_RE = re.compile(r"(?<!\.)(?<!->)\b([A-Za-z_]\w*)\b")
BLAMPC_RE = re.compile(r"\bblampc_[A-Za-z0-9_]*\b")
HEADER_INCLUDE_RE = re.compile(r'#include\s*"(?:\.\./)*headers/')

STRUCT_OPEN_RE = re.compile(r"typedef\s+struct\b\s*([A-Za-z_]\w*)?\s*\{")
STRUCT_TAIL_RE = re.compile(
    r"\s*([A-Za-z_]\w*)\s*;[ \t]*(?:/\*(.*?)\*/|//([^\n]*))?", re.DOTALL
)
FIELD_RE = re.compile(
    r"^\s*(?P<type>[A-Za-z_][\w\s\*]*?)\s+(?P<stars>\**)(?P<name>[A-Za-z_]\w*)"
    r"\s*(?P<arr>(?:\[[^\]]*\])*)\s*;\s*"
    r"(?:/\*(?P<comment>.*?)\*/|//(?P<linecomment>.*))?\s*$"
)
SIZE_EQ_BYTES_RE = re.compile(r"=\s*(\d+)\s*bytes")
SIZE_PLAIN_BYTES_RE = re.compile(r"\b(\d+)\s*bytes\b")
SIZE_HEX_RE = re.compile(r"0x([0-9A-Fa-f]+)")


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def mask_ranges(text, blank):
    """Walk text once, returning a same-length copy with comments blanked
    (if blank=True) and always skipping over string/char literal bodies so
    braces/keywords inside them never confuse the caller. Used as the basis
    for both comment-stripping and brace-depth counting."""
    out = list(text)
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            end = n if j == -1 else j + 2
            if blank:
                for k in range(i, end):
                    out[k] = " "
            i = end
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i + 2)
            end = n if j == -1 else j
            if blank:
                for k in range(i, end):
                    out[k] = " "
            i = end
            continue
        if c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            i = j
            continue
        if c == "'":
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "'":
                    j += 1
                    break
                j += 1
            i = j
            continue
        i += 1
    return "".join(out)


def strip_comments(text):
    """Comments -> single blanks; string/char literals left intact."""
    return mask_ranges(text, blank=True)


def mask_literals(code_no_comments):
    """String/char literal bodies -> blanks of the same length, so callee /
    identifier scanning never matches text that only appears inside a
    string."""
    out = list(code_no_comments)
    i = 0
    n = len(code_no_comments)
    text = code_no_comments
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if c == "'":
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "'":
                    j += 1
                    break
                j += 1
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def extract_addr(raw_text, name):
    lines = raw_text.splitlines()
    if not lines:
        return None

    # Fast path: the common convention puts "name @0x8XXXXXXX" on line 1.
    m = ADDR_RE.search(lines[0])
    if m:
        return int(m.group(1), 16)

    # Slow path: some files lead with #include lines before the header
    # comment (e.g. "attract_mode_reset_timer", "rasterizer_memory_pool_
    # dispose"), pushing the address annotation past line 1. Collect the
    # first block comment that appears before any real code, then accept
    # an address from it ONLY on a line where the function's own name
    # appears before the address. That guard is what keeps files like
    # "abs16" (whose header comment lists OTHER functions' call-site
    # addresses, never abs16's own) correctly address-less.
    block_lines = []
    started = False
    for line in lines:
        stripped = line.strip()
        if not started:
            if not stripped or stripped.startswith("#"):
                continue
            if "/*" in line:
                started = True
                block_lines.append(line)
                if "*/" in line[line.index("/*") + 2:]:
                    break
                continue
            break  # real code before any comment: no header block at all
        else:
            block_lines.append(line)
            if "*/" in line:
                break

    name_re = re.compile(r"\b" + re.escape(name) + r"\b")
    for line in block_lines:
        nm = name_re.search(line)
        if not nm:
            continue
        m = ADDR_RE.search(line, nm.end())
        if m:
            return int(m.group(1), 16)
    return None


def has_any(text, markers):
    # Case-insensitive: the corpus spells these both as shouting tags
    # ("DEVIATION", "OWNER-DIRECTED DIVERGENCE") and as ordinary sentence
    # case ("Deviation: ..."). Both mark the same kind of note.
    lowered = text.lower()
    return any(m.lower() in lowered for m in markers)


def is_all_caps_macro(tok):
    return tok.upper() == tok and any(ch.isalpha() for ch in tok)


def extract_callees(masked_code, own_name):
    out = set()
    for m in IDENT_CALL_RE.finditer(masked_code):
        tok = m.group(1)
        if tok in C_KEYWORDS or tok == own_name or is_all_caps_macro(tok):
            continue
        out.add(tok)
    return sorted(out)


def extract_strings(code_no_comments):
    out = set()
    for line in code_no_comments.splitlines():
        # Skip preprocessor directives: #include "headers/foo.h" is not a
        # string literal in the function body, it is an include path, and
        # letting it through pollutes this field with noise the caller has
        # to filter back out.
        if line.lstrip().startswith("#"):
            continue
        for m in STRING_RE.finditer(line):
            s = m.group(1)
            if len(s) > 200:
                s = s[:200]
            out.add(s)
    return sorted(out)


def extract_blampc_hooks(masked_code):
    return sorted(set(BLAMPC_RE.findall(masked_code)))


def extract_globals(masked_code, data_names, own_name):
    if not data_names:
        return []
    idents = set(IDENT_RE.findall(masked_code))
    idents.discard(own_name)
    return sorted(idents & data_names)


def rel(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def index_function(path, root, data_names):
    raw = read_text(path)
    name = os.path.splitext(os.path.basename(path))[0]
    no_comments = strip_comments(raw)
    masked = mask_literals(no_comments)

    return {
        "name": name,
        "file": rel(path, root),
        "addr360": extract_addr(raw, name),
        "has_header": bool(HEADER_INCLUDE_RE.search(raw)),
        "deviation": has_any(raw, DEVIATION_MARKERS + OWNER_MARKERS),
        "owner_divergence": has_any(raw, OWNER_MARKERS),
        "callees": extract_callees(masked, name),
        "strings": extract_strings(no_comments),
        "globals": extract_globals(masked, data_names, name),
        "blampc_hooks": extract_blampc_hooks(masked),
    }


def first_declaration_line(raw):
    no_comments = strip_comments(raw)
    for line in no_comments.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        return s
    return ""


def index_data(path, root):
    raw = read_text(path)
    name = os.path.splitext(os.path.basename(path))[0]
    return {
        "name": name,
        "file": rel(path, root),
        "addr360": extract_addr(raw, name),
        "decl": first_declaration_line(raw),
    }


def parse_struct_size(size_comment):
    if not size_comment:
        return None
    m = SIZE_EQ_BYTES_RE.search(size_comment)
    if m:
        return int(m.group(1))
    m = SIZE_HEX_RE.search(size_comment)
    if m:
        return int(m.group(1), 16)
    m = SIZE_PLAIN_BYTES_RE.search(size_comment)
    if m:
        return int(m.group(1))
    return None


def parse_field(line):
    m = FIELD_RE.match(line)
    if not m:
        return None
    ftype = re.sub(r"\s+", " ", m.group("type")).strip()
    stars = m.group("stars") or ""
    arr = m.group("arr") or ""
    if stars:
        ftype = ftype + stars
    fname = m.group("name")
    offset = None
    comment = m.group("comment") or m.group("linecomment")
    if comment:
        om = SIZE_HEX_RE.search(comment)
        if om:
            offset = int(om.group(1), 16)
    if arr:
        ftype = ftype + arr
    return {"name": fname, "offset": offset, "type": ftype}


def find_struct_blocks(text, masked):
    """Yield (name, body_text, size_comment) for each `typedef struct {...}
    Name;` block, using brace-depth counting over the comment-blanked text
    so nested unions/structs don't break the match, then slicing the
    ORIGINAL text (which still has the offset comments) for the body."""
    blocks = []
    for m in STRUCT_OPEN_RE.finditer(masked):
        tag_name = m.group(1)
        brace_pos = m.end() - 1
        depth = 0
        i = brace_pos
        n = len(masked)
        end = -1
        while i < n:
            if masked[i] == "{":
                depth += 1
            elif masked[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
            i += 1
        if end == -1:
            continue
        body = text[brace_pos + 1:end]
        tail_raw = text[end + 1:end + 400]
        tm = STRUCT_TAIL_RE.match(tail_raw)
        if not tm:
            continue
        typedef_name = tm.group(1)
        size_comment = tm.group(2) or tm.group(3)
        name = typedef_name or tag_name
        if not name:
            continue
        blocks.append((name, body, size_comment))
    return blocks


def index_header(path, root):
    try:
        raw = read_text(path)
        masked = mask_ranges(raw, blank=True)
        structs = []
        for name, body, size_comment in find_struct_blocks(raw, masked):
            fields = []
            for line in body.splitlines():
                if not line.strip():
                    continue
                f = parse_field(line)
                if f is not None:
                    fields.append(f)
            structs.append({
                "name": name,
                "file": rel(path, root),
                "size": parse_struct_size(size_comment),
                "fields": fields,
            })
        return structs
    except Exception:
        return []


def walk_files(root, exts):
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if any(fn.endswith(e) for e in exts):
                yield os.path.join(dirpath, fn)


def build_index(corpus_root):
    blam_dir = os.path.join(corpus_root, "blam")
    data_dir = os.path.join(corpus_root, "data")
    headers_dir = os.path.join(corpus_root, "headers")

    data_files = sorted(walk_files(data_dir, (".c",)))
    data_names = {os.path.splitext(os.path.basename(p))[0] for p in data_files}

    data_records = [index_data(p, corpus_root) for p in data_files]

    blam_files = sorted(walk_files(blam_dir, (".c",)))
    function_records = [index_function(p, corpus_root, data_names) for p in blam_files]

    header_files = sorted(walk_files(headers_dir, (".h",)))
    struct_records = []
    for p in header_files:
        struct_records.extend(index_header(p, corpus_root))

    summary = {
        "functions": len(function_records),
        "functions_with_addr360": sum(1 for f in function_records if f["addr360"] is not None),
        "data": len(data_records),
        "data_with_addr360": sum(1 for d in data_records if d["addr360"] is not None),
        "structs": len(struct_records),
        "structs_with_size": sum(1 for s in struct_records if s["size"] is not None),
        "fields_total": sum(len(s["fields"]) for s in struct_records),
        "fields_with_offset": sum(
            1 for s in struct_records for fld in s["fields"] if fld["offset"] is not None
        ),
        "headers_scanned": len(header_files),
    }

    signature = compute_corpus_signature(corpus_root)

    return {
        "functions": function_records,
        "data": data_records,
        "structs": struct_records,
        "summary": summary,
        "corpus_signature": {
            "corpus_root": corpus_root,
            "file_count": signature["file_count"],
            "hash": signature["hash"],
            "fast_hash": signature["fast_hash"],
        },
    }


def main():
    ap = argparse.ArgumentParser(description="Index the CEA Xbox 360 decompiled C corpus.")
    ap.add_argument("--corpus", default=None,
                     help="Path to src/engine of the corpus. If omitted, falls back to the "
                          "HALO_CEA_CORPUS environment variable, then to "
                          "tools/analysis/cea_corpus.path.")
    ap.add_argument("--out", default=DEFAULT_OUT, help="Output JSON path.")
    args = ap.parse_args()
    args.corpus = validate_corpus_layout(resolve_corpus_root(args.corpus))

    t0 = time.time()
    index = build_index(args.corpus)
    elapsed = time.time() - t0

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1, sort_keys=False)

    out_size = os.path.getsize(args.out)
    print(json.dumps(index["summary"], indent=2))
    print(json.dumps(index["corpus_signature"], indent=2))
    print("elapsed_seconds=%.2f" % elapsed, file=sys.stderr)
    print("output_bytes=%d" % out_size, file=sys.stderr)


if __name__ == "__main__":
    main()
