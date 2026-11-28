from cbm_memory.incremental import changed_files, plan_reindex


def test_changed_files():
    assert changed_files({"a.py", "b.py"}, {"a.py"}) == ["a.py", "b.py"]


def test_plan_reindex_basic():
    plan = plan_reindex({"a.py", "b.py"}, {"a.py", "c.py"})
    assert plan["to_reindex"] == ["a.py", "b.py"]
    assert plan["to_delete"] == ["c.py"]
    assert plan["unchanged"] == ["a.py"]


def test_plan_reindex_no_changes():
    plan = plan_reindex(set(), {"a.py", "b.py"})
    assert plan["to_reindex"] == []
    assert plan["to_delete"] == ["a.py", "b.py"]


def test_plan_reindex_empty():
    plan = plan_reindex(set(), set())
    assert plan["total"] == 0


def test_invalid_inputs():
    # plan_reindex should handle non-set inputs gracefully
    plan = plan_reindex(["a.py"], ["b.py"])
    assert "a.py" in plan["to_reindex"]
