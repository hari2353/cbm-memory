from cbm_memory.footprint import estimate_index_bytes, summarize_index
from cbm_memory.memorygraph import MemoryGraph


def test_memory_graph_footprint_empty():
    graph = MemoryGraph(":memory:")
    assert graph.footprint() == {
        "entities": 0,
        "episodes": 0,
        "working_items": 0,
        "estimated_bytes": 0,
    }
    graph.close()


def test_memory_graph_footprint_counts():
    graph = MemoryGraph(":memory:")
    graph.add_entity("svc", "Service", "project")
    graph.retain("svc", "started", "2026-01-01T00:00:00Z")
    graph.remember_working("task", "fix", ttl_seconds=60)
    out = graph.footprint()
    assert out["entities"] == 1
    assert out["episodes"] == 1
    assert out["working_items"] == 1
    assert out["estimated_bytes"] > 0
    graph.close()


def test_estimate_index_bytes_zero():
    assert estimate_index_bytes() == 0


def test_estimate_index_bytes_scales():
    one = estimate_index_bytes(files=1)
    more = estimate_index_bytes(files=2, symbols=10, call_edges=3)
    assert more > one


def test_estimate_index_bytes_rejects_negative():
    for kwargs in (
        {"files": -1},
        {"symbols": -1},
        {"call_edges": -1},
        {"import_edges": -1},
        {"commits": -1},
    ):
        try:
            estimate_index_bytes(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid input: {kwargs}")


def test_summarize_index():
    index = {
        "files": [
            {
                "symbols": [{"name": "a"}, {"name": "b"}],
                "calls": [{"x": 1}],
                "imports": [],
            },
            {"symbols": [], "calls": [], "imports": [{"m": 1}, {"m": 2}]},
        ],
        "commits": [{"sha": "1"}, {"sha": "2"}],
    }
    out = summarize_index(index)
    assert out["files"] == 2
    assert out["symbols"] == 2
    assert out["call_edges"] == 1
    assert out["import_edges"] == 2
    assert out["commits"] == 2
    assert out["estimated_bytes"] == estimate_index_bytes(2, 2, 1, 2, 2)
