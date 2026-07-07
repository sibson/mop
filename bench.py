#!/usr/bin/env python3
"""Performance benchmark harness for the mop index store.

Drives the CLI via subprocess so the SAME file runs unchanged on both the
current leveldb backend and the future sqlite backend (see
docs/sqlite-migration-spec.md section 7). The one exception is the pure-write
microbench (phase 1), which must import the backend module directly to bypass
sha1 hashing.

Usage:
    uv run bench.py [--n N] [--dup F] [--k K] [--index-group NAME]
                    [--backend-label LABEL] [--out PATH]
    uv run bench.py --compare a.json b.json
"""
import argparse
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Constants / knobs
# ---------------------------------------------------------------------------

SEED = 1234567  # fixed so the synthetic corpus is reproducible across backends

# The mopcli subgroup that exposes the index/store commands. mopcli.py currently
# registers leveldb.cli under the name "leveldb"; after the sqlite rename the
# group name may change, so it is overridable via --index-group.
INDEX_GROUP = "store"

EXTS = ["jpg", "mp4", "txt", "pdf", "none"]
TOP_N = 50   # top{0..49}
SUB_N = 100  # sub{0..99}

SIZE_MIN = 1024              # 1 KB
SIZE_MAX = 4 * 1024 ** 3     # 4 GB
NULL_SIZE_FRACTION = 0.02    # ~2% of rows have absent/NULL size

TINY_FILE_BYTES = 4096       # 4 KB random content per real file

REPO = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(REPO, "bench", "tmp")
DEFAULT_RESULTS_DIR = os.path.join(REPO, "bench", "results")

WARMUP_RUNS = 1
TIMED_RUNS = 3

# ---------------------------------------------------------------------------
# Backend (phase-1 write microbench only)
# ---------------------------------------------------------------------------
# The store is native sqlite. The leveldb baseline lives in
# bench/results/leveldb.json; reproduce it from a git worktree of the
# pre-migration commit if ever needed.
import store as backend

FileMetaData = backend.FileMetaData


def backend_open(path):
    """Open a fresh writable sqlite db handle for the write microbench.

    store.connect(path, create=True) returns a sqlite3.Connection whose
    FileMetaData.write(conn) does one INSERT OR REPLACE (no commit). The write
    loop commits ONCE at the end (batched, mirrors local.py); backend_close()
    performs that commit.
    """
    return backend.connect(path, create=True)


def backend_close(db):
    """Commit the single batched write and close, so on-disk size and reads
    are consistent (mirrors local.py's commit-once-at-end)."""
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Synthetic corpus generation (OUTSIDE all timed regions)
# ---------------------------------------------------------------------------

def _rand_sha1(rnd):
    return "".join(rnd.choice("0123456789abcdef") for _ in range(40))


def build_records(n, dup):
    """Deterministic list of FileMetaData-ready records.

    Returns list of dicts: {uri, sha1, path, size, fileName}. `size` may be
    absent (~2%) to exercise the usage "without size" branch. ~dup fraction of
    entries reuse a previously generated sha1.
    """
    rnd = random.Random(SEED)
    records = []
    shas = []  # pool of already-used sha1s for duplicate reuse
    for i in range(n):
        top = rnd.randrange(TOP_N)
        sub = rnd.randrange(SUB_N)
        ext = rnd.choice(EXTS)
        if ext == "none":
            fname = f"file{i}"
        else:
            fname = f"file{i}.{ext}"
        path = f"top{top}/sub{sub}/{fname}"
        uri = f"file:///{path}"

        if shas and rnd.random() < dup:
            sha = rnd.choice(shas)
        else:
            sha = _rand_sha1(rnd)
            shas.append(sha)

        rec = {"uri": uri, "sha1": sha, "path": path, "fileName": fname}
        if rnd.random() >= NULL_SIZE_FRACTION:
            rec["size"] = rnd.randint(SIZE_MIN, SIZE_MAX)
        records.append(rec)
    return records


def build_tree(k, root):
    """Create K real tiny files (4KB random each) under root/ for e2e index.

    Deterministic content. Reuses the same top/sub path shape. Generated
    outside timed regions.
    """
    rnd = random.Random(SEED ^ 0xABCDEF)
    if os.path.exists(root):
        shutil.rmtree(root)
    os.makedirs(root)
    for i in range(k):
        top = rnd.randrange(TOP_N)
        sub = rnd.randrange(SUB_N)
        ext = rnd.choice(EXTS)
        d = os.path.join(root, f"top{top}", f"sub{sub}")
        os.makedirs(d, exist_ok=True)
        name = f"file{i}" if ext == "none" else f"file{i}.{ext}"
        with open(os.path.join(d, name), "wb") as f:
            f.write(bytes(rnd.getrandbits(8) for _ in range(TINY_FILE_BYTES)))
    return root


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def rm_artifact(path):
    """Remove a backend db artifact whether it's a dir (leveldb) or file+wal
    (sqlite)."""
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)
    for side in ("-wal", "-shm", "-journal"):
        s = path + side
        if os.path.exists(s):
            os.remove(s)


