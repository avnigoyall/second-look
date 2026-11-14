"""Datetimes in API responses always carry a UTC offset.

Browsers read an ISO string without an offset as local time, so a naive UTC value would show hours off.
backend/db.py makes MongoDB return aware UTC datetimes; UtcDateTime is the second guard on every response
model field, for values that come from anywhere else (Nessie strings, older code paths).
"""
from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator


def as_utc(value: datetime) -> datetime:
    """A naive datetime is UTC (that is how MongoDB and this app store them). Aware ones convert to UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


UtcDateTime = Annotated[datetime, AfterValidator(as_utc)]  # serializes as "2026-11-14T18:57:21Z"
