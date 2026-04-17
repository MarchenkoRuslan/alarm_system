"""Minimal argument parser for Telegram slash commands.

The parser supports three shapes in a single utterance:

- Positional args separated by whitespace: ``/enable alert-1``
- ``key=value`` pairs without whitespace around ``=``: ``cooldown=120``
- Simple flags: ``--all``

Quoted values are not supported; commands that need structured payloads
(JSON for example) should consume the raw remaining text via
``CommandArgs.raw_tail``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CommandArgs:
    command: str
    positional: list[str] = field(default_factory=list)
    options: dict[str, str] = field(default_factory=dict)
    flags: frozenset[str] = frozenset()
    raw_tail: str = ""

    def first_positional(self) -> str | None:
        return self.positional[0] if self.positional else None

    def has_flag(self, name: str) -> bool:
        return name in self.flags

    def option(self, name: str, default: str | None = None) -> str | None:
        return self.options.get(name, default)


def split_command(text: str) -> CommandArgs:
    """Split a Telegram message into command + args.

    ``@BotName`` suffix on the command is stripped so that
    ``/alerts@MyBot`` and ``/alerts`` route to the same handler.
    """

    stripped = text.strip()
    if not stripped.startswith("/"):
        return CommandArgs(command="")

    head, sep, tail_raw = stripped.partition(" ")
    raw_tail = tail_raw.strip()
    command = head.split("@", 1)[0].lower()

    positional: list[str] = []
    options: dict[str, str] = {}
    flags: set[str] = set()

    if sep:
        for token in raw_tail.split():
            if token.startswith("--"):
                flags.add(token[2:])
                continue
            if "=" in token and not token.startswith("="):
                key, value = token.split("=", 1)
                if key:
                    options[key] = value
                continue
            positional.append(token)

    return CommandArgs(
        command=command,
        positional=positional,
        options=options,
        flags=frozenset(flags),
        raw_tail=raw_tail,
    )


_INT_PATTERN = re.compile(r"\d+")


def parse_int(value: str) -> int:
    """Strict non-negative integer parse.

    Accepts only ``[0-9]+`` — rejects empty input, signs (``+5``/``-5``),
    underscores (``5_000``), whitespace inside, and anything else
    ``int(...)`` would otherwise allow silently.
    """

    cleaned = value.strip()
    if not cleaned or not _INT_PATTERN.fullmatch(cleaned):
        raise ValueError(f"cannot parse integer: {value!r}")
    return int(cleaned)


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"cannot parse boolean value: {value!r}")


_DURATION_UNITS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


def parse_duration_seconds(value: str) -> int:
    """Parse a short duration like ``30m``, ``2h``, ``1d`` or a bare ``90``.

    Bare integers are treated as seconds to stay friendly for power users.
    """

    raw = value.strip().lower()
    if not raw:
        raise ValueError("empty duration value")
    if raw[-1] in _DURATION_UNITS:
        number_part, unit = raw[:-1], raw[-1]
        multiplier = _DURATION_UNITS[unit]
    else:
        number_part, multiplier = raw, 1
    try:
        amount = int(number_part)
    except ValueError as exc:
        raise ValueError(f"cannot parse duration: {value!r}") from exc
    if amount <= 0:
        raise ValueError(f"duration must be positive: {value!r}")
    return amount * multiplier


def format_duration_seconds(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    for unit_seconds, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds % unit_seconds == 0:
            return f"{seconds // unit_seconds}{unit}"
    return f"{seconds}s"
