from __future__ import annotations

from hashlib import sha256

from alarm_system.canonical_event import EventType


def build_canonical_event_id(
    event_type: EventType,
    market_id: str,
    source_event_id: str,
    payload_hash: str,
) -> str:
    """
    Build canonical event identifier from stable tuple fields.

    Contract:
    sha256(event_type | market_id | source_event_id | payload_hash)[:32]
    """

    digest = sha256(
        f"{event_type.value}|{market_id}|{source_event_id}|{payload_hash}".encode("utf-8")
    ).hexdigest()
    return digest[:32]