def path_size(path):
    """Total bytes of a db artifact: sum a dir tree (leveldb) or file+sidecars
    (sqlite)."""
    total = 0
    if os.path.isdir(path):
        for dp, _, fns in os.walk(path):
            for fn in fns:
                fp = os.path.join(dp, fn)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    elif os.path.exists(path):
        total += os.path.getsize(path)
    for side in ("-wal", "-shm", "-journal"):
        s = path + side
        if os.path.exists(s):
            total += os.path.getsize(s)
    return total


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def run_cli(args, expect_output=False):
    """Run `uv run mopcli.py <args...>`. Returns (elapsed_s, stdout).

    Raises CalledProcessError on non-zero exit so the phase wrapper can record
    a SKIPPED reason.
    """
    cmd = ["uv", "run", "mopcli.py"] + args
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    elapsed = time.perf_counter() - t0
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr)
    return elapsed, proc.stdout


def run_local_index(tree, dbpath):
    """Run `uv run local.py index --dbpath <db> <tree>`. Returns (elapsed_s, stdout)."""
    cmd = ["uv", "run", "local.py", "index", "--dbpath", dbpath, tree]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    elapsed = time.perf_counter() - t0
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr)
    return elapsed, proc.stdout


# ---------------------------------------------------------------------------
# Timing harness
# ---------------------------------------------------------------------------

def median_of(fn):
    """1 untimed warmup, then median of TIMED_RUNS. fn() returns elapsed_s and
    may return (elapsed, extra) where extra is captured from the last run.

    Returns dict {median, min, max} plus any 'extra' from the final timed run.
    """
    def _elapsed(res):
        return res[0] if isinstance(res, tuple) else res

    for _ in range(WARMUP_RUNS):
        fn()
    samples = []
    last_extra = None
    for _ in range(TIMED_RUNS):
        res = fn()
        samples.append(_elapsed(res))
        if isinstance(res, tuple) and len(res) > 1:
            last_extra = res[1]
    out = {
        "median_s": statistics.median(samples),
        "min_s": min(samples),
        "max_s": max(samples),
    }
    if last_extra is not None:
        out["extra"] = last_extra
    return out


def phase(results, name, fn):
    """Run a phase, wrapping failures so one broken command doesn't abort the
    whole run. Records SKIPPED: <reason> instead of crashing."""
    print(f"  phase: {name} ...", flush=True)
    try:
        results[name] = fn()
        r = results[name]
        if "median_s" in r:
            print(f"    median={r['median_s']:.4f}s "
                  f"(min={r['min_s']:.4f} max={r['max_s']:.4f})", flush=True)
    except subprocess.CalledProcessError as e:
        reason = (e.stderr or e.output or str(e)).strip().splitlines()
        reason = reason[-1] if reason else str(e)
        results[name] = f"SKIPPED: exit {e.returncode}: {reason}"
        print(f"    SKIPPED: exit {e.returncode}: {reason}", flush=True)
    except Exception as e:  # noqa: BLE001 - defensive: never abort the run
        results[name] = f"SKIPPED: {type(e).__name__}: {e}"
        print(f"    SKIPPED: {type(e).__name__}: {e}", flush=True)


# ---------------------------------------------------------------------------
# Phase 1: write microbench (direct backend import)
# ---------------------------------------------------------------------------

def write_microbench(records, dbpath):
    """Loop N records straight through the writer, fresh db each timed run.
    Returns {median_s, ..., files_per_sec, n}."""
    n = len(records)

    def one_run():
        rm_artifact(dbpath)
        db = backend_open(dbpath)
        t0 = time.perf_counter()
        for rec in records:
            kwargs = {k: v for k, v in rec.items()
                      if k not in ("uri", "sha1", "path")}
            md = FileMetaData(rec["uri"], rec["sha1"], rec["path"], **kwargs)
            md.write(db)
        elapsed = time.perf_counter() - t0
        backend_close(db)
        return elapsed

    res = median_of(one_run)
    res["files_per_sec"] = n / res["median_s"] if res["median_s"] else None
    res["n"] = n
    return res


