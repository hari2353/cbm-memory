"""Next-level specs: T04 tree-sitter symbols, T05 call graph + impact tools,
T06 FTS search + reindex, T07 PageRank ranking, T09 junk-dir hygiene."""

import textwrap


from git_fixtures import run_git

from cbm_memory.graph import GitNexusGraph
from cbm_memory.repoindex import index_repo


def make_repo(tmp_path, files: dict[str, str], commit: str = "feat: initial"):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
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
    repo = make_repo(
        tmp_path,
        {
            "app.py": "def main():\n    pass\n",
            "build/lib/junk.py": "def junky():\n    pass\n",
            "node_modules/pkg/index.js": "function nm() {}\n",
            "dist/junk2.py": "def junky2():\n    pass\n",
            ".venv/lib/junk3.py": "def junky3():\n    pass\n",
        },
    )
    data = index_repo(repo)
    paths = {t["path"] for t in data["tree"]}
    assert "app.py" in paths
    assert not any(
        p.startswith(("build/", "node_modules/", "dist/", ".venv/")) for p in paths
    )


# ---------- T04: tree-sitter symbols (multi-language, no string false-positives) ----------


def test_tree_sitter_symbols_go(tmp_path):
    repo = make_repo(
        tmp_path,
        {
            "srv.go": textwrap.dedent("""\
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
        """)
        },
    )
    syms = {s["name"]: s["kind"] for s in index_repo(repo)["symbols"]}
    assert syms.get("Start") == "function"
    assert syms.get("NewServer") == "function"
    assert syms.get("Server") == "class"


def test_tree_sitter_symbols_rust_and_java(tmp_path):
    repo = make_repo(
        tmp_path,
        {
            "lib.rs": "pub fn boot() -> u32 { 1 }\n\npub struct Widget { a: u32 }\n",
            "Foo.java": "package com.x;\npublic class Foo {\n    public void bar() { }\n}\n",
        },
    )
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
    monkeypatch.setattr(
        symbols_ts,
        "extract_symbols",
        lambda path, src: (_ for _ in ()).throw(RuntimeError("no pack")),
    )
    names = {s["name"] for s in index_repo(repo)["symbols"]}
    assert "greet" in names


# ---------- T05: call graph + find_callers + change_impact ----------


def test_find_callers(tmp_path):
    repo = make_repo(
        tmp_path,
        {
            "util.py": "def helper(x):\n    return x\n",
            "main.py": "from util import helper\n\ndef run():\n    return helper(1)\n",
        },
    )
    g = graph_for(repo)
    out = g.find_callers("*", "helper")
    assert {c["path"] for c in out} == {"main.py"}
    assert out[0]["callers"] == ["run"]


def test_change_impact_follows_calls_and_imports(tmp_path):
    repo = make_repo(
        tmp_path,
        {
            "lib.py": "def core():\n    return 1\n",
            "mid.py": "from lib import core\n\ndef run():\n    return core()\n",
            "main.py": "from mid import run\n\ndef entry():\n    return run()\n",
            "lone.py": "def unrelated():\n    pass\n",
        },
    )
    g = graph_for(repo)
    out = g.change_impact("*", "lib.py")
    impacted = {f["path"]: f["depth"] for f in out["impacted"]}
    assert impacted.get("mid.py") == 1
    assert impacted.get("main.py") == 2
    assert "lone.py" not in impacted


# ---------- T06: FTS search over file bodies + reindex ----------


def test_fts_search_finds_symbols_by_body_words(tmp_path):
    repo = make_repo(
        tmp_path,
        {
            "kafka_consumer.py": 'class Consumer:\n    """polls kafka streams"""\n    def poll(self):\n        pass\n',
            "unrelated.py": "def zz():\n    pass\n",
        },
    )
    g = graph_for(repo)
    out = g.search_codebase_memory("*", "kafka")
    paths = {r["path"] for r in out}
    assert "kafka_consumer.py" in paths
    assert "unrelated.py" not in paths
    assert all({"name", "kind"} <= set(s) for r in out for s in r["symbols"])


