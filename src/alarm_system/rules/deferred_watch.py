from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from alarm_system.dedup import deferred_watch_key
from alarm_system.rules_dsl import AlertRuleV1


@dataclass
class DeferredWatchState:
    alert_id: str
    market_id: str
    target_liquidity_usd: float
    armed_at: datetime
    expires_at: datetime
    fired_at: datetime | None = None

    @property
    def is_fired(self) -> bool:
        return self.fired_at is not None


class InMemoryDeferredWatchStore:
    def __init__(self) -> None:
        self._states: dict[str, DeferredWatchState] = {}

    def arm(
        self,
        alert_id: str,
        market_id: str,
        rule: AlertRuleV1,
        armed_at: datetime,
    ) -> bool:
        if not rule.deferred_watch.enabled:
            return False
        target = rule.deferred_watch.target_liquidity_usd
        if target is None:
            return False
        key = deferred_watch_key(alert_id=alert_id, market_id=market_id)
        current = self._states.get(key)
        if current is not None and not current.is_fired and current.expires_at > armed_at:
            return False
        state = DeferredWatchState(
            alert_id=alert_id,
            market_id=market_id,
            target_liquidity_usd=float(target),
            armed_at=armed_at,
            expires_at=armed_at + timedelta(hours=rule.deferred_watch.ttl_hours),
        )
        self._states[key] = state
        return True

    def check_and_fire(
        self,
        alert_id: str,
        market_id: str,
        liquidity_usd: float,
        at: datetime,
    ) -> bool:
        key = deferred_watch_key(alert_id=alert_id, market_id=market_id)
        state = self._states.get(key)
        if state is None:
            return False
        if state.is_fired:
            return False
        if at >= state.expires_at:
            del self._states[key]
            return False
        if liquidity_usd >= state.target_liquidity_usd:
            state.fired_at = at
            return True
        return False

    def get(self, alert_id: str, market_id: str) -> DeferredWatchState | None:
        return self._states.get(deferred_watch_key(alert_id=alert_id, market_id=market_id))
