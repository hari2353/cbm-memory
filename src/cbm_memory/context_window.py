"""Bounded context windows using StreamingLLM's attention-sink policy.

Cross-pollinated from mit-han-lab/streaming-llm: keep the first few items
(the attention sinks) plus the most recent window, and drop the middle.
Applied to agent memory, this gives a deterministic context budget without
losing the anchors that keep a long-running session stable.
"""

from __future__ import annotations


def _validate_window(sinks: int, recent: int) -> None:
    if sinks < 0:
        raise ValueError("sinks must be >= 0")
    if recent < 1:
        raise ValueError("recent must be >= 1")


def select_streaming_window(items: list, *, sinks: int = 2, recent: int = 6) -> dict:
    """Keep ``sinks`` head items plus ``recent`` tail items, in order.

    ``items`` is ordered oldest to newest. The returned dict carries the
    kept items, how many were dropped, and how many were kept.
    """
    _validate_window(sinks, recent)
    total = len(items)
    if total == 0:
        return {"items": [], "omitted": 0, "kept": 0}
    sink_end = min(sinks, total)
    recent_start = max(sink_end, total - recent)
    kept = list(items[:sink_end]) + list(items[recent_start:])
    return {"items": kept, "omitted": total - len(kept), "kept": len(kept)}