def test_reindex_picks_up_new_commits(tmp_path):
    repo = make_repo(tmp_path, {"app.py": "def one():\n    pass\n"})
    g = graph_for(repo)
    assert {
        s["name"] for r in g.search_codebase_memory("*", "one") for s in r["symbols"]
    } == {"one"}

    (repo / "two.py").write_text("def two():\n    pass\n")
    (repo / "app.py").unlink()
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "feat: two")

    out = g.reindex("*")
    assert out["changed"] >= 2
    assert out["changed_files"] and set(out["changed_files"]) >= {"two.py", "app.py"}
    assert {
        s["name"] for r in g.search_codebase_memory("*", "two") for s in r["symbols"]
    } == {"two"}
    assert {
        s["name"] for r in g.search_codebase_memory("*", "one") for s in r["symbols"]
    } == set()
    assert out["indexed_commit"]
    # deleted file: current-state views empty, no crash
    assert g.explain_file("*", "app.py")["symbols"] == []


# ---------- T07: PageRank beats lexical relevance ----------


def test_pagerank_beats_lexical_relevance(tmp_path):
    # hub.py is imported by 3 files (high PageRank, 1 lexical hit);
    # heavy.py has 2 lexical hits but no importers. Rank must put hub first.
    repo = make_repo(
        tmp_path,
        {
            "hub.py": "def shared_core():\n    return 1\n",
            "a.py": "from hub import shared_core\n\ndef a1():\n    return shared_core()\n",
            "b.py": "from hub import shared_core\n\ndef b1():\n    return shared_core()\n",
            "c.py": "from hub import shared_core\n\ndef c1():\n    return shared_core()\n",
            "heavy.py": "def shared_core():\n    return 2\n\ndef shared_core_extra():\n    return 3\n",
        },
    )
    g = graph_for(repo)
    ctx = g.get_codebase_context("*", "shared_core")
    assert ctx["top_files"][0]["path"] == "hub.py"


# ---------- architecture: multi-repo per server + concurrency + caching ----------


def test_index_repositories_multi_repo(tmp_path):

    from cbm_memory import mcp_server

    a = make_repo(tmp_path / "a", {"app.py": "def only_in_a():\n    pass\n"})
    b = make_repo(tmp_path / "b", {"app.py": "def only_in_b():\n    pass\n"})
    g = mcp_server.index_repositories([str(a), str(b)], workers=2)
    ctx = g.get_codebase_context("*", "only_in_b")
    assert ctx["top_files"][0]["path"] == "app.py"  # both have app.py; rank picks b's
    assert {
        s["name"]
        for r in (g.explain_file("*", "app.py")["repos"] or [""])
        for s in g.explain_file(r, "app.py")["symbols"]
    } >= {"only_in_a", "only_in_b"}
    assert mcp_server.get_graph() is g  # wired into the server


def test_reindex_star_refreshes_all_repos(tmp_path):
    from cbm_memory import mcp_server

    a = make_repo(tmp_path / "a", {"x.py": "def ax():\n    pass\n"})
    b = make_repo(tmp_path / "b", {"y.py": "def by():\n    pass\n"})
    g = mcp_server.index_repositories([str(a), str(b)])
    assert g.search_codebase_memory("*", "newinb") == []
    (b / "z.py").write_text("def newinb():\n    pass\n")
    run_git(b, "add", "-A")
    run_git(b, "commit", "-q", "-m", "feat: newinb")
    out = g.reindex("*")
    assert out["changed"] >= 1
    assert {
        s["name"] for r in g.search_codebase_memory("*", "newinb") for s in r["symbols"]
    } == {"newinb"}


def test_read_results_cached_until_mutation(tmp_path):
    repo = make_repo(tmp_path, {"app.py": "def one():\n    pass\n"})
    g = graph_for(repo)
    r1 = g.get_codebase_context("*", "one")
    r2 = g.get_codebase_context("*", "one")
    assert r1 is r2  # memoized (same object), not recomputed
    g.reindex("*")  # mutation invalidates
    r3 = g.get_codebase_context("*", "one")
    assert r3 is not r1


