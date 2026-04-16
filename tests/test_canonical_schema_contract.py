from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

from alarm_system.canonical_event import Source
from alarm_system.ingestion.polymarket.mapper import (
    MappingContext,
    map_polymarket_payload,
)


class CanonicalSchemaContractTests(unittest.TestCase):
    def test_polymarket_mapping_output_matches_json_schema(self) -> None:
        fixtures_dir = (
            Path(__file__).resolve().parent / "fixtures" / "polymarket"
        )
        payload = json.loads(
            (fixtures_dir / "book.json").read_text(encoding="utf-8")
        )
        event = map_polymarket_payload(
            payload=payload,
            received_at=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
            context=MappingContext(adapter_version="contract@v1"),
        )

        schema_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "alarm_system"
            / "schemas"
            / "canonical_event.v1.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(
            event.model_dump(mode="json", exclude_none=True)
        )
        self.assertEqual(event.source, Source.POLYMARKET)
