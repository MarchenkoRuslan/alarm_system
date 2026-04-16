from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping

from alarm_system.rules.comparison import compare_values
from alarm_system.rules_dsl import AlertRuleV1
from alarm_system.state import RedisSuppressionWindowStateStore


class InMemorySuppressionStore:
    """
    In-memory suppression state.

    Key contract:
    - deterministic key: `alert_id + scope_id + suppress_if index`;
    - value: active suppression `until` timestamp.
    """

    def __init__(self) -> None:
        self._active_until: dict[str, datetime] = {}

    def should_suppress(
        self,
        alert_id: str,
        scope_id: str,
        rule: AlertRuleV1,
        signal_values: Mapping[str, float],
        at: datetime,
    ) -> bool:
        if not rule.suppress_if:
            return False

        for idx, suppress_rule in enumerate(rule.suppress_if):
            key = self._key(
                alert_id=alert_id,
                scope_id=scope_id,
                suppress_idx=idx,
            )
            active_until = self._active_until.get(key)
            if active_until is not None:
                if at < active_until:
                    return True
                del self._active_until[key]

            observed = signal_values.get(suppress_rule.signal)
            if observed is None:
                continue
            if compare_values(
                suppress_rule.op,
                float(observed),
                suppress_rule.threshold,
            ):
                self._active_until[key] = at + timedelta(
                    seconds=suppress_rule.duration_seconds
                )
                return True
        return False

    @staticmethod
    def _key(alert_id: str, scope_id: str, suppress_idx: int) -> str:
        return f"{alert_id}:{scope_id}:suppress:{suppress_idx}"


class RedisSuppressionStore:
    def __init__(self, state: RedisSuppressionWindowStateStore) -> None:
        self._state = state

    def should_suppress(
        self,
        alert_id: str,
        scope_id: str,
        rule: AlertRuleV1,
        signal_values: Mapping[str, float],
        at: datetime,
    ) -> bool:
        if not rule.suppress_if:
            return False

        for idx, suppress_rule in enumerate(rule.suppress_if):
            active_until = self._state.get_active_until(
                alert_id=alert_id,
                scope_id=scope_id,
                suppress_idx=idx,
            )
            if active_until is not None:
                if at < active_until:
                    return True
                self._state.clear(
                    alert_id=alert_id,
                    scope_id=scope_id,
                    suppress_idx=idx,
                )

            observed = signal_values.get(suppress_rule.signal)
            if observed is None:
                continue
            if compare_values(
                suppress_rule.op,
                float(observed),
                suppress_rule.threshold,
            ):
                self._state.set_active_until(
                    alert_id=alert_id,
                    scope_id=scope_id,
                    suppress_idx=idx,
                    active_until=at
                    + timedelta(seconds=suppress_rule.duration_seconds),
                )
                return True
        return False