# ---------- token safety: tool outputs are bounded (agent context = budget) ----------


def test_team_activity_bounded_sorted(tmp_path):
    repo = make_repo(tmp_path, {"app.py": "def one():\n    pass\n"})
    for i in range(60):
        run_git(
            repo,
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            f"c{i}",
            env_user=f"U{i}",
            env_email=f"u{i}@x.dev",
        )
    g = graph_for(repo)
    out = g.team_activity("*")
    assert len(out) <= 50
    counts = [r["commits"] for r in out]
    assert counts == sorted(counts, reverse=True)  # top contributors first


def test_md_heading_strips_anchor(tmp_path):
    from cbm_memory.repoindex import scan_symbols

    assert scan_symbols("docs.md", "## Async Tests { #async-tests }\n") == [
        ("heading", "Async Tests")
    ]


def test_explain_file_caps_commits(tmp_path):
    repo = make_repo(tmp_path, {"app.py": "def one():\n    pass\n"})
    for i in range(24):
        (repo / "app.py").write_text(f"def one():\n    return {i}\n")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", f"chore: bump {i}")
    g = graph_for(repo)  # full index: all 25 commits enter the graph
    out = g.explain_file("*", "app.py")
    assert len(out["commits"]) <= 20  # bounded, most recent kept
    assert {c["subject"] for c in out["commits"]} >= {"chore: bump 23"}


def test_find_callers_bounded(tmp_path):
    files = {"lib.py": "def core():\n    return 1\n"}
    for i in range(60):
        files[f"c{i:02d}.py"] = (
            "from lib import core\n\ndef f%d():\n    return core()\n" % i
        )
    repo = make_repo(tmp_path, files)
    g = graph_for(repo)
    out = g.find_callers("*", "core")
    assert len(out) <= 50
    assert out == sorted(out, key=lambda x: (x["repo"], x["path"]))  # deterministic
    assert all(cs == sorted(cs) for cs in (x["callers"] for x in out))


def test_change_impact_bounded_and_flags_truncation(tmp_path):
    files = {"lib.py": "def core():\n    return 1\n"}
    for i in range(60):
        files[f"c{i:02d}.py"] = (
            "from lib import core\n\ndef f%d():\n    return core()\n" % i
        )
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
    assert names == {
        "get_codebase_context",
        "search_codebase_memory",
        "explain_file",
        "team_activity",
        "find_callers",
        "change_impact",
        "reindex",
    }


# ---------- cross-repo pollination: one repo's context answers another repo's question ----------


def test_get_codebase_context_scopes_to_repo_and_pollinates(tmp_path):
    from cbm_memory import mcp_server

    a = make_repo(tmp_path / "a", {"auth.py": "def sign_jwt():\n    pass\n"})
    b = make_repo(tmp_path / "b", {"api.py": "def verify_jwt():\n    pass\n"})
    g = mcp_server.index_repositories([str(a), str(b)])
    # scoped: a specific repo gets only its own files in top_files, each labeled
    ctx = g.get_codebase_context(str(a), "jwt")
    assert ctx["top_files"], "scoped query should hit repo a's auth.py"
    assert all(t["repo"] == str(a) for t in ctx["top_files"])
    assert ctx["top_files"][0]["path"] == "auth.py"
    # pollination: repo b's jwt hit is surfaced under related, labeled
    assert any(r["repo"] == str(b) and r["path"] == "api.py" for r in ctx["related"])
    # "*": blend of all repos, labeled so the agent can tell them apart
    all_ctx = g.get_codebase_context("*", "jwt")
    assert {t["repo"] for t in all_ctx["top_files"]} == {str(a), str(b)}


