from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean


@dataclass
class MetricSnapshot:
    counters: dict[str, int]
    gauges: dict[str, float]
    timings_ms: dict[str, float]
    series: dict[str, dict[str, float | int]] = field(default_factory=dict)


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
    _counters_by_series: dict[str, int] = field(default_factory=dict)
    _gauges_by_series: dict[str, float] = field(default_factory=dict)
    _timings_by_series: dict[str, list[float]] = field(default_factory=dict)

    def increment(
        self,
        key: str,
        value: int = 1,
        labels: dict[str, str] | None = None,
    ) -> None:
        self._counters[key] = self._counters.get(key, 0) + value
        series_key = _series_key(key, labels)
        self._counters_by_series[series_key] = (
            self._counters_by_series.get(series_key, 0) + value
        )

    def set_gauge(
        self,
        key: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        self._gauges[key] = value
        self._gauges_by_series[_series_key(key, labels)] = value

    def observe_timing_ms(
        self,
        key: str,
        value_ms: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        self._timings.setdefault(key, []).append(value_ms)
        self._timings_by_series.setdefault(
            _series_key(key, labels), []
        ).append(value_ms)

    def snapshot(self) -> MetricSnapshot:
        timing_means = {
            key: fmean(values) if values else 0.0
            for key, values in self._timings.items()
        }
        series_timing_means = {
            key: fmean(values) if values else 0.0
            for key, values in self._timings_by_series.items()
        }
        return MetricSnapshot(
            counters=dict(self._counters),
            gauges=dict(self._gauges),
            timings_ms=timing_means,
            series={
                "counters": dict(self._counters_by_series),
                "gauges": dict(self._gauges_by_series),
                "timings_ms": series_timing_means,
            },
        )


def _series_key(key: str, labels: dict[str, str] | None) -> str:
    if not labels:
        return key
    suffix = ",".join(f"{k}={labels[k]}" for k in sorted(labels))
    return f"{key}|{suffix}"
