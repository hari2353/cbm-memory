"""MCP server (T01): FastMCP tools backed by the in-process GitNexusGraph.
Run:  python -m cbm_memory.mcp_server <repo> [repo2 ...]   # stdio, 1+ repos
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .graph import GitNexusGraph

_GRAPH: GitNexusGraph | None = None


def set_graph(graph: GitNexusGraph) -> None:
    global _GRAPH
    _GRAPH = graph


def get_graph() -> GitNexusGraph:
    if _GRAPH is None:
        raise RuntimeError("no graph loaded; run index_repository(repo_path) first or call set_graph()")
    return _GRAPH


def index_repositories(paths, workers: int = 4) -> GitNexusGraph:
    """Index one or more repos (parse phase parallel across repos) into ONE
    graph; servers may host several repos, tools are repo-agnostic."""
    import pathlib

    from .repoindex import index_repo

    paths = [str(pathlib.Path(p).resolve()) for p in paths]
    if len(paths) == 1:
        data = [index_repo(paths[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(paths))) as ex:
            data = list(ex.map(index_repo, paths))
    g = GitNexusGraph()
    for d in data:
        g.load(d)
    set_graph(g)
    return g


def index_repository(repo_path: str) -> dict:
    data = index_repositories([repo_path]).db.execute(
        "SELECT repo FROM repo_meta ORDER BY indexed_at DESC LIMIT 1").fetchone()
    graph = get_graph()
    repo = data["repo"]
    files = graph.db.execute(
        "SELECT COUNT(*) FROM nodes WHERE kind='file' AND repo=?", (repo,)).fetchone()[0]
    symbols = graph.db.execute(
        "SELECT COUNT(*) FROM nodes WHERE kind='symbol' AND repo=?", (repo,)).fetchone()[0]
    commits = graph.db.execute(
        "SELECT COUNT(*) FROM nodes WHERE kind='commit' AND repo=?", (repo,)).fetchone()[0]
    return {"indexed": repo_path, "commits": commits,
            "files": files, "symbols": symbols}


# ---- tool bodies (tested directly; FastMCP wraps them below) ----

def get_codebase_context(ctx=None, repo: str = "*", query: str = "") -> dict:
    return get_graph().get_codebase_context(repo, query)


def search_codebase_memory(ctx=None, repo: str = "*", query: str = "") -> list[dict]:
    return get_graph().search_codebase_memory(repo, query)


def explain_file(ctx=None, repo: str = "*", path: str = "") -> dict:
    return get_graph().explain_file(repo, path)


def team_activity(ctx=None, repo: str = "*") -> list[dict]:
    return get_graph().team_activity(repo)


def find_callers(ctx=None, repo: str = "*", symbol: str = "") -> list[dict]:
    return get_graph().find_callers(repo, symbol)


def change_impact(ctx=None, repo: str = "*", path: str = "") -> dict:
    return get_graph().change_impact(repo, path)


def reindex(ctx=None, repo: str = "*") -> dict:
    return get_graph().reindex(repo)


def build_mcp():
    """MCP app with the 7 tools (stdio)."""
    try:
        from mcp.server.mcpserver import MCPServer          # mcp 2.x
    except ImportError:
        from mcp.server.fastmcp import FastMCP as MCPServer  # mcp 1.x
    mcp = MCPServer("cbm-memory")

    @mcp.tool()
    def get_codebase_context(repo: str, query: str) -> dict:
        """Relevant files + recent commits for a work query, from repo memory.
repo="*" searches ALL indexed repos (each hit labeled with its repo); a
specific repo scopes results to it and adds `related`: top hits from your
OTHER repos (cross-pollination)."""
        return get_graph().get_codebase_context(repo, query)

    @mcp.tool()
    def search_codebase_memory(repo: str, query: str) -> list[dict]:
        """Search indexed symbols by name fragment, then file contents (FTS).
Spans every indexed repo; rows carry `repo` so hits are attributable."""
        return get_graph().search_codebase_memory(repo, query)

    @mcp.tool()
    def explain_file(repo: str, path: str) -> dict:
        """Provenance for one file: its symbols + commits that modified it.
If several indexed repos contain the same path, `repo` picks one (others
listed in `repos`); `related` surfaces the same-named symbols in other
repos (cross-pollination)."""
        return get_graph().explain_file(repo, path)

    @mcp.tool()
    def team_activity(repo: str) -> list[dict]:
        """Commits per author across the indexed repo(s); the same person
working in several repos is merged into one row with a `repos` count."""
        return get_graph().team_activity(repo)

    @mcp.tool()
    def find_callers(repo: str, symbol: str) -> list[dict]:
        """Who calls this symbol: caller functions + their files (call graph).
Searches every indexed repo by symbol name; hits carry `repo`."""
        return get_graph().find_callers(repo, symbol)

    @mcp.tool()
    def change_impact(repo: str, path: str) -> dict:
        """Blast radius of editing a file: symbols + impacted files by depth.
Scoped to the file's repo (resolved by `repo` when several repos share
the path)."""
        return get_graph().change_impact(repo, path)

    @mcp.tool()
    def reindex(repo: str) -> dict:
        """Incremental refresh from git (diff + status) after new commits."""
        return get_graph().reindex(repo)

    @mcp.resource("codebase://indexed")
    def indexed() -> str:
        from .graph import _SCHEMA
        return "codebase-memory MCP; sources indexed via index_repository tool."

    return mcp


def main(repo_path: str | None = None) -> None:
    if repo_path is None:  # console script (uvx) calls main() with no args
        import sys
        repo_path = sys.argv[1] if len(sys.argv) > 1 else None
    if repo_path:
        if isinstance(repo_path, (list, tuple)):
            index_repositories(repo_path)
        else:
            import sys
            index_repositories(sys.argv[1:] or [repo_path])
    build_mcp().run()  # stdio transport


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else None)
