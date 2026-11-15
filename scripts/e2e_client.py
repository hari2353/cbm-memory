"""E2E proof: spawn the real MCP server over stdio and drive all 7 tools.

Usage: .venv/bin/python scripts/e2e_client.py /path/to/some/repo
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TOOLS = {"get_codebase_context", "search_codebase_memory",
                  "explain_file", "team_activity", "find_callers",
                  "change_impact", "reindex"}


def payload(result):
    sc = getattr(result, "structured_content", None)
    if sc is None:  # mcp 1.x alias
        sc = getattr(result, "structuredContent", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    if sc is not None:
        return sc
    return json.loads(result.content[0].text)


async def main(repos: list[str]) -> None:
    repo = repos[0]  # focused questions target the first repo; "*" spans all
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "cbm_memory.mcp_server", *repos],
        cwd=str(REPO_ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print("TOOLS:", sorted(tools))
            assert set(tools) == EXPECTED_TOOLS, tools

            r = payload(await s.call_tool("get_codebase_context",
                                          {"repo": repo, "query": "symbol benchmark"}))
            if not r["top_files"]:  # tiny repo: no lexical hit for that query
                r = payload(await s.call_tool("get_codebase_context",
                                              {"repo": "*", "query": "symbol benchmark"}))
            print("CTX top_files:", [(f.get("repo", "?").rsplit("/", 1)[-1], f["path"]) for f in r["top_files"]])
            assert r["top_files"] and r["recent_commits"]
            print("CTX related:", [(f["repo"].rsplit("/", 1)[-1], f["path"]) for f in r["related"]])

            r = payload(await s.call_tool("search_codebase_memory",
                                          {"repo": repo, "query": "test"}))
            print(f"SEARCH hits: {len(r)}", [h["name"] for h in r][:5])
            assert isinstance(r, list)

            target = next((h["path"] for h in r if h.get("path", "").endswith(".py")), None)
            if target is None:  # fallback: pick any indexed py file via CTX
                target = next(f["path"] for f in payload(await s.call_tool(
                    "get_codebase_context", {"repo": repo, "query": "0"}))["top_files"])
            r = payload(await s.call_tool("explain_file", {"repo": repo, "path": target}))
            print("EXPLAIN", target, "commits:", len(r["commits"]))
            assert r["commits"], "expected at least one commit touching the file"

            r = payload(await s.call_tool("team_activity", {"repo": repo}))
            print("TEAM:", r)
            assert r

            symbol = payload(await s.call_tool("search_codebase_memory",
                                               {"repo": repo, "query": "def"}))[0]["name"]
            r = payload(await s.call_tool("find_callers", {"repo": repo, "symbol": symbol}))
            print(f"CALLERS of {symbol}:", r[:3])
            assert isinstance(r, list)

            r = payload(await s.call_tool("change_impact", {"repo": repo, "path": target}))
            print("IMPACT of", target, ":", r["impacted"][:3], "symbols:", len(r["symbols"]))
            assert {"root", "impacted", "symbols"} <= set(r)

            r = payload(await s.call_tool("reindex", {"repo": repo}))
            print("REINDEX:", r)
            assert {"changed", "indexed_commit"} <= set(r)
    print("E2E OK: 7-tool wire contract proven over stdio")


asyncio.run(main(sys.argv[1:]))
