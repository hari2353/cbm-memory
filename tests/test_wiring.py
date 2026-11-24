from pathlib import Path
from cbm_memory.repoindex import index_repo
from cbm_memory.graph import GitNexusGraph
from cbm_memory.wiring import inject_context


def test_inject_context_returns_files_and_commits(sample_repo):
    g = GitNexusGraph(":memory:")
    g.load(index_repo(sample_repo))
    out = inject_context(g, "*", "greeting")
    assert "Relevant files:" in out
    assert "app.py" in out or "util.ts" in out
    assert "Recent commits:" in out