def test_explain_file_same_path_in_two_repos_not_mixed(tmp_path):
    from cbm_memory import mcp_server

    a = make_repo(tmp_path / "a", {"util.py": "def hash_pw():\n    pass\n"})
    b = make_repo(
        tmp_path / "b",
        {"util.py": "def encode_token():\n    pass\ndef hash_pw():\n    pass\n"},
    )
    g = mcp_server.index_repositories([str(a), str(b)])
    x = g.explain_file(str(b), "util.py")
    assert {s["name"] for s in x["symbols"]} == {"encode_token", "hash_pw"}
    assert x["repo"] == str(b)
    assert set(x["repos"]) == {str(a), str(b)}  # both repos own this path
    # pollination: a symbol defined in BOTH repos surfaces the other repo's copy
    y = g.explain_file(str(a), "util.py")
    assert {s["name"] for s in y["symbols"]} == {"hash_pw"}  # only repo a's copy
    assert any(r["repo"] == str(b) and r["name"] == "hash_pw" for r in y["related"])


def test_search_and_find_callers_label_their_repo(tmp_path):
    from cbm_memory import mcp_server

    a = make_repo(tmp_path / "a", {"x.py": "def parse_cfg():\n    pass\n"})
    b = make_repo(tmp_path / "b", {"y.py": "def parse_cfg():\n    pass\n"})
    g = mcp_server.index_repositories([str(a), str(b)])
    rows = g.search_codebase_memory("*", "parse_cfg")
    assert {r["repo"] for r in rows} == {str(a), str(b)}  # cross-repo by name, labeled
    callers = g.find_callers("*", "parse_cfg")
    assert all("repo" in c for c in callers)


def test_reindex_one_repo_does_not_delete_same_path_symbols_in_another(tmp_path):
    from cbm_memory import mcp_server

    a = make_repo(tmp_path / "a", {"util.py": "def hash_pw():\n    pass\n"})
    b = make_repo(tmp_path / "b", {"util.py": "def encode_token():\n    pass\n"})
    g = mcp_server.index_repositories([str(a), str(b)])
    g.reindex(str(a))  # incremental refresh of repo a only
    names = {s["name"] for s in g.explain_file(str(b), "util.py")["symbols"]}
    assert names == {"encode_token"}, (
        "repo a's reindex must not touch repo b's util.py symbols"
    )


def test_team_activity_merges_same_author_across_repos(tmp_path):
    from cbm_memory import mcp_server

    a = make_repo(tmp_path / "a", {"x.py": "def ax():\n    pass\n"})
    b = make_repo(tmp_path / "b", {"y.py": "def by():\n    pass\n"})
    run_git(
        a,
        "commit",
        "-q",
        "--amend",
        "--reset-author",
        "-m",
        "feat: ax",
        env_user="Same Person",
        env_email="same@x.io",
    )
    run_git(
        b,
        "commit",
        "-q",
        "--amend",
        "--reset-author",
        "-m",
        "feat: by",
        env_user="Same Person",
        env_email="same@x.io",
    )
    g = mcp_server.index_repositories([str(a), str(b)])
    acts = {r["author_name"]: r for r in g.team_activity("*")}
    assert acts["Same Person"]["commits"] == 2  # merged, not 1+1 split
    assert acts["Same Person"]["repos"] == 2


# ---------- cross-repo intelligence: semantic similarity + PageRank strength ----------


def test_related_prefers_high_pagerank_shared_symbols(tmp_path):
    """Generic names like 'handler' shouldn't imply sameness: a shared symbol
    that lives in the other repo's CORE file outranks one in a peripheral file."""
    from cbm_memory import mcp_server

    core = "def handler():\n    pass\n"
    b_files = {"lib/core.py": core}
    for i in range(25):  # many importers -> core.py gets high PageRank
        b_files[f"u{i:02d}.py"] = (
            "from lib.core import handler\n\ndef run%d():\n    handler()\n" % i
        )
    b = make_repo(tmp_path / "b", b_files)
    c = make_repo(tmp_path / "c", {"tiny.py": "def handler():\n    pass\n"})
    a = make_repo(tmp_path / "a", {"app.py": "def handler():\n    pass\n"})
    g = mcp_server.index_repositories([str(a), str(b), str(c)])
    rel = g.explain_file(str(a), "app.py")["related"]
    assert rel, "shared handler must surface from b and c"
    assert rel[0]["repo"] == str(b)  # core-file copy first, not peripheral
    assert rel[0]["rank"] > rel[-1]["rank"]  # strength is ranked, not flat


