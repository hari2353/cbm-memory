---
name: codebase-memory-mcp
description: Turn any local git repo into persistent codebase memory for your coding agent - commits, files, symbols and a call graph in a queryable graph served over MCP (stdio). Use this skill whenever the user asks to index a repo, give the agent codebase/project/repo memory, remember the codebase, find where something is implemented, find a symbol by name, find who calls a function, check what breaks if a file changes, see who wrote or last changed a file, review recent changes, list main contributors, refresh an index after new commits, or set up the codebase-memory-mcp MCP server in Claude Code, Cursor, Copilot or any other MCP client - even if they don't name the server.
---

# Codebase Memory (MCP server)

codebase-memory-mcp turns a local git repo into **codebase memory**: commits,
files, symbols, call sites and imports projected into a small SQLite graph,
exposed as 7 MCP tools over stdio. One tool call answers "where is X",
"who wrote Y", "what changed recently", "who calls Z" or "what breaks if I
edit this file" - instead of grepping and reading files one by one.

Repo: https://github.com/hari2353/cbm-memory (MIT)

## How the agent should use it

Once the `codebase-memory` MCP server is configured, prefer these tools over
repeated grep/read for structural questions; then read only the returned
files for the actual code. The graph finds the code - it does not replace
reading it.

| Tool | Call when | Args | Returns |
|---|---|---|---|
| `get_codebase_context` | Default entry point for any work question ("where do I add X", "how does checkout work") | `repo="*"`, `query` (2-4 keywords) | `{"top_files": [{path, rank}], "recent_commits": [{subject, occurred_at}]}` |
| `search_codebase_memory` | Find symbols by name fragment ("where is the OrderProcessor class"), or words from file contents | `repo="*"`, `query` | `[{name, path, kind}]` |
| `explain_file` | History/ownership of one file ("who's been changing this file") | `repo="*"`, `path` (repo-relative) | `{path, symbols, commits: [{subject, author_name, occurred_at}]}` |
| `team_activity` | Contributor overview | `repo="*"` | `[{author_name, commits}]` |
| `find_callers` | "Who calls `helper()`" before changing its signature | `repo="*"`, `symbol` (exact name) | `[{caller, path}]` |
| `change_impact` | "What breaks if I edit this file" - blast radius before a refactor | `repo="*"`, `path` | `{"root", "symbols", "impacted": [{path, depth}]}` |
| `reindex` | After new commits/edits during a long session (cheaper than restarting the server) | `repo="*"` | `{"changed", "indexed_commit"}` |

Notes:

- `repo` is a required argument; `"*"` matches whatever this server indexed
  and is always the right value (one server hosts one repo).
- The graph is a snapshot from server start + `reindex` calls. After heavy
  changes, call `reindex` - no restart needed.
- Symbols come from tree-sitter (offline grammars) for Python, JS/TS/TSX,
  Go, Rust, Java, C/C++, Ruby, PHP; Markdown headings are regex. Call and
  import edges are name-level: a callee name matching several symbols links
  all of them (recall over precision).
- `search_codebase_memory` first does a symbol `LIKE %fragment%` match;
  if nothing matches it falls back to full-text search over file bodies and
  returns the symbols of the best-matching files. Shorter fragments match more.
- `get_codebase_context` ranks files by lexical match × PageRank over
  import/call edges, so widely-imported hub files win ties.
- `find_callers` takes the exact symbol name; `change_impact` takes the
  repo-relative path (both tolerate any `repo` string).
- Outputs are bounded to protect the agent's context: `team_activity` returns
  the top 50 contributors, `find_callers` up to 50 callers, `change_impact`
  up to 50 impacted files (with a `truncated` flag), `explain_file` the 20
  most recent commits, `search_codebase_memory` 20 rows.

## Setup (per project, run by the agent)

Zero-setup install via [uv](https://docs.astral.sh/uv/): no venv, no pip, no
absolute paths - `uvx` fetches, builds and caches the server from GitHub
automatically (it even manages Python itself). Prerequisite: `git` on PATH.

1. Make sure `uv` exists (skip if `uvx --version` works):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

2. Register the server. It indexes the repo passed as its only argument, so
   **each project needs its own entry** with the repo's absolute path.
   **Claude Code - one command:**

```bash
claude mcp add codebase-memory -- uvx --from git+https://github.com/hari2353/cbm-memory cbm-memory /absolute/path/to/the/repo
```

   Any other MCP client - copy `mcp-config.example.json` (next to this file):

```json
{
  "mcpServers": {
    "codebase-memory": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/hari2353/cbm-memory", "cbm-memory", "/absolute/path/to/the/repo"]
    }
  }
}
```

   (Cursor: paste into `~/.cursor/mcp.json`; Codex/Copilot CLI: same shape.)

3. Restart the agent (or reload MCP servers) so it picks up the config.

4. Verify: ask the agent what MCP tools it sees. You should see exactly 7:
   `get_codebase_context`, `search_codebase_memory`, `explain_file`,
   `team_activity`, `find_callers`, `change_impact`, `reindex`. Then ask it
   to answer a question about the repo (e.g. "where is the main entry
   point?") using `get_codebase_context`.

**Privacy: everything stays on your machine.** Indexing and the SQLite graph
run locally; repo code, the index and tool results are never uploaded
anywhere. (No `uv` or prefer explicit installs? Fallback: `python3 -m venv
~/.cbm-mcp-venv && ~/.cbm-mcp-venv/bin/pip install
"git+https://github.com/hari2353/cbm-memory.git"`, then point the
MCP client at `~/.cbm-mcp-venv/bin/python -m cbm_memory.mcp_server
<repo>`.)

## Troubleshooting

- Tools missing → server not configured or agent not restarted; recheck the
  config JSON and restart.
- Result contains "no graph loaded" → the server was started without the
  repo path argument; add it to `args`.
- Empty results → wrong repo path in `args`, or the repo has no commits yet.
- Server exits at startup → the repo path is not a git repo; the path must
  contain a `.git` directory.
- Stale results after big edits → call the `reindex` tool; it applies the
  git diff + status incrementally.
