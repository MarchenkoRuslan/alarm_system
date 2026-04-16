# Compute And Rules Baseline (2026-04-16)

## Purpose

Capture the minimal smoke baseline for the compute/rules domain.

## Scope

- `tests/compute`
- `tests/rules`
- command: `pytest tests/compute tests/rules`

## Result snapshot

- Status: PASS
- Collected: 18
- Passed: 18
- Failed: 0
- Runtime: ~0.5s

## Gate evidence

- Recorded fixture replay parity:
  - `tests/rules/test_runtime_replay.py::test_replay_parity_is_deterministic_under_duplicate_noise`
  - fixture: `tests/rules/fixtures/replay_window.json`
  - provenance: `tests/fixtures/polymarket/price_change.json`, `tests/fixtures/polymarket/new_market.json`, `tests/fixtures/polymarket/book.json`
- One-shot delayed-liquidity crossing:
  - `tests/rules/test_runtime_replay.py::test_reference_a_b_c_rules_trigger_with_one_shot_delayed_liquidity`
- Suppression window behavior:
  - `tests/rules/test_runtime_replay.py::test_suppress_if_blocks_within_duration_then_allows_trigger`
  - `tests/rules/test_runtime_replay.py::test_suppress_if_missing_signal_does_not_block_trigger`

## Notes

- Runtime applies non-tag filters (`iran_tag_only`, `min_smart_score`, `min_account_age_days`) before predicate evaluation.
- `suppress_if` is applied in runtime; persistent backend must stay consistent with dedup/cooldown contracts.