def test_semantic_related_finds_topic_matches_without_shared_names(tmp_path):
    """Pollination by TOPIC: repos share vocabulary but no symbol names."""
    from cbm_memory import mcp_server

    a = make_repo(tmp_path / "a", {"svc.py": "def do_a():\n    pass\n"})
    b = make_repo(
        tmp_path / "b",
        {
            "docs/notes.md": "# Notes\nOAuth token refresh grant rotation for the auth service.\n"
        },
    )
    (a / "svc.py").write_text(
        "def do_a():\n    pass\n\n# TODO: refresh token rotation, oauth grant flow\n"
    )
    run_git(a, "add", "-A")
    run_git(a, "commit", "-q", "-m", "feat: oauth notes")
    g = mcp_server.index_repositories([str(a), str(b)])
    ctx = g.get_codebase_context(str(a), "oauth token refresh grant")
    assert any(
        r["repo"] == str(b) and r["path"] == "docs/notes.md" for r in ctx["related"]
    ), "topic match in other repo must surface"
    # scoped fallback: no lexical hit in repo a for that query -> semantic top_files
    assert ctx["top_files"], "semantic fallback must answer inside the repo too"


def test_search_semantic_fallback(tmp_path):
    from cbm_memory import mcp_server

    a = make_repo(tmp_path / "a", {"svc.py": "def do_a():\n    pass\n"})
    (a / "svc.py").write_text(
        "def do_a():\n    pass\n  # rate limiter bucket refill algorithm\n"
    )
    run_git(a, "add", "-A")
    run_git(a, "commit", "-q", "-m", "feat: limiter")
    g = mcp_server.index_repositories([str(a)])
    hits = g.search_codebase_memory("*", "limiter refill")
    assert hits and any(s["name"] == "do_a" for r in hits for s in r["symbols"])


def test_tool_calls_survive_partial_args(tmp_path):
    """Any model may omit optional args - tools must default, not error."""
    import inspect

    from cbm_memory import mcp_server

    a = make_repo(tmp_path, {"x.py": "def one():\n    pass\n"})
    mcp_server.index_repositories([str(a)])
    for name in (
        "get_codebase_context",
        "search_codebase_memory",
        "explain_file",
        "team_activity",
        "find_callers",
        "change_impact",
        "reindex",
    ):
        fn = getattr(mcp_server, name)
        sig = inspect.signature(fn)
        if name != "team_activity":
            assert (
                all(
                    p.default is not inspect.Parameter.empty
                    for n, p in sig.parameters.items()
                    if n not in ("ctx",)
                )
                or n in ()
            ), name
    assert mcp_server.get_codebase_context(ctx=None, query="one")["top_files"]
    assert mcp_server.search_codebase_memory(ctx=None, query="one")
    assert mcp_server.explain_file(ctx=None, path="x.py")["symbols"]


def test_restart_reloads_graph_from_cache(tmp_path, monkeypatch):
    """Model/client switch restarts the server: reload from disk, no re-parse."""
    from cbm_memory import mcp_server

    cache = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    a = make_repo(tmp_path / "a", {"x.py": "def one():\n    pass\n"})
    b = make_repo(tmp_path / "b", {"y.py": "def two():\n    pass\n"})
    g1 = mcp_server.index_repositories([str(a), str(b)], cache_dir=str(cache))
    t1 = g1.db.execute(
        "SELECT indexed_at FROM repo_meta WHERE path=?", (str(a),)
    ).fetchone()[0]
    g2 = mcp_server.index_repositories([str(a), str(b)], cache_dir=str(cache))
    t2 = g2.db.execute(
        "SELECT indexed_at FROM repo_meta WHERE path=?", (str(a),)
    ).fetchone()[0]
    assert t1 == t2, "second startup must hit the cache, not re-index"
    assert {
        s["name"] for r in g2.search_codebase_memory("*", "one") for s in r["symbols"]
    } == {"one"}
    assert g2.explain_file("*", "x.py")["symbols"]


