# Design: codebase-memory-mcp — next level (T03–T09)

Date: 2026-11-15. Author: Hari (with agent research).
Status: proposed — build order needs approval.

## Intent

The 2026 market for "code intelligence for AI agents over MCP" has consolidated.
Buyers now expect tree-sitter-grade symbols, a call graph with impact analysis,
real search, and a fresh index — from a local-first MIT tool. Our repo has the
unique angle (git memory: file provenance, team activity, platform signals) but
is one category generation behind on extraction quality, structure, and
distribution. This design closes the gap in the order that buys the most
adoption per day of work, and sets up the Pro tier for monetization.

## Market map (researched 2026-11-15)

Direct/local competitors:

| Tool | Graph core | Languages | Search | Git history | Memory layer | License | Traction |
|---|---|---|---|---|---|---|---|
| Serena (~31k★) | LSP live, no graph | 40+ | symbols/refs | no | .serena markdown notes | GPL-3 | huge |
| GitNexus (~30k★) | Kuzu graph, blast radius, wiki | 18 | Cypher | diff impact | none | PolyForm NC | big |
| code-review-graph (13.5k★) | tree-sitter, SQLite+FTS5 | ~35 | FTS5+RRF+embeddings | no | lite | MIT | big |
| **DeusData codebase-memory-mcp** | 162 grammars + LSP, SQLite, 17 tools | 162 | graph queries | watch mode, snapshots | shallow | MIT | featured in comparison blogs |
| repowise | tree-sitter 28 langs | 28 | ten core tools | **hotspots, co-change, ownership** | decisions | AGPL-3 + $15/mo hosted | growing |
| CodeGraph | Rust kernel, 1 explore tool | 20+ | call paths | watcher | none | MIT | growing |
| Sverklo | SQLite+ONNX | 12 | BM25+embeddings+PageRank | SHA-pinned diffs | bi-temporal | MIT | new |
| trace-mcp | polyglot graph | many | impact traversal | decisions bound to symbol IDs | verified non-stale | MIT | new |
| jCodeMunch (1.7k★) | tree-sitter+SCIP | 70+ | 90+ tools | no | none | $79–$1999 commercial | niche |
| Sourcegraph MCP | hosted SCIP | all | search/refs/diffs | commit search | none | $16k/yr | enterprise |
| Augment Context Engine | hosted semantic | broad | embeddings | recent commits | none | $20/mo | hosted |

Category takeaways:
1. Table stakes 2026: tree-sitter (or LSP) symbols, call graph + impact, FTS or
   embeddings, watcher/incremental freshness, local-first, MIT.
2. Only repowise, trace-mcp, GitNexus (and us) treat git history as first-class.
   repowise charges $15/mo for "history and judgement a graph cannot hold" —
   that validates our explain_file / team_activity direction.
3. DeusData ranks in third-party comparison articles under OUR product name.
   They own the mindshare for "codebase-memory-mcp". We must publish and market
   under `cbm-memory` (PyPI name verified free) and differentiate in copy.
4. Aider's repo-map (PageRank over dep graph, token-budgeted) is the cheap,
   proven ranking idea worth copying.

## Grade card (ours vs category bar)

