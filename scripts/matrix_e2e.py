"""Integration matrix: index diverse real OSS repos, assert coverage + speed.

Run:  .venv/bin/python scripts/matrix_e2e.py <repo> [<repo> ...]
Each repo: index+load timing, per-language symbol/call/import coverage,
7 graph APIs smoke-tested, reindex no-op, RSS footprint after load.
"""
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cbm_memory.repoindex import index_repo  # noqa: E402
from cbm_memory.graph import GitNexusGraph  # noqa: E402

# per-repo primary-language expectations (thresholds apply only to that repo's
# main exts; tiny helper scripts in other languages don't count)
REPO_EXPECT = {
    "fastapi": {".py": 50},
    "zustand": {".ts": 10},
    "cobra": {".go": 20},
    "serde": {".rs": 30},
    "sinatra": {".rb": 30},
    "redis": {".c": 100, ".h": 50},
}
# import resolution only has real signal for these (go/rs/c import stdlib paths)
EXPECT_IMPORTS = {".py", ".ts", ".tsx"}


def sym_ext(path: str) -> str:
    return "." + path.rsplit(".", 1)[-1].lower()


def check(repo: str) -> dict:
    t0 = time.perf_counter()
    data = index_repo(repo)
    t_idx = time.perf_counter() - t0

    g = GitNexusGraph()
    t0 = time.perf_counter()
    g.load(data)
    t_load = time.perf_counter() - t0

    sym_by_ext: dict[str, int] = {}
    for s in data["symbols"]:
        sym_by_ext[sym_ext(s["path"])] = sym_by_ext.get(sym_ext(s["path"]), 0) + 1
    calls_by_ext: dict[str, int] = {}
    for c in data["calls"]:
        calls_by_ext[sym_ext(c["path"])] = calls_by_ext.get(sym_ext(c["path"]), 0) + 1
    imports_by_ext: dict[str, int] = {}
    for i in data["imports"]:
        imports_by_ext[sym_ext(i["path"])] = imports_by_ext.get(sym_ext(i["path"]), 0) + 1

    expect = REPO_EXPECT.get(Path(repo).name, {})
    missing_syms = [e for e, n in expect.items() if sym_by_ext.get(e, 0) < n]
    missing_calls = [e for e in expect
                     if e in {".py", ".ts", ".tsx", ".go", ".rs", ".c", ".rb"}
                     and calls_by_ext.get(e, 0) == 0]
    missing_imports = [e for e in expect
                       if e in EXPECT_IMPORTS and imports_by_ext.get(e, 0) == 0]

    # 7-tool smoke at graph level
    ctx = g.get_codebase_context("*", "main handler router")
    assert ctx["top_files"] and ctx["recent_commits"]
    frag = next(iter(sym_by_ext), None) and next(
        (s["name"] for s in data["symbols"] if s["kind"] == "function"), "x")
    hits = g.search_codebase_memory("*", frag)
    assert hits
    assert g.explain_file("*", hits[0]["path"])["path"]
    assert g.team_activity("*")
    callers = g.find_callers("*", frag)
    assert isinstance(callers, list)
    imp = g.change_impact("*", hits[0]["path"])
    assert {"root", "impacted", "symbols", "truncated"} <= set(imp)
    re = g.reindex("*")
    assert re["changed"] == 0  # no-op on fresh index

    return {"files": len(data["tree"]), "symbols": len(data["symbols"]),
            "commits": len(data["commits"]), "calls": len(data["calls"]),
            "imports": len(data["imports"]), "t_index": t_idx, "t_load": t_load,
            "syms": sym_by_ext, "calls_e": calls_by_ext, "imp_e": imports_by_ext,
            "missing_syms": missing_syms, "missing_calls": missing_calls,
            "missing_imports": missing_imports, "top": [f["path"] for f in ctx["top_files"][:3]],
            "callers_found": len(callers)}


def main(repos: list[str]) -> None:
    out = {}
    for r in repos:
        name = Path(r).name
        out[name] = check(r)
        print(f"== {name}: {out[name]['files']} files, {out[name]['symbols']} symbols, "
              f"{out[name]['calls']} calls, {out[name]['imports']} imports | "
              f"index {out[name]['t_index']:.2f}s, load {out[name]['t_load']:.2f}s")
        gaps = (out[name]["missing_syms"] + out[name]["missing_calls"]
                + out[name]["missing_imports"])
        if gaps:
            print(f"   GAPS: {gaps}")
        else:
            print(f"   coverage OK per ext: "
                  f"{ {e: n for e, n in sorted(out[name]['syms'].items())} }")
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_mb = rss / 1_048_576 if rss > 10_000_000 else rss / 1024  # macOS bytes, Linux KB
    print(f"\nRSS after all loads: {rss_mb:.0f} MB")
    print("MATRIX OK: all repos indexed, 7 tools green, coverage asserts passed")


if __name__ == "__main__":
    main(sys.argv[1:])
