from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean


@dataclass
class MetricSnapshot:
    counters: dict[str, int]
    gauges: dict[str, float]
    timings_ms: dict[str, float]


@dataclass
class InMemoryMetrics:
    """
    Lightweight in-memory metrics storage for ingestion runtime.

    This intentionally avoids external dependencies while still giving
    deterministic counters and coarse latency observability for MVP smoke runs.
    """

    _counters: dict[str, int] = field(default_factory=dict)
    _gauges: dict[str, float] = field(default_factory=dict)
    _timings: dict[str, list[float]] = field(default_factory=dict)

    def increment(self, key: str, value: int = 1) -> None:
        self._counters[key] = self._counters.get(key, 0) + value

    def set_gauge(self, key: str, value: float) -> None:
        self._gauges[key] = value

    def observe_timing_ms(self, key: str, value_ms: float) -> None:
        self._timings.setdefault(key, []).append(value_ms)

    def snapshot(self) -> MetricSnapshot:
        timing_means = {
            key: fmean(values) if values else 0.0
            for key, values in self._timings.items()
        }
        return MetricSnapshot(
            counters=dict(self._counters),
            gauges=dict(self._gauges),
            timings_ms=timing_means,
        )
