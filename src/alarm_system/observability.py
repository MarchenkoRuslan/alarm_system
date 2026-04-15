from __future__ import annotations

from dataclasses import dataclass, field
from statistics import quantiles


@dataclass
class MetricPoint:
    key: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class SLOCheckResult:
    metric: str
    p95_ms: float
    threshold_ms: float
    passed: bool


@dataclass
class RuntimeObservability:
    """
    In-memory metrics collector aligned with MVP metric catalog.
    """

    _timings: dict[str, list[float]] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def observe_timing_ms(self, metric: str, value_ms: float) -> None:
        self._timings.setdefault(metric, []).append(value_ms)

    def increment(self, metric: str, value: int = 1) -> None:
        self._counters[metric] = self._counters.get(metric, 0) + value

    def p95_ms(self, metric: str) -> float:
        values = self._timings.get(metric, [])
        if not values:
            return 0.0
        if len(values) == 1:
            return values[0]
        # Inclusive method keeps behavior deterministic for small samples.
        return quantiles(values, n=100, method="inclusive")[94]

    def check_event_to_enqueue_slo(
        self,
        threshold_ms: float = 1000.0,
    ) -> SLOCheckResult:
        p95 = self.p95_ms("event_to_enqueue_ms")
        return SLOCheckResult(
            metric="event_to_enqueue_ms",
            p95_ms=p95,
            threshold_ms=threshold_ms,
            passed=p95 <= threshold_ms,
        )

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        timings_summary = {
            metric: self.p95_ms(metric)
            for metric in self._timings
        }
        return {
            "p95_timings_ms": timings_summary,
            "counters": dict(self._counters),
        }
