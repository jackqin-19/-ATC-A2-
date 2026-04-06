from __future__ import annotations

from datetime import UTC, datetime


DATETIME_WITH_MS = "%Y-%m-%d %H:%M:%S.%f"
DATETIME_NO_MS = "%Y-%m-%d %H:%M:%S"


def utcnow_text() -> str:
    return datetime.now(UTC).strftime(DATETIME_WITH_MS)[:-3]


def parse_datetime(value: str) -> datetime:
    try:
        if "." in value:
            return datetime.strptime(value, DATETIME_WITH_MS)
        return datetime.strptime(value, DATETIME_NO_MS)
    except ValueError as exc:
        raise ValueError(
            f"Invalid datetime '{value}'. Expected yyyy-MM-dd HH:mm:ss or yyyy-MM-dd HH:mm:ss.SSS"
        ) from exc


def format_datetime(value: datetime, with_ms: bool = True) -> str:
    text = value.strftime(DATETIME_WITH_MS if with_ms else DATETIME_NO_MS)
    return text[:-3] if with_ms else text