def test_restart_after_new_commit_applies_incremental(tmp_path, monkeypatch):
    from cbm_memory import mcp_server

    cache = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    a = make_repo(tmp_path / "a", {"x.py": "def one():\n    pass\n"})
    g1 = mcp_server.index_repositories([str(a)], cache_dir=str(cache))
    (a / "z.py").write_text("def fresh():\n    pass\n")
    run_git(a, "add", "-A")
    run_git(a, "commit", "-q", "-m", "feat: fresh")
    g2 = mcp_server.index_repositories([str(a)], cache_dir=str(cache))
    assert {
        s["name"] for r in g2.search_codebase_memory("*", "fresh") for s in r["symbols"]
    } == {"fresh"}


def test_restart_with_dirty_worktree_reindexes(tmp_path, monkeypatch):
    from cbm_memory import mcp_server

    cache = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    a = make_repo(tmp_path / "a", {"x.py": "def one():\n    pass\n"})
    mcp_server.index_repositories([str(a)], cache_dir=str(cache))
    (a / "x.py").write_text("def uncommitted():\n    pass\n")
    g2 = mcp_server.index_repositories([str(a)], cache_dir=str(cache))
    assert {
        s["name"]
        for r in g2.search_codebase_memory("*", "uncommitted")
        for s in r["symbols"]
    } == {"uncommitted"}


def test_cache_isolated_per_repo_set_and_corrupt_file_rebuilds(tmp_path, monkeypatch):
    from cbm_memory import mcp_server

    cache = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    a = make_repo(tmp_path / "a", {"x.py": "def one():\n    pass\n"})
    b = make_repo(tmp_path / "b", {"y.py": "def two():\n    pass\n"})
    mcp_server.index_repositories([str(a)], cache_dir=str(cache))
    mcp_server.index_repositories([str(b)], cache_dir=str(cache))
    files = list(cache.rglob("*.db"))
    assert len(files) == 2, "different repo sets must not share one cache"
    files[0].write_bytes(b"not a database")
    g = mcp_server.index_repositories([str(a)], cache_dir=str(cache))
    assert {
        s["name"] for r in g.search_codebase_memory("*", "one") for s in r["symbols"]
    } == {"one"}


def test_multi_repo_edge_cases(tmp_path, monkeypatch):
    """Duplicate paths, vanished repos, dead repo args, unicode names."""
    from cbm_memory import mcp_server

    cache = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    a = make_repo(tmp_path / "a", {"café/ünïcode.py": "def héllo():\n    pass\n"})
    b = make_repo(tmp_path / "b", {"deep/nest/edge.py": "def edge_sym():\n    pass\n"})
    g = mcp_server.index_repositories([str(a), str(a), str(b)], cache_dir=str(cache))
    assert {
        s["name"] for r in g.search_codebase_memory("*", "héllo") for s in r["symbols"]
    } == {"héllo"}
    assert len(g.db.execute("SELECT DISTINCT repo FROM repo_meta").fetchall()) == 2
    gone = tmp_path / "vanished"
    gone.mkdir()
    (gone / "keep.txt").write_text("x")
    g2 = mcp_server.index_repositories([str(gone), str(b)], cache_dir=str(cache))
    assert {
        s["name"]
        for r in g2.search_codebase_memory("*", "edge_sym")
        for s in r["symbols"]
    } == {"edge_sym"}
    # weird repo args never crash the tools
    for arg in ("", "*", "no/such/repo", "/definitely/not/a/repo"):
        g2.get_codebase_context(arg, "edge")
        g2.search_codebase_memory(arg, "edge")
        g2.explain_file(arg, "deep/nest/edge.py")
        g2.change_impact(arg, "deep/nest/edge.py")
        g2.reindex(arg)
