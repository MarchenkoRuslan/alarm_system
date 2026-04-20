from __future__ import annotations

import unittest
from datetime import datetime, timezone

from alarm_system.dedup import DedupInput, cooldown_key, dedup_key
from alarm_system.rules_dsl import build_trigger_key


class DedupHelpersTests(unittest.TestCase):
    def test_dedup_key_matches_trigger_key_contract(self) -> None:
        event_ts = datetime(2026, 4, 16, 12, 0, 30, tzinfo=timezone.utc)
        data = DedupInput(
            tenant_id="tenant-a",
            rule_id="rule-1",
            rule_version=2,
            scope_id="market-7",
            bucket_seconds=60,
            event_time=event_ts,
        )

        self.assertEqual(
            dedup_key(data),
            build_trigger_key(
                tenant_id="tenant-a",
                rule_id="rule-1",
                rule_version=2,
                scope_id="market-7",
                bucket_seconds=60,
                at=event_ts,
            ),
        )

    def test_dedup_key_treats_naive_datetime_as_utc(self) -> None:
        naive = datetime(2026, 4, 16, 12, 0, 30)
        aware = datetime(2026, 4, 16, 12, 0, 30, tzinfo=timezone.utc)
        base = {
            "tenant_id": "tenant-a",
            "rule_id": "rule-1",
            "rule_version": 1,
            "scope_id": "market-1",
            "bucket_seconds": 60,
        }

        self.assertEqual(
            dedup_key(DedupInput(event_time=naive, **base)),
            dedup_key(DedupInput(event_time=aware, **base)),
        )

    def test_dedup_key_changes_across_time_buckets(self) -> None:
        base = {
            "tenant_id": "tenant-a",
            "rule_id": "rule-1",
            "rule_version": 1,
            "scope_id": "market-1",
            "bucket_seconds": 60,
        }
        first = dedup_key(
            DedupInput(
                event_time=datetime(
                    2026, 4, 16, 12, 0, 59, tzinfo=timezone.utc
                ),
                **base,
            )
        )
        second = dedup_key(
            DedupInput(
                event_time=datetime(
                    2026, 4, 16, 12, 1, 0, tzinfo=timezone.utc
                ),
                **base,
            )
        )
        self.assertNotEqual(first, second)

    def test_cooldown_key_is_channel_aware(self) -> None:
        telegram = cooldown_key(
            tenant_id="tenant-a",
            rule_id="rule-1",
            rule_version=1,
            scope_id="market-1",
            channel="telegram",
        )
        email = cooldown_key(
            tenant_id="tenant-a",
            rule_id="rule-1",
            rule_version=1,
            scope_id="market-1",
            channel="email",
        )
        self.assertNotEqual(telegram, email)

    def test_build_trigger_key_rejects_non_positive_bucket(self) -> None:
        with self.assertRaises(ValueError):
            build_trigger_key(
                tenant_id="tenant-a",
                rule_id="rule-1",
                rule_version=1,
                scope_id="market-1",
                bucket_seconds=0,
                at=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
            )
