from __future__ import annotations

from dataclasses import dataclass


WARNING_UTILIZATION = 0.70
CRITICAL_UTILIZATION = 0.90


@dataclass(frozen=True)
class BackpressureSnapshot:
    queued: int
    capacity: int
    utilization: float
    state: str
    degrade_non_critical: bool


class BackpressureController:
    """
    Minimal bounded-queue controller with saturation states:
    normal -> warning -> critical -> normal (after stabilization window).
    """

    def __init__(
        self,
        *,
        capacity: int,
        warning_utilization: float = WARNING_UTILIZATION,
        critical_utilization: float = CRITICAL_UTILIZATION,
        recovery_window_samples: int = 3,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if not (0 < warning_utilization < critical_utilization <= 1):
            raise ValueError("invalid utilization thresholds")
        self._capacity = capacity
        self._warning = warning_utilization
        self._critical = critical_utilization
        self._recovery_window = max(1, recovery_window_samples)
        self._queued = 0
        self._state = "normal"
        self._stable_below_warning_samples = 0

    def reserve_slot(self) -> bool:
        if self._queued >= self._capacity:
            self._apply_state_transition()
            return False
        self._queued += 1
        self._apply_state_transition()
        return True

    def release_slot(self) -> None:
        if self._queued > 0:
            self._queued -= 1
        self._apply_state_transition()

    def snapshot(self) -> BackpressureSnapshot:
        utilization = self.utilization
        return BackpressureSnapshot(
            queued=self._queued,
            capacity=self._capacity,
            utilization=utilization,
            state=self._state,
            degrade_non_critical=self._state == "critical",
        )

    @property
    def utilization(self) -> float:
        return self._queued / self._capacity

    @property
    def state(self) -> str:
        return self._state

    def _apply_state_transition(self) -> None:
        utilization = self.utilization
        if utilization >= self._critical:
            self._state = "critical"
            self._stable_below_warning_samples = 0
            return
        if utilization >= self._warning:
            self._state = "warning"
            self._stable_below_warning_samples = 0
            return
        self._stable_below_warning_samples += 1
        if self._stable_below_warning_samples >= self._recovery_window:
            self._state = "normal"
