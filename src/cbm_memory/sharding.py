"""Index sharding (cross-pollinated from Notion/Pinterest).

Partitions a repo index into N shards by file-path hash, with a merge helper
to reassemble. Deterministic and bounded. Pure Python.
"""

from __future__ import annotations

import hashlib


def shard_index(index: dict, num_shards: int) -> list[dict]:
    if num_shards < 1:
        raise ValueError("num_shards must be >= 1")
    if not isinstance(index, dict):
        raise TypeError("index must be a dict")
    files = list(index.get("files", []))
    shards: list[dict] = [{"shard": i, "files": []} for i in range(num_shards)]
    for f in files:
        path = str(f.get("path", ""))
        digest = hashlib.md5(path.encode("utf-8")).hexdigest()
        sid = int(digest, 16) % num_shards
        shards[sid]["files"].append(f)
    # propagate top-level metadata
    for shard in shards:
        shard["commits"] = list(index.get("commits", []))
    return shards


def merge_shards(shards: list[dict]) -> dict:
    if not isinstance(shards, list):
        raise TypeError("shards must be a list")
    files: list = []
    commits: list = []
    seen_commits: set = set()
    for shard in shards:
        files.extend(shard.get("files", []))
        for c in shard.get("commits", []):
            key = c.get("sha") if isinstance(c, dict) else c
            if key not in seen_commits:
                seen_commits.add(key)
                commits.append(c)
    return {"files": files, "commits": commits}
