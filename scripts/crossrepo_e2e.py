"""Cross-repo pollination e2e: index ALL of hari2353's non-fork repos into one
graph, then ask questions whose answers must combine context from several repos.

Usage: .venv/bin/python scripts/crossrepo_e2e.py <repo-dir> [<repo-dir> ...]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cbm_memory.graph import GitNexusGraph  # noqa: E402
from cbm_memory.repoindex import index_repo  # noqa: E402


def build(paths: list[str]) -> GitNexusGraph:
    import collections

    t0 = time.perf_counter()
    data = {}
    for p in paths:  # index serially here; parallel path covered by mcp_server tests
        data[p] = index_repo(p)
        print(f"  indexed {Path(p).name}: {len(data[p]['symbols'])} syms, "
              f"{len(data[p]['calls'])} calls, {len(data[p]['commits'])} commits")
    g = GitNexusGraph()
    for d in data.values():
        g.load(d)
    print(f"index+load all: {time.perf_counter() - t0:.1f}s")
    per_repo = collections.Counter(r["repo"] for r in g.db.execute(
        "SELECT repo FROM nodes WHERE kind='symbol'"))
    print(f"repos in graph: {len(per_repo)}")
    return g


def main(paths: list[str]) -> None:
    g = build(paths)
    names = {Path(p).name: p for p in paths}
    print("\n===== Q1: shared vocabulary across repos (pollination points) =====")
    owner: dict[str, set[str]] = {}
    for r in g.db.execute("SELECT name, repo FROM nodes WHERE kind='symbol' "
                          "AND length(name) > 5 AND meta NOT IN ('heading','md-heading')"):
        owner.setdefault(r["name"], set()).add(r["repo"])
    shared = sorted(((n, rs) for n, rs in owner.items() if len(rs) >= 2),
                    key=lambda x: -len(x[1]))
    for n, rs in shared[:12]:
        print(f"  {n}: {len(rs)} repos -> {sorted(Path(r).name for r in rs)[:6]}")

    print("\n===== Q2: 'where is jwt auth handled across ALL my projects?' =====")
    rows = g.search_codebase_memory("*", "auth")
    by_repo: dict[str, list[str]] = {}
    for r in rows[:20]:
        by_repo.setdefault(Path(r["repo"]).name, []).append(r["name"])
    for repo, syms in by_repo.items():
        print(f"  {repo}: {', '.join(syms[:5])}")

    print("\n===== Q3: working IN one repo, what do my OTHER repos say? =====")
    probe = next(p for n, p in names.items() if n == "auth-gateway")
    ctx = g.get_codebase_context(probe, "auth token session")
    print("  scoped top_files:", [(Path(t['repo']).name, t['path']) for t in ctx["top_files"]])
    print("  related (other repos):", [(Path(r['repo']).name, r['path'])
                                       for r in ctx["related"]])

    print("\n===== Q4: same-named symbol defined in several repos — provenance =====")
    cand = next((n for n, rs in shared
                 if not n.startswith("__") and len(rs) >= 4), None)
    if cand:
        rows = [r for r in g.search_codebase_memory("*", cand) if r["kind"] != "heading"]
        for r in rows[:6]:
            print(f"  {cand}: {Path(r['repo']).name}/{r['path']} ({r['kind']})")

    print("\n===== Q5: who works across multiple of my repos? =====")
    for a in g.team_activity("*")[:10]:
        if a["repos"] > 1:
            print(f"  {a['author_name']}: {a['commits']} commits across {a['repos']} repos")

    print("\n===== Q6: blast radius question spanning repos =====")
    hit_repos = ("cbm-memory", "graph-memory-platform", "skills-engine")
    hits = [r for r in g.search_codebase_memory("*", "index")
            if Path(r["repo"]).name in hit_repos and r["kind"] != "heading"]
    for r in hits[:3]:
        imp = g.change_impact(r["repo"], r["path"])
        print(f"  {Path(r['repo']).name}/{r['path']}: {len(imp['impacted'])} impacted files")
    assert hits, "expected an index-related file in at least one of the probed repos"

    print("\nCROSS-REPO E2E OK")


if __name__ == "__main__":
    main(sys.argv[1:])
