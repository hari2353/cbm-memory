"""Footprint helpers for cbm-memory indexes and memory graphs.

Cross-pollinated from harvard-edge/TinyML-Footprint: report the size of the
memory an agent is carrying, the same way a model datasheet reports its
footprint. Counts are exact; byte estimates are order-of-magnitude only.
"""

from __future__ import annotations


_BYTES_PER_FILE = 200
_BYTES_PER_SYMBOL = 120
_BYTES_PER_EDGE = 40
_BYTES_PER_COMMIT = 160


def estimate_index_bytes(
    files: int = 0,
    symbols: int = 0,
    call_edges: int = 0,
    import_edges: int = 0,
    commits: int = 0,
) -> int:
    """Order-of-magnitude byte estimate for an indexed repo's graph."""
    for value, label in (
        (files, "files"), (symbols, "symbols"), (call_edges, "call_edges"),
        (import_edges, "import_edges"), (commits, "commits"),
    ):
        if value < 0:
            raise ValueError(f"{label} must be >= 0")
    return (
        files * _BYTES_PER_FILE
        + symbols * _BYTES_PER_SYMBOL
        + (call_edges + import_edges) * _BYTES_PER_EDGE
        + commits * _BYTES_PER_COMMIT
    )


def summarize_index(index: dict) -> dict:
    """Summarize a repo index (from ``index_repo``) into counts + footprint."""
    files = len(index.get("files", []))
    symbols = sum(len(f.get("symbols", [])) for f in index.get("files", []))
    call_edges = sum(len(f.get("calls", [])) for f in index.get("files", []))
    import_edges = sum(len(f.get("imports", [])) for f in index.get("files", []))
    commits = len(index.get("commits", []))
    return {
        "files": files,
        "symbols": symbols,
        "call_edges": call_edges,
        "import_edges": import_edges,
        "commits": commits,
        "estimated_bytes": estimate_index_bytes(
            files, symbols, call_edges, import_edges, commits
        ),
    }