| Metric | Us | Bar | Grade |
|---|---|---|---|
| Price | free, MIT | free–$16k/yr | A |
| Privacy (local-first) | fully local | mostly local | A |
| Setup | uvx one-liner (v0.2.0) | uvx/npx one-liners | B+ |
| Symbol quality | regex, 3 langs | tree-sitter/LSP, 18–162 langs | D |
| Call graph / impact | none (SKILL.md says don't) | expected | F |
| Search | LIKE %fragment% | FTS5/embeddings+RRF | D- |
| Index freshness | snapshot at server start | watcher/incremental | C- |
| Git memory (provenance, team) | unique, 4-tool-native | rare; repowise $15/mo | A- |
| Memory layer | built (memorygraph.py), unexposed | Sverklo/trace ship it | C |
| Distribution | no PyPI, no registry, 0★, no topics | PyPI+registry+lists | D |
| Repo hygiene | build/ dirs indexed; README shows stale venv flow | clean | C- |
| Docs/tests | SPEC.md, README, 12 tests, e2e script | comparable | B |

Overall: C+ idea, C implementation, A direction. The gaps are additive work,
not a rewrite — SQLite schema, 4-tool API, and e2e harness all survive.

## Portfolio grade (github.com/hari2353)

| Repo | Verdict |
|---|---|
| codebase-memory-mcp | **the horse.** Market-validated category, real product, needs T03–T07. |
| graph-memory-platform | **the moat.** Memory/decision layer (signals, memory graph) — Sverklo and trace-mcp prove the category wants exactly this. Feeds T08 Pro. |
| skills-mcp | adjacent option: a skills runtime for marketplaces like Agensi. Park until codebase-memory wins. |
| clickgraph, india-quant-research, localavatar-pipeline, stair-vs-rag, cross-platform-migration-kit, skill-detect | different markets; out of scope here. |

## Goals / non-goals / anti-goals

Goals: category-parity symbols and call graph; discoverable (PyPI, MCP
Registry, awesome lists); fresh index; ranked context; a $9.99 Pro tier
packaged from already-built modules — without breaking the free 4-tool core or
the uvx zero-setup path.

Non-goals: LSP servers (Serena owns precision editing; we own memory), hosted
service, embeddings-in-v1 (FTS5 first), Neo4j/Kuzu backends, IDE plugins.

Anti-goals (failure modes): becoming DeusData-clone-by-name (publish as
cbm-memory); breaking stdio contract or the 4-tool wire format; shipping Pro
before traction signals exist.

## Approaches considered

- **A. Feature-parity race** — build tree-sitter + call graph + embeddings to
  match the field first, market later. Trades: 2+ weeks of pure code before any
  adoption signal; market already has 9 graph servers.
- **B. Distribution-first, then table stakes** (chosen) — T03/T09 in one day
  (registry presence, hygiene), then T04→T05→T06→T07 in dependency order, T08
  Pro gated on traction. Trades: short-term feature embarrassment in exchange
  for every later feature shipping into an indexed, listed product.
- **C. Monetize now** — package Pro today. Trades: charges into a 0-use
  listing; category data says paid skills get 1–15 uses vs 187–332 free.

Chosen: B. Reason: our only losing metrics are distribution and parity; B
fixes distribution first (cheap, compounding) and sequences parity work so
each spec ships to an audience.

## Specs

### T03 — Distribution & registry presence (1 day, no product code)
- Problem: invisible. No PyPI, no MCP Registry entry, no repo description or
  topics, README still shows the venv flow.
- Work: GitHub description + topics (`mcp`, `code-intelligence`, `llm`,
  `sqlite`, `git`); README rewrite (uvx first, competitor-differentiation
  table, privacy line); `uv build` + publish to PyPI as **`cbm-memory`**
  (verified free); add `mcp-name: io.github.hari2353/codebase-memory-mcp`
  line to README (MCP Registry verification); `mcp-publisher` publish; Glama
  and Smithery submissions; PR to awesome-mcp-servers.
- Accept: `uvx cbm-memory <repo>` serves the 4 tools; registry entry live;
  repo discoverable in PyPI search.
- Needs user: PyPI account/token, GitHub device-code login (2 min each).

### T04 — Tree-sitter symbol extraction (2–3 days)
- Problem: regex only covers py/ts/md; every serious competitor parses 18–162
  languages. Wrong symbols poison everything downstream.
- Design: add `tree-sitter-language-pack` (pip wheels, no compiler); lazy
  import with regex fallback so uvx never breaks; per-language capture queries
  for functions/classes/methods/structs; first wave 12 languages: py, js, ts,
  tsx, jsx, go, rs, java, c, cpp, cs, rb, php. SQLite `symbols` schema
  unchanged (name, path, kind, line).
- Accept: recall ≥ regex baseline on repo fixtures; no symbol false-positives
  from strings/comments; 5k-file repo indexes < 5 s; 12 existing tests pass;
  new fixture tests for ≥3 non-python languages.

### T05 — Call graph + impact tools (3–4 days) — kills the competitor selling point
- Problem: Codebase Index sells "locates call sites"; our SKILL.md tells the
  agent NOT to answer "who calls X". This is the category's most-advertised
  capability and our hard zero.
- Design: extract name-level call edges during T04 parse (typed when cheap);
  store edges (caller_path, caller_symbol, callee_name); new tools:
  `find_callers(repo, symbol)` → ranked callers; `change_impact(repo, path)` →
  transitive callers + importers of a file's symbols. Keep total tool count ≤6
  (SKILL.md stays lean).
- Accept: on this repo, callers of shared helpers include the real e2e/test
  files; on a TS fixture, top-5 callers ≥80% correct vs manual check; wire
  contract extended in scripts/e2e_client.py.

### T06 — Real search + index freshness (2 days)
- Problem: `LIKE %fragment%` is not search; index is a snapshot at server
  start, so agents use stale memory after commits.
- Design: SQLite FTS5 table over file text + symbol names ( Ships in SQLite
  core; zero deps); `search_codebase_memory` → FTS5 query with LIKE fallback;
  store `indexed_commit_sha` in a meta table; new `reindex` tool does
  incremental update via `git diff --name-only <old_sha>` (delete/reindex
  changed files); doc the "restart or reindex" choice. Optional watcher later.
- Accept: after a new commit, `reindex` picks up changes without restart;
  FTS5 returns relevant files for multi-word queries LIKE misses.

### T07 — Ranked context, Aider-style (1 day)
- Problem: `top_files` ranks by regex hits; Aider proved PageRank-over-deps is
  the cheap 10× here.
- Design: build file→file import graph during index; PageRank (pure python,
  ~40 lines); `get_codebase_context` ranks by `query_relevance × page_rank`;
  respect a token budget in the payload.
- Accept: on this repo, entry points (mcp_server.py, repoindex.py) outrank
  build artifacts and tests for generic queries.

### T08 — Pro tier packaging (2–3 days, ship gated on traction)
- Problem: memorygraph.py (retain/recall/reflect), obsidian.py, convert.py
  (PDF→md), signals.py are built but unexposed — the $9.99 tier sits unbundled.
- Design: second console script `cbm-pro` in the same package; new Agensi skill
  zip "codebase-memory-pro" ($9.99): persistent memory across sessions,
  Obsidian vault ingestion, PDF→markdown corpus. Free core untouched. Ship
  trigger: ~100 uses or mostly-positive agent feedback on Agensi.
- Accept: `uvx --from git+... cbm-pro <repo>` runs all Pro tools over stdio
  (same harness as e2e_client.py); Pro listing live and security-scanned.

### T09 — Hygiene & CI (1 day, do first with T03)
- Problem: our own e2e ranks `build/lib/**` files (garbage in graph); no CI;
  README workflow stale.
- Design: default ignore set (build/, dist/, .venv/, node_modules/, .git/,
  __pycache__, .cbm*) in repoindex.py; GitHub Actions: pytest + e2e_client.py
  against a fixture repo on push; README updated to uvx flow (part of T03).
- Accept: e2e top_files contains no build/ paths; CI green badge on README.

## Build order & success metrics

Order: **T09 + T03 → T04 → T05 → T06 → T07 → T08** (T08 gated on traction).
T04 unblocks T05; T06 and T07 are independent of T05 and can interleave.

Metrics: GitHub stars and PyPI downloads (T03+); Agensi views/uses/agent
feedback pass rate (weekly check); symbol recall and index time fixtures
(T04); callers precision on fixtures (T05).

## Risks

- DeusData ships fast under our product name — mitigate: publish cbm-memory
  fast, differentiate copy on git-memory + local privacy.
- tree-sitter-language-pack wheel size (~40 MB) slows first uvx run — mitigate:
  measure; if heavy, load grammars per-language lazily or trim wave to 8 langs.
- One-person bandwidth — mitigate: specs are ≤4 days each, T05 can split.
- Pro tier sold too early kills free funnel — mitigated by the explicit gate.

## What's left to the builder

Exact tree-sitter query syntax per language; FTS5 tokenizer choice; PageRank
damping constant; whether `reindex` is a tool or a client-visible note in
get_codebase_context output. All are builder-level calls within the spec's
acceptance criteria.
