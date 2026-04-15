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
    _timings_by_series: dict[str, list[float]] = field(default_factory=dict)
    _counters_by_series: dict[str, int] = field(default_factory=dict)

    def observe_timing_ms(
        self,
        metric: str,
        value_ms: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        self._timings.setdefault(metric, []).append(value_ms)
        self._timings_by_series.setdefault(
            _series_key(metric, labels), []
        ).append(value_ms)

    def increment(
        self,
        metric: str,
        value: int = 1,
        labels: dict[str, str] | None = None,
    ) -> None:
        self._counters[metric] = self._counters.get(metric, 0) + value
        series_key = _series_key(metric, labels)
        self._counters_by_series[series_key] = (
            self._counters_by_series.get(series_key, 0) + value
        )

    def count(self, metric: str) -> int:
        return self._counters.get(metric, 0)

    def p95_ms(self, metric: str) -> float:
        values = self._timings.get(metric, [])
        return _p95(values)

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
            "series": {
                "p95_timings_ms": {
                    metric: _p95(values)
                    for metric, values in self._timings_by_series.items()
                },
                "counters": dict(self._counters_by_series),
            },
        }


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return quantiles(values, n=100, method="inclusive")[94]


def _series_key(metric: str, labels: dict[str, str] | None) -> str:
    if not labels:
        return metric
    suffix = ",".join(f"{k}={labels[k]}" for k in sorted(labels))
    return f"{metric}|{suffix}"
