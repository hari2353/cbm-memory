from cbm_memory.context_window import select_streaming_window
from cbm_memory.memorygraph import MemoryGraph


def test_keeps_sinks_and_recent_and_drops_middle():
    items = [{"text": f"e{i}"} for i in range(10)]
    out = select_streaming_window(items, sinks=2, recent=3)
    assert [item["text"] for item in out["items"]] == ["e0", "e1", "e7", "e8", "e9"]
    assert out["omitted"] == 5
    assert out["kept"] == 5


def test_short_list_is_kept_whole():
    items = [{"text": f"e{i}"} for i in range(4)]
    out = select_streaming_window(items, sinks=2, recent=6)
    assert out["items"] == items
    assert out["omitted"] == 0


def test_overlapping_sink_and_recent_do_not_duplicate():
    items = [{"text": f"e{i}"} for i in range(5)]
    out = select_streaming_window(items, sinks=3, recent=3)
    assert [item["text"] for item in out["items"]] == ["e0", "e1", "e2", "e3", "e4"]
    assert out["omitted"] == 0


def test_zero_sinks_keeps_only_recent():
    items = [{"text": f"e{i}"} for i in range(4)]
    out = select_streaming_window(items, sinks=0, recent=2)
    assert [item["text"] for item in out["items"]] == ["e2", "e3"]
    assert out["omitted"] == 2


def test_empty_input():
    out = select_streaming_window([], sinks=2, recent=3)
    assert out == {"items": [], "omitted": 0, "kept": 0}


def test_invalid_window_rejected():
    for kwargs in ({"sinks": -1, "recent": 3}, {"sinks": 0, "recent": 0}):
        try:
            select_streaming_window([], **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid window: {kwargs}")


def test_graph_context_window_is_bounded_and_chronological():
    graph = MemoryGraph(":memory:")
    graph.add_entity("svc", "Service", "project")
    for i in range(10):
        graph.add_episode("svc", f"e{i}", f"2026-01-01T00:00:{i:02d}Z")
    out = graph.context_window("svc", sinks=2, recent=3)
    assert [episode["text"] for episode in out["episodes"]] == [
        "e0", "e1", "e7", "e8", "e9",
    ]
    assert out["omitted"] == 5
    graph.close()


def test_graph_context_window_missing_entity():
    graph = MemoryGraph(":memory:")
    assert graph.context_window("nope") is None
    graph.close()