# ---------------------------------------------------------------------------
# DB population helper (for read phases): build a persistent db once via the
# same writer, leave it on disk.
# ---------------------------------------------------------------------------

def populate_db(records, dbpath):
    rm_artifact(dbpath)
    db = backend_open(dbpath)
    for rec in records:
        kwargs = {k: v for k, v in rec.items()
                  if k not in ("uri", "sha1", "path")}
        md = FileMetaData(rec["uri"], rec["sha1"], rec["path"], **kwargs)
        md.write(db)
    backend_close(db)


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def git_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    except Exception:
        return "unknown"


def utc_timestamp():
    try:
        return int(subprocess.check_output(
            ["date", "-u", "+%s"], text=True).strip())
    except Exception:
        return int(time.time())


def run_bench(args):
    os.makedirs(TMP_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    global INDEX_GROUP
    INDEX_GROUP = args.index_group

    print(f"Backend module: {backend.__name__}  label={args.backend_label}")
    print(f"Generating corpus: n={args.n} dup={args.dup} k={args.k}")

    # Corpus (outside all timing) -----------------------------------------
    records = build_records(args.n, args.dup)
    src_records = records[: args.n // 2]  # N/2 subset for missing/present/merge
    tree = build_tree(args.k, os.path.join(TMP_DIR, "tree"))

    # Artifact paths
    write_db = os.path.join(TMP_DIR, "write.db")
    main_db = os.path.join(TMP_DIR, "main.db")
    src_db = os.path.join(TMP_DIR, "src.db")
    index_db = os.path.join(TMP_DIR, "index.db")

    results = {}

    # Phase 1: write microbench -------------------------------------------
    phase(results, "1_store_write",
          lambda: write_microbench(records, write_db))

    # Populate the persistent read db once (not timed as a phase). ---------
    print("  populating read db (untimed) ...", flush=True)
    populate_db(records, main_db)
    populate_db(src_records, src_db)

    # Phase 2: db size on disk --------------------------------------------
    results["2_db_size"] = {"bytes": path_size(main_db)}
    print(f"  phase: 2_db_size = {results['2_db_size']['bytes']} bytes", flush=True)

    # Phases 3-6: full-scan read commands via subprocess ------------------
    phase(results, "3_ls",
          lambda: median_of(lambda: run_cli([INDEX_GROUP, "ls", main_db])[0]))
    phase(results, "4_meta",
          lambda: median_of(lambda: run_cli([INDEX_GROUP, "meta", main_db])[0]))
    phase(results, "5_dupes",
          lambda: median_of(lambda: run_cli([INDEX_GROUP, "dupes", main_db])[0]))
    phase(results, "6_usage",
          lambda: median_of(lambda: run_cli([INDEX_GROUP, "usage", main_db])[0]))

    # Phase 7: missing / present (target N vs source N/2) -----------------
    phase(results, "7_missing",
          lambda: median_of(
              lambda: run_cli([INDEX_GROUP, "missing", main_db, src_db])[0]))
    phase(results, "7_present",
          lambda: median_of(
              lambda: run_cli([INDEX_GROUP, "present", main_db, src_db])[0]))

    # Phase 8: merge (target + source). merge MUTATES the target, so run
    # against a throwaway copy each timed run; capture result line count.
    def merge_run():
        merge_target = os.path.join(TMP_DIR, "merge_target.db")
        rm_artifact(merge_target)
        if os.path.isdir(main_db):
            shutil.copytree(main_db, merge_target)
        else:
            shutil.copy2(main_db, merge_target)
        elapsed, out = run_cli([INDEX_GROUP, "merge", merge_target, src_db])
        lines = out.count("\n")
        rm_artifact(merge_target)
        return elapsed, {"result_lines": lines}

    phase(results, "8_merge", lambda: median_of(merge_run))

    # Phase 9: e2e local index on the real K-file tree --------------------
    def index_run():
        rm_artifact(index_db)
        elapsed, out = run_local_index(tree, index_db)
        return elapsed, {"lines": out.count("\n")}

    def index_phase():
        res = median_of(index_run)
        res["files_per_sec"] = args.k / res["median_s"] if res["median_s"] else None
        res["k"] = args.k
        return res

    phase(results, "9_e2e_index", index_phase)

    # Assemble result document --------------------------------------------
    doc = {
        "backend_label": args.backend_label,
        "backend_module": backend.__name__,
        "index_group": INDEX_GROUP,
        "n": args.n,
        "dup": args.dup,
        "k": args.k,
        "python_version": sys.version.split()[0],
        "git_sha": git_sha(),
        "timestamp_utc": utc_timestamp(),
        "seed": SEED,
        "warmup_runs": WARMUP_RUNS,
        "timed_runs": TIMED_RUNS,
        "phases": results,
    }

    with open(args.out, "w") as f:
        json.dump(doc, f, indent=2)
    print(f"\nWrote results to {args.out}")

    # Cleanup scratch dbs (leave results). Keep tree removal too.
    for p in (write_db, main_db, src_db, index_db):
        rm_artifact(p)
    if os.path.isdir(tree):
        shutil.rmtree(tree)

    return doc


# ---------------------------------------------------------------------------
# Compare mode
# ---------------------------------------------------------------------------

READ_PHASES = {"3_ls", "4_meta", "5_dupes", "6_usage",
               "7_missing", "7_present"}
REGRESS_THRESHOLD = 0.10  # >10% slower on a read phase => warn


def compare(path_a, path_b):
    with open(path_a) as f:
        a = json.load(f)
    with open(path_b) as f:
        b = json.load(f)

    la, lb = a["backend_label"], b["backend_label"]
    print(f"Compare: {la} (A)  vs  {lb} (B)")
    print(f"  A: n={a['n']} dup={a['dup']} k={a['k']} sha={a['git_sha'][:8]}")
    print(f"  B: n={b['n']} dup={b['dup']} k={b['k']} sha={b['git_sha'][:8]}")
    print()

    hdr = f"{'phase':<16}{la+' (s)':>16}{lb+' (s)':>16}{'delta %':>12}  flag"
    print(hdr)
    print("-" * len(hdr))

    names = []
    for d in (a["phases"], b["phases"]):
        for k in d:
            if k not in names:
                names.append(k)
    names.sort()

    for name in names:
        pa = a["phases"].get(name)
        pb = b["phases"].get(name)

        def med(p):
            if isinstance(p, dict) and "median_s" in p:
                return p["median_s"]
            return None

        ma, mb = med(pa), med(pb)
        if ma is None or mb is None:
            sa = "SKIP" if not isinstance(pa, dict) or "median_s" not in pa else f"{ma:.4f}"
            sb = "SKIP" if not isinstance(pb, dict) or "median_s" not in pb else f"{mb:.4f}"
            print(f"{name:<16}{sa:>16}{sb:>16}{'-':>12}  -")
            continue

        delta = (mb - ma) / ma if ma else 0.0
        flag = "✅"
        if name in READ_PHASES and delta > REGRESS_THRESHOLD:
            flag = "⚠️"
        print(f"{name:<16}{ma:>16.4f}{mb:>16.4f}{delta*100:>11.1f}%  {flag}")

    # size + throughput extras
    print()
    sa = a["phases"].get("2_db_size", {})
    sb = b["phases"].get("2_db_size", {})
    if "bytes" in sa and "bytes" in sb:
        d = (sb["bytes"] - sa["bytes"]) / sa["bytes"] * 100 if sa["bytes"] else 0
        print(f"db size:  {la}={sa['bytes']:,}B  {lb}={sb['bytes']:,}B  ({d:+.1f}%)")

    for name, key in (("1_store_write", "files_per_sec"),
                      ("9_e2e_index", "files_per_sec")):
        wa = a["phases"].get(name, {})
        wb = b["phases"].get(name, {})
        if isinstance(wa, dict) and isinstance(wb, dict) \
                and wa.get(key) and wb.get(key):
            d = (wb[key] - wa[key]) / wa[key] * 100
            print(f"{name}:  {la}={wa[key]:,.0f} f/s  "
                  f"{lb}={wb[key]:,.0f} f/s  ({d:+.1f}%)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--n", type=int, default=100000,
                   help="number of synthetic records (write/read phases)")
    p.add_argument("--dup", type=float, default=0.1,
                   help="fraction of entries reusing an existing sha1")
    p.add_argument("--k", type=int, default=5000,
                   help="number of real tiny files for the e2e index run")
    p.add_argument("--index-group", default=INDEX_GROUP,
                   help="mopcli subgroup name for store commands")
    p.add_argument("--backend-label", default="store",
                   help="label recorded in the result JSON")
    p.add_argument("--out", default=None,
                   help="output JSON path (default bench/results/<label>.json)")
    p.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"),
                   help="compare two result JSONs and exit")
    args = p.parse_args()

    if args.compare:
        compare(args.compare[0], args.compare[1])
        return

    if args.out is None:
        args.out = os.path.join(DEFAULT_RESULTS_DIR, f"{args.backend_label}.json")

    run_bench(args)


if __name__ == "__main__":
    main()
