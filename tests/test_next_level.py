"""Next-level specs: T04 tree-sitter symbols, T05 call graph + impact tools,
T06 FTS search + reindex, T07 PageRank ranking, T09 junk-dir hygiene."""
import textwrap

import pytest

from git_fixtures import run_git

from cbm_memory.graph import GitNexusGraph
from cbm_memory.repoindex import index_repo


def make_repo(tmp_path, files: dict[str, str], commit: str = "feat: initial"):
    root = tmp_path / "repo"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    for path, text in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", commit)
    return root


def graph_for(repo) -> GitNexusGraph:
    g = GitNexusGraph()
    g.load(index_repo(repo))
    return g


# ---------- T09: junk dirs never enter the index ----------

def test_index_ignores_junk_dirs(tmp_path):
    repo = make_repo(tmp_path, {
        "app.py": "def main():\n    pass\n",
        "build/lib/junk.py": "def junky():\n    pass\n",
        "node_modules/pkg/index.js": "function nm() {}\n",
        "dist/junk2.py": "def junky2():\n    pass\n",
        ".venv/lib/junk3.py": "def junky3():\n    pass\n",
    })
    data = index_repo(repo)
    paths = {t["path"] for t in data["tree"]}
    assert "app.py" in paths
    assert not any(p.startswith(("build/", "node_modules/", "dist/", ".venv/")) for p in paths)


# ---------- T04: tree-sitter symbols (multi-language, no string false-positives) ----------

def test_tree_sitter_symbols_go(tmp_path):
    repo = make_repo(tmp_path, {"srv.go": textwrap.dedent("""\
        package main

        import "fmt"

        type Server struct {
            Port int
        }

        func (s *Server) Start() {
            fmt.Println("start")
        }

        func NewServer(port int) *Server {
            return &Server{Port: port}
        }
        """)})
    syms = {s["name"]: s["kind"] for s in index_repo(repo)["symbols"]}
    assert syms.get("Start") == "function"
    assert syms.get("NewServer") == "function"
    assert syms.get("Server") == "class"


def test_tree_sitter_symbols_rust_and_java(tmp_path):
    repo = make_repo(tmp_path, {
        "lib.rs": "pub fn boot() -> u32 { 1 }\n\npub struct Widget { a: u32 }\n",
        "Foo.java": "package com.x;\npublic class Foo {\n    public void bar() { }\n}\n",
    })
    syms = {s["name"]: s["kind"] for s in index_repo(repo)["symbols"]}
    assert syms.get("boot") == "function"
    assert syms.get("Widget") == "class"
    assert syms.get("Foo") == "class"
    assert syms.get("bar") == "function"


def test_symbols_ignore_strings_and_comments(tmp_path):
    src = "x = 'def not_a_fn(): pass'\n# class NotAClass\ndef real():\n    pass\n"
    repo = make_repo(tmp_path, {"app.py": src})
    names = {s["name"] for s in index_repo(repo)["symbols"]}
    assert "not_a_fn" not in names
    assert "NotAClass" not in names
    assert "real" in names


def test_regex_fallback_when_tree_sitter_unavailable(tmp_path, monkeypatch):
    import cbm_memory.symbols_ts as symbols_ts
    repo = make_repo(tmp_path, {"app.py": "def greet(name):\n    return name\n"})
    monkeypatch.setattr(symbols_ts, "extract_symbols",
                        lambda path, src: (_ for _ in ()).throw(RuntimeError("no pack")))
    names = {s["name"] for s in index_repo(repo)["symbols"]}
    assert "greet" in names


# ---------- T05: call graph + find_callers + change_impact ----------

def test_find_callers(tmp_path):
    repo = make_repo(tmp_path, {
        "util.py": "def helper(x):\n    return x\n",
        "main.py": "from util import helper\n\ndef run():\n    return helper(1)\n",
    })
    g = graph_for(repo)
    out = g.find_callers("*", "helper")
    assert {c["path"] for c in out} == {"main.py"}
    assert {c["caller"] for c in out} == {"run"}


def test_change_impact_follows_calls_and_imports(tmp_path):
    repo = make_repo(tmp_path, {
        "lib.py": "def core():\n    return 1\n",
        "mid.py": "from lib import core\n\ndef run():\n    return core()\n",
        "main.py": "from mid import run\n\ndef entry():\n    return run()\n",
        "lone.py": "def unrelated():\n    pass\n",
    })
    g = graph_for(repo)
    out = g.change_impact("*", "lib.py")
    impacted = {f["path"]: f["depth"] for f in out["impacted"]}
    assert impacted.get("mid.py") == 1
    assert impacted.get("main.py") == 2
    assert "lone.py" not in impacted


# ---------- T06: FTS search over file bodies + reindex ----------

