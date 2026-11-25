from cbm_memory.sharding import merge_shards, shard_index


def test_shard_index_partitions_files():
    index = {
        "files": [{"path": f"f{i}.py"} for i in range(10)],
        "commits": [{"sha": "1"}],
    }
    shards = shard_index(index, 3)
    assert len(shards) == 3
    total = sum(len(s["files"]) for s in shards)
    assert total == 10


def test_shard_index_deterministic():
    index = {"files": [{"path": f"f{i}.py"} for i in range(5)]}
    a = shard_index(index, 2)
    b = shard_index(index, 2)
    assert [len(s["files"]) for s in a] == [len(s["files"]) for s in b]


def test_merge_shards_roundtrips():
    index = {
        "files": [{"path": f"f{i}.py"} for i in range(7)],
        "commits": [{"sha": "1"}],
    }
    shards = shard_index(index, 3)
    merged = merge_shards(shards)
    assert len(merged["files"]) == 7
    assert len(merged["commits"]) == 1


def test_invalid_inputs():
    try:
        shard_index({"files": []}, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero shards accepted")
    try:
        shard_index("nope", 2)
    except TypeError:
        pass
    else:
        raise AssertionError("non-dict accepted")
    try:
        merge_shards("nope")
    except TypeError:
        pass
    else:
        raise AssertionError("non-list accepted")
