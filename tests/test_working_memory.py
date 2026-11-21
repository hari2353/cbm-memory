import pytest

from cbm_memory.memorygraph import MemoryGraph

NOW = "2026-11-21T12:00:00Z"


def test_working_set_get_roundtrip():
    g = MemoryGraph()
    g.remember_working("task", "indexing repo", ttl_seconds=600, importance=0.9, now=NOW)
    row = g.recall_working("task", now=NOW)
    assert row["key"] == "task"
    assert row["value"] == "indexing repo"
    assert row["importance"] == 0.9
    assert row["updated_at"].startswith("2026-11-21T12:00:00")
    assert row["expires_at"].startswith("2026-11-21T12:10:00")
    g.close()


def test_working_upsert_replaces_value_and_ttl():
    g = MemoryGraph()
    g.remember_working("k", "old", ttl_seconds=60, now=NOW)
    g.remember_working("k", "new", ttl_seconds=600, now=NOW)
    row = g.recall_working("k", now=NOW)
    assert row["value"] == "new"
    assert row["expires_at"].startswith("2026-11-21T12:10:00")
    assert len(g.working_items(now=NOW)) == 1
    g.close()


def test_working_ttl_expiry_with_injected_now():
    g = MemoryGraph()
    g.remember_working("k", "v", ttl_seconds=60, now=NOW)
    assert g.recall_working("k", now="2026-11-21T12:00:59Z") is not None
    assert g.recall_working("k", now="2026-11-21T12:01:00Z") is None
    g.close()


def test_evict_expired_returns_count():
    g = MemoryGraph()
    g.remember_working("a", "v", ttl_seconds=60, now=NOW)
    g.remember_working("b", "v", ttl_seconds=120, now=NOW)
    g.remember_working("c", "v", ttl_seconds=600, now=NOW)
    assert g.evict_expired(now="2026-11-21T12:01:00Z") == 1
    assert g.evict_expired(now="2026-11-21T12:02:00Z") == 1
    assert g.evict_expired(now="2026-11-21T12:02:00Z") == 0
    assert [r["key"] for r in g.working_items(now="2026-11-21T12:02:00Z")] == ["c"]
    g.close()


def test_working_items_order_by_importance_then_updated_at():
    g = MemoryGraph()
    g.remember_working("low", "v", importance=0.2, now=NOW)
    g.remember_working("high-old", "v", ttl_seconds=7200, importance=0.9,
                       now="2026-11-21T11:00:00Z")
    g.remember_working("high-new", "v", importance=0.9, now=NOW)
    g.remember_working("mid", "v", importance=0.5, now=NOW)
    assert [r["key"] for r in g.working_items(now=NOW)] == [
        "high-new", "high-old", "mid", "low"]
    assert [r["key"] for r in g.working_items(now=NOW, limit=2)] == [
        "high-new", "high-old"]
    g.close()


def test_working_items_hides_expired():
    g = MemoryGraph()
    g.remember_working("gone", "v", ttl_seconds=60, now=NOW)
    g.remember_working("fresh", "v", ttl_seconds=600, now=NOW)
    assert [r["key"] for r in g.working_items(now="2026-11-21T12:05:00Z")] == [
        "fresh"]
    g.close()


def test_working_persists_across_reopen(tmp_path):
    path = str(tmp_path / "memory.db")
    g = MemoryGraph(path)
    g.remember_working("k", "durable", ttl_seconds=3600, importance=0.7, now=NOW)
    g.close()
    g = MemoryGraph(path)
    row = g.recall_working("k", now=NOW)
    assert row["value"] == "durable"
    assert row["importance"] == 0.7
    g.close()


def test_promote_working_creates_episode_and_removes_row():
    g = MemoryGraph()
    g.add_entity("proj", "project", "project")
    g.remember_working("k", "deployed v2", ttl_seconds=600, now=NOW)
    episode_id = g.promote_working("k", "proj", now=NOW)
    assert isinstance(episode_id, int)
    assert g.recall_working("k", now=NOW) is None
    ctx = g.context_for("proj")
    assert [e["text"] for e in ctx["episodes"]] == ["deployed v2"]
    assert ctx["episodes"][0]["source"] == "working"
    g.close()


def test_promote_missing_or_expired_returns_none():
    g = MemoryGraph()
    assert g.promote_working("missing", "proj", now=NOW) is None
    g.remember_working("k", "v", ttl_seconds=60, now=NOW)
    assert g.promote_working("k", "proj", now="2026-11-21T12:01:00Z") is None
    g.close()


def test_working_validation_errors():
    g = MemoryGraph()
    with pytest.raises(ValueError):
        g.remember_working("k", "v", ttl_seconds=0)
    with pytest.raises(ValueError):
        g.remember_working("k", "v", ttl_seconds=-1)
    with pytest.raises(ValueError):
        g.remember_working("k", "v", importance=-0.1)
    with pytest.raises(ValueError):
        g.remember_working("k", "v", importance=1.1)
    g.close()