def test_fts_search_finds_symbols_by_body_words(tmp_path):
    repo = make_repo(tmp_path, {
        "kafka_consumer.py": "class Consumer:\n    \"\"\"polls kafka streams\"\"\"\n    def poll(self):\n        pass\n",
        "unrelated.py": "def zz():\n    pass\n",
    })
    g = graph_for(repo)
    out = g.search_codebase_memory("*", "kafka")
    paths = {r["path"] for r in out}
    assert "kafka_consumer.py" in paths
    assert "unrelated.py" not in paths
    assert all({"name", "path", "kind"} <= set(r) for r in out)


def test_reindex_picks_up_new_commits(tmp_path):
    repo = make_repo(tmp_path, {"app.py": "def one():\n    pass\n"})
    g = graph_for(repo)
    assert {s["name"] for s in g.search_codebase_memory("*", "one")} == {"one"}

    (repo / "two.py").write_text("def two():\n    pass\n")
    (repo / "app.py").unlink()
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "feat: two")

    out = g.reindex("*")
    assert out["changed"] >= 2
    assert {s["name"] for s in g.search_codebase_memory("*", "two")} == {"two"}
    assert {s["name"] for s in g.search_codebase_memory("*", "one")} == set()
    assert out["indexed_commit"]
    # deleted file: current-state views empty, no crash
    assert g.explain_file("*", "app.py")["symbols"] == []


# ---------- T07: PageRank beats lexical relevance ----------

def test_pagerank_beats_lexical_relevance(tmp_path):
    # hub.py is imported by 3 files (high PageRank, 1 lexical hit);
    # heavy.py has 2 lexical hits but no importers. Rank must put hub first.
    repo = make_repo(tmp_path, {
        "hub.py": "def shared_core():\n    return 1\n",
        "a.py": "from hub import shared_core\n\ndef a1():\n    return shared_core()\n",
        "b.py": "from hub import shared_core\n\ndef b1():\n    return shared_core()\n",
        "c.py": "from hub import shared_core\n\ndef c1():\n    return shared_core()\n",
        "heavy.py": "def shared_core():\n    return 2\n\ndef shared_core_extra():\n    return 3\n",
    })
    g = graph_for(repo)
    ctx = g.get_codebase_context("*", "shared_core")
    assert ctx["top_files"][0]["path"] == "hub.py"


# ---------- token safety: tool outputs are bounded (agent context = budget) ----------

def test_team_activity_bounded_sorted(tmp_path):
    repo = make_repo(tmp_path, {"app.py": "def one():\n    pass\n"})
    for i in range(60):
        run_git(repo, "commit", "-q", "--allow-empty", "-m", f"c{i}",
                env_user=f"U{i}", env_email=f"u{i}@x.dev")
    g = graph_for(repo)
    out = g.team_activity("*")
    assert len(out) <= 50
    counts = [r["commits"] for r in out]
    assert counts == sorted(counts, reverse=True)  # top contributors first


def test_md_heading_strips_anchor(tmp_path):
    from cbm_memory.repoindex import scan_symbols
    assert scan_symbols("docs.md", "## Async Tests { #async-tests }\n") == [("heading", "Async Tests")]


def test_explain_file_caps_commits(tmp_path):
    repo = make_repo(tmp_path, {"app.py": "def one():\n    pass\n"})
    for i in range(24):
        (repo / "app.py").write_text(f"def one():\n    return {i}\n")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", f"chore: bump {i}")
    g = graph_for(repo)  # full index: all 25 commits enter the graph
    out = g.explain_file("*", "app.py")
    assert len(out["commits"]) <= 20          # bounded, most recent kept
    assert {c["subject"] for c in out["commits"]} >= {"chore: bump 23"}


def test_find_callers_bounded(tmp_path):
    files = {"lib.py": "def core():\n    return 1\n"}
    for i in range(60):
        files[f"c{i:02d}.py"] = "from lib import core\n\ndef f%d():\n    return core()\n" % i
    repo = make_repo(tmp_path, files)
    g = graph_for(repo)
    out = g.find_callers("*", "core")
    assert len(out) <= 50
    assert out == sorted(out, key=lambda x: (x["path"], x["caller"]))  # deterministic


def test_change_impact_bounded_and_flags_truncation(tmp_path):
    files = {"lib.py": "def core():\n    return 1\n"}
    for i in range(60):
        files[f"c{i:02d}.py"] = "from lib import core\n\ndef f%d():\n    return core()\n" % i
    repo = make_repo(tmp_path, files)
    g = graph_for(repo)
    out = g.change_impact("*", "lib.py")
    assert len(out["impacted"]) <= 50
    assert out["truncated"] is True
    assert {"root", "impacted", "symbols"} <= set(out)


# ---------- wire surface: the new tools exist on the MCP app ----------

def test_mcp_app_registers_seven_tools():
    import asyncio

    from cbm_memory import mcp_server
    mcp = mcp_server.build_mcp()
    listed = mcp.list_tools()
    tools = asyncio.run(listed) if asyncio.iscoroutine(listed) else listed
    names = {t.name for t in tools}
    assert names == {"get_codebase_context", "search_codebase_memory", "explain_file",
                     "team_activity", "find_callers", "change_impact", "reindex"}
