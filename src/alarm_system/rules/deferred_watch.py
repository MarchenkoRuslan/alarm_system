from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from alarm_system.alert_filters import deferred_target_liquidity_usd, deferred_ttl_hours
from alarm_system.dedup import deferred_watch_key
from alarm_system.rules_dsl import AlertRuleV1
from alarm_system.state import RedisDeferredWatchStore


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
        filters_json: dict[str, str | int | float | bool | list[str]] | None = None,
    ) -> bool:
        if not rule.deferred_watch.enabled:
            return False
        fj = dict(filters_json or {})
        target = deferred_target_liquidity_usd(rule, fj)
        if target is None:
            return False
        ttl_hours = deferred_ttl_hours(rule, fj)
        key = deferred_watch_key(alert_id=alert_id, market_id=market_id)
        current = self._states.get(key)
        if current is not None and not current.is_fired and current.expires_at > armed_at:
            return False
        state = DeferredWatchState(
            alert_id=alert_id,
            market_id=market_id,
            target_liquidity_usd=float(target),
            armed_at=armed_at,
            expires_at=armed_at + timedelta(hours=ttl_hours),
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
        if not self.is_crossed(
            alert_id=alert_id,
            market_id=market_id,
            liquidity_usd=liquidity_usd,
            at=at,
        ):
            return False
        return self.mark_fired(
            alert_id=alert_id,
            market_id=market_id,
            fired_at=at,
        )

    def is_crossed(
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
        return liquidity_usd >= state.target_liquidity_usd

    def mark_fired(
        self,
        alert_id: str,
        market_id: str,
        fired_at: datetime,
    ) -> bool:
        key = deferred_watch_key(alert_id=alert_id, market_id=market_id)
        state = self._states.get(key)
        if state is None or state.is_fired:
            return False
        if fired_at >= state.expires_at:
            del self._states[key]
            return False
        state.fired_at = fired_at
        return True

    def get(self, alert_id: str, market_id: str) -> DeferredWatchState | None:
        key = deferred_watch_key(alert_id=alert_id, market_id=market_id)
        return self._states.get(key)


class RedisBackedDeferredWatchStore:
    def __init__(
        self,
        state: RedisDeferredWatchStore,
    ) -> None:
        self._state = state

    def arm(
        self,
        alert_id: str,
        market_id: str,
        rule: AlertRuleV1,
        armed_at: datetime,
        filters_json: dict[str, str | int | float | bool | list[str]] | None = None,
    ) -> bool:
        if not rule.deferred_watch.enabled:
            return False
        fj = dict(filters_json or {})
        target = deferred_target_liquidity_usd(rule, fj)
        if target is None:
            return False
        ttl_hours = deferred_ttl_hours(rule, fj)
        current = self._state.load(alert_id=alert_id, market_id=market_id)
        expires_at = armed_at + timedelta(hours=ttl_hours)
        if current is not None:
            current_fired_at = current.get("fired_at")
            current_expires = current.get("expires_at")
            if (
                current_fired_at is None
                and isinstance(current_expires, str)
            ):
                existing_expires = datetime.fromisoformat(current_expires)
                if existing_expires > armed_at:
                    return False
        self._state.save(
            alert_id=alert_id,
            market_id=market_id,
            expires_at=expires_at,
            payload={
                "target_liquidity_usd": float(target),
                "armed_at": armed_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "fired_at": None,
            },
        )
        return True

    def check_and_fire(
        self,
        alert_id: str,
        market_id: str,
        liquidity_usd: float,
        at: datetime,
    ) -> bool:
        if not self.is_crossed(
            alert_id=alert_id,
            market_id=market_id,
            liquidity_usd=liquidity_usd,
            at=at,
        ):
            return False
        return self.mark_fired(
            alert_id=alert_id,
            market_id=market_id,
            fired_at=at,
        )

    def is_crossed(
        self,
        alert_id: str,
        market_id: str,
        liquidity_usd: float,
        at: datetime,
    ) -> bool:
        current = self._state.load(alert_id=alert_id, market_id=market_id)
        if current is None:
            return False
        if current.get("fired_at") is not None:
            return False
        expires_at_raw = current.get("expires_at")
        if not isinstance(expires_at_raw, str):
            return False
        expires_at = datetime.fromisoformat(expires_at_raw)
        if at >= expires_at:
            return False
        target = float(current.get("target_liquidity_usd") or 0.0)
        return liquidity_usd >= target

    def mark_fired(
        self,
        alert_id: str,
        market_id: str,
        fired_at: datetime,
    ) -> bool:
        current = self._state.load(alert_id=alert_id, market_id=market_id)
        if current is None:
            return False
        if current.get("fired_at") is not None:
            return False
        expires_at_raw = current.get("expires_at")
        if not isinstance(expires_at_raw, str):
            return False
        expires_at = datetime.fromisoformat(expires_at_raw)
        if fired_at >= expires_at:
            return False
        current["fired_at"] = fired_at.isoformat()
        self._state.save(
            alert_id=alert_id,
            market_id=market_id,
            expires_at=expires_at,
            payload=current,
        )
        return True
