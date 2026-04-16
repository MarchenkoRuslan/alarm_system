from __future__ import annotations

from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class EnumRegistry(Generic[K, V]):
    """Small reusable registry for enum-like keys."""

    def __init__(self) -> None:
        self._items: dict[K, V] = {}

    def register(self, key: K, value: V) -> None:
        self._items[key] = value

    def get(self, key: K) -> V:
        item = self._items.get(key)
        if item is None:
            raise KeyError(f"No item registered for key '{key}'.")
        return item

    def keys(self) -> list[K]:
        return list(self._items)
