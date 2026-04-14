from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from alarm_system.entities import DeliveryChannel, DeliveryStatus


@dataclass(frozen=True)
class DeliveryPayload:
    """
    Channel-agnostic notification payload built from a trigger.
    Provider implementations format this into channel-specific messages.
    """

    trigger_id: str
    alert_id: str
    user_id: str
    channel: DeliveryChannel
    destination: str
    subject: str
    body: str
    reason_summary: str
    metadata: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class DeliveryResult:
    """Outcome of a single delivery attempt."""

    status: DeliveryStatus
    provider_message_id: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    retryable: bool = False


class DeliveryProvider(ABC):
    """
    Abstract base for all notification channel providers.

    To add a new channel:
    1. Subclass DeliveryProvider.
    2. Set `channel` to the matching DeliveryChannel value.
    3. Implement `send()`.
    4. Register in ProviderRegistry.
    """

    @property
    @abstractmethod
    def channel(self) -> DeliveryChannel:
        ...

    @abstractmethod
    async def send(self, payload: DeliveryPayload) -> DeliveryResult:
        """
        Send notification. Must be idempotent when called with the same
        trigger_id to protect against retry duplicates.
        """
        ...


class ProviderRegistry:
    """
    Registry mapping DeliveryChannel -> DeliveryProvider instance.
    One provider per channel at runtime; replace via register() to swap
    implementations (e.g. real vs. stub in tests).
    """

    def __init__(self) -> None:
        self._providers: dict[DeliveryChannel, DeliveryProvider] = {}

    def register(self, provider: DeliveryProvider) -> None:
        self._providers[provider.channel] = provider

    def get(self, channel: DeliveryChannel) -> DeliveryProvider:
        provider = self._providers.get(channel)
        if provider is None:
            raise KeyError(
                f"No provider registered for channel '{channel}'. "
                f"Available: {list(self._providers)}"
            )
        return provider

    def registered_channels(self) -> list[DeliveryChannel]:
        return list(self._providers)
