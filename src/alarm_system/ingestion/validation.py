from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator

from alarm_system.canonical_event import CanonicalEvent


@lru_cache(maxsize=1)
def _load_schema() -> dict[str, Any]:
    ref = files("alarm_system.schemas") / "canonical_event.v1.schema.json"
    return json.loads(ref.read_text(encoding="utf-8"))


def validate_canonical_event(event: CanonicalEvent) -> None:
    """
    Validate canonical event against canonical JSON Schema.
    """

    payload = event.model_dump(mode="json", exclude_none=True)
    validator = Draft202012Validator(_load_schema())
    validator.validate(payload)
