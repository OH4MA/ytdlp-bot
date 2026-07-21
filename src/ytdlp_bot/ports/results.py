"""Neutral result types shared by port protocols."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Conflict:
    """Optimistic concurrency conflict."""

    expected_version: int
    actual_version: int | None = None
    message: str = "version conflict"


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T


Result = Ok[T] | Conflict
