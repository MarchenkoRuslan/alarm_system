from __future__ import annotations

from typing import Any


def normalize_tag(value: str) -> str:
    return value.strip().lower()


def to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def extract_event_tags(payload: dict[str, Any]) -> list[str]:
    tags = payload.get("tags")
    if isinstance(tags, list):
        result: set[str] = set()
        for tag in tags:
            if isinstance(tag, str) and tag.strip():
                result.add(normalize_tag(tag))
            elif isinstance(tag, dict):
                label = tag.get("label") or tag.get("name")
                if isinstance(label, str) and label.strip():
                    result.add(normalize_tag(label))
        if result:
            return sorted(result)

    category = payload.get("category")
    if isinstance(category, str) and category.strip():
        return [normalize_tag(category)]

    category_tags = payload.get("category_tags")
    if isinstance(category_tags, list):
        result = {
            normalize_tag(tag)
            for tag in category_tags
            if isinstance(tag, str) and tag.strip()
        }
        if result:
            return sorted(result)
    return []


def extract_event_tag_ids(payload: dict[str, Any]) -> list[int]:
    tags = payload.get("tags")
    if not isinstance(tags, list):
        return []
    result: set[int] = set()
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        raw = tag.get("id") or tag.get("tag_id") or tag.get("tagId")
        if isinstance(raw, int):
            result.add(raw)
        elif isinstance(raw, str):
            stripped = raw.strip()
            if stripped.isdigit():
                result.add(int(stripped))
    return sorted(result)
