from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from alarm_system.canonical_event import CanonicalEvent


@lru_cache(maxsize=1)
def _load_schema() -> dict[str, Any]:
    ref = files("alarm_system.schemas") / "canonical_event.v1.schema.json"
    return json.loads(ref.read_text(encoding="utf-8"))


def validate_canonical_event(event: CanonicalEvent) -> None:
    """
    Validate canonical event against JSON Schema when possible.

    Falls back to pydantic model validation in environments where `jsonschema`
    is unavailable. This keeps checks operational in minimal local setups.
    """

    payload = event.model_dump(mode="json", exclude_none=True)
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        CanonicalEvent.model_validate(payload)
        return

    validator = Draft202012Validator(_load_schema())
    validator.validate(payload)
