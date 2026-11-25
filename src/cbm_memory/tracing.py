"""Lightweight tracing (cross-pollinated from Arize-ai/phoenix).

Records spans with parent/child relationships, attributes and durations.
Pure Python, bounded export, no OpenTelemetry dependency.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_id: str | None = None
    start: float = field(default_factory=time.perf_counter)
    end: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def finish(self, **attrs: Any) -> None:
        self.end = time.perf_counter()
        self.attributes.update(attrs)

    @property
    def duration_s(self) -> float | None:
        if self.end is None:
            return None
        return round(self.end - self.start, 6)


class Tracer:
    def __init__(self, max_spans: int = 1000) -> None:
        if max_spans < 1:
            raise ValueError("max_spans must be >= 1")
        self.max_spans = max_spans
        self._spans: list[Span] = []

    def start(self, name: str, *, parent: Span | None = None, **attrs: Any) -> Span:
        if not name.strip():
            raise ValueError("span name must be non-empty")
        span = Span(
            name=name,
            trace_id=parent.trace_id if parent else uuid.uuid4().hex[:16],
            parent_id=parent.span_id if parent else None,
            attributes=dict(attrs),
        )
        self._spans.append(span)
        if len(self._spans) > self.max_spans:
            self._spans = self._spans[-self.max_spans :]
        return span

    def spans(self) -> list[Span]:
        return list(self._spans)

    def export(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "trace_id": s.trace_id,
                "span_id": s.span_id,
                "parent_id": s.parent_id,
                "duration_s": s.duration_s,
                "attributes": dict(s.attributes),
            }
            for s in self._spans
        ]

    def clear(self) -> None:
        self._spans.clear()
