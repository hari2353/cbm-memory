# cbm-memory

*(formerly `codebase-memory-mcp` — renamed to avoid the PyPI name collision
with DeusData's dist; `uvx` URLs may still use the old repo name, GitHub
redirects it.)*

MCP server that turns any local git repo into **codebase memory**: commits,
files, symbols, call sites and imports projected into a tiny SQLite graph,
served over stdio as 7 MCP tools. Everything runs locally — no code, index or
results ever leave your machine.

`mcp-name: io.github.hari2353/codebase-memory-mcp`

## Tools

| Tool | What it answers |
|---|---|
| `get_codebase_context(repo, query)` | most relevant files (symbol match × PageRank over import/call edges) + recent commits |
| `search_codebase_memory(repo, query)` | symbols by name fragment, then full-text search over file bodies (FTS5) |
| `explain_file(repo, path)` | file provenance: symbols + commits that modified it |
| `team_activity(repo)` | commits per author |
| `find_callers(repo, symbol)` | who calls this symbol — caller functions + files (call graph) |
| `change_impact(repo, path)` | blast radius of editing a file: impacted files by dependency depth |
| `reindex(repo)` | incremental refresh from git (diff + status) after new commits |

`repo = "*"` scopes across every indexed repo in the same server. Pass any
string the server indexed with (e.g. `.`); keys are resolved server-side.

## Usage

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# serve one repo (stdio MCP)
.venv/bin/python -m cbm_memory.mcp_server /path/to/repo

# wire-level proof: spawns the server, calls all 7 tools over stdio
.venv/bin/python scripts/e2e_client.py /path/to/repo
```

Unit tests: `.venv/bin/python -m pytest -q` (fixture git repos, 38 tests).

## Signals (graph-memory-platform integration)

`cbm_memory.signals.to_signals(index, sink)` emits CloudEvents
(`specversion`, `id`, `source`, `type`, `time`, `partitionkey`) shaped for
`POST /v1/signals:batch`. Sinks: `InMemorySink` (tests) and `GmpHttpSink`
(real platform). Signal types: `codebase.commit.v1`, `codebase.file_changed.v1`,
`codebase.symbol_seen.v1`.

## Design notes

- Symbols, calls and imports come from tree-sitter grammars
  (`tree-sitter-language-pack`, bundled offline) for 11 languages
  (py/js/ts/tsx/go/rs/java/c/cpp/rb/php); md headings stay regex. Extraction
  is fail-soft per language and per component — a missing query degrades to
  the regex scan, never crashes indexing.
- Callee edges are name-level: a callee name matching several symbols links
  all of them (recall over precision — v1 trade-off, no scope resolution).
- Ranking: PageRank (damping 0.85, 20 iters) over import + call edges boosts
  hub files; exact symbol-name matches outrank substring hits in lexical score.
- Full-text search is FTS5 with external content + triggers; `reindex`
  applies git diff/status incrementally and forgets deleted files (modified
  history is kept).
- Junk dirs (`build/`, `dist/`, `node_modules/`, `.venv/`, caches…) never
  enter the index; venv, crawl/session state and secrets never enter the repo.

## Three memory tiers

`MemoryGraph` keeps durable entities/episodes plus consolidated observations.
The working tier is a bounded scratchpad for in-flight agent state:

```python
g.remember_working("task", "fix auth retry", ttl_seconds=900, importance=0.8)
g.recall_working("task")        # None once expired
g.working_items()               # non-expired, importance desc
g.promote_working("task", "auth-service")   # -> durable episode, scratch row removed
g.evict_expired()               # sweep count
```

TTL and importance are validated; timestamps are ISO-8601 UTC and `now=` is
injectable for deterministic tests. No background sweeper — callers evict.

## Index + memory footprint

`cbm_memory.footprint` reports the size of the memory an agent carries:

```python
from cbm_memory.footprint import estimate_index_bytes, summarize_index

summarize_index(index_repo(path))   # files, symbols, call/import edges, commits, estimated_bytes
graph.footprint()                   # entities, episodes, working items, estimated bytes
```

Counts are exact; byte figures are order-of-magnitude only. Cross-pollinated
from Harvard Edge's TinyML-Footprint, applying the same "report your
footprint" discipline to a codebase memory index.

## Streaming context window (MIT HAN Lab StreamingLLM cross-pollination)\n\n`cbm_memory.context_window.select_streaming_window` and `MemoryGraph.context_window`\nkeep the first few episodes (attention sinks) plus the newest window and drop\nthe middle, giving a deterministic context budget for long-running sessions:\n\n```python\nfrom cbm_memory.context_window import select_streaming_window\nselect_streaming_window(items, sinks=2, recent=6)\ngraph.context_window(\"svc\", sinks=2, recent=6)\n```\n\nNo tokenizer dependency; counts are exact and bounded.\n\n## How it differs from Serena / GitNexus / codebase-memory (DeusData)

| | this server | Serena (~31k★) | GitNexus (~30k★) | DeusData codebase-memory-mcp |
|---|---|---|---|---|
| License | MIT | MIT-ish | **PolyForm-NC (non-commercial)** | MIT |
| Git history as memory (authors/commits/blast radius) | **yes** | no | no | partial |
| Setup | `uvx` one line, stdio, no daemon | LSP servers per language | Neo4j/embeddings, heavy | 162 grammars, heavier |
| Privacy | **all local, sqlite in-process** | local | local | local |
| Focus | agent memory: where/who/what-changed/who-calls | IDE-grade LSP ops | code exploration UI | broad code ops |

Niche: a zero-setup, privacy-first *memory* layer — structural recall
(who calls what, what breaks when this changes, who owns this file) rather
than another language server.
