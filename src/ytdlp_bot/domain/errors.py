"""Safe domain failure model.

Domain exceptions carry only stable codes and bounded non-sensitive context.
Raw external exception text must never enter these types.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ytdlp_bot.domain.enums import FailureCode

# Upper bound for safe_context entries and string values.
_MAX_CONTEXT_KEYS = 16
_MAX_CONTEXT_VALUE_LEN = 128
_MAX_DIAGNOSTIC_LEN = 512


def _sanitize_context(context: Mapping[str, Any] | None) -> dict[str, str | int | float | bool]:
    """Copy and bound safe context values; drop unsafe types."""
    if not context:
        return {}
    out: dict[str, str | int | float | bool] = {}
    for index, (raw_key, value) in enumerate(context.items()):
        if index >= _MAX_CONTEXT_KEYS:
            break
        key = str(raw_key)
        if not key or len(key) > 64:
            continue
        if isinstance(value, bool) or (isinstance(value, int) and not isinstance(value, bool)) or isinstance(value, float):
            out[key] = value
        elif isinstance(value, str):
            out[key] = value[:_MAX_CONTEXT_VALUE_LEN]
        else:
            # Never coerce arbitrary objects into user-visible context.
            continue
    return out


@dataclass(frozen=True, slots=True)
class DomainFailure:
    """Structured, serializable domain failure."""

    code: FailureCode
    user_message_key: str
    retryable: bool = False
    operator_actionable: bool = False
    safe_context: Mapping[str, str | int | float | bool] = field(default_factory=dict)
    diagnostic: str = ""

    def __post_init__(self) -> None:
        cleaned: dict[str, str | int | float | bool] = _sanitize_context(dict(self.safe_context))
        object.__setattr__(self, "safe_context", cleaned)
        diagnostic = self.diagnostic[:_MAX_DIAGNOSTIC_LEN] if self.diagnostic else ""
        object.__setattr__(self, "diagnostic", diagnostic)
        if not self.user_message_key:
            object.__setattr__(
                self,
                "user_message_key",
                f"failure.{self.code.value.lower()}",
            )

    def to_dict(self) -> dict[str, object]:
        """Secret-free serialization for tests and logs."""
        return {
            "code": self.code.value,
            "user_message_key": self.user_message_key,
            "retryable": self.retryable,
            "operator_actionable": self.operator_actionable,
            "safe_context": dict(self.safe_context),
            "diagnostic": self.diagnostic,
        }


class DomainError(Exception):
    """Base exception for domain and application policy failures."""

    def __init__(self, failure: DomainFailure) -> None:
        self.failure = failure
        super().__init__(failure.code.value)

    @property
    def code(self) -> FailureCode:
        return self.failure.code


class ValidationError(DomainError):
    """Input or command shape rejected by domain rules."""


class AuthorizationError(DomainError):
    """Caller is not permitted to perform the operation."""


class ConflictError(DomainError):
    """Optimistic concurrency conflict."""


class NotFoundError(DomainError):
    """Resource not found for the authorized scope."""


class IllegalTransitionError(DomainError):
    """State machine rejected an illegal edge (developer error path)."""


def failure(
    code: FailureCode,
    *,
    retryable: bool = False,
    operator_actionable: bool = False,
    safe_context: Mapping[str, Any] | None = None,
    diagnostic: str = "",
    user_message_key: str | None = None,
) -> DomainFailure:
    """Factory for DomainFailure with default locale key."""
    return DomainFailure(
        code=code,
        user_message_key=user_message_key or f"failure.{code.value.lower()}",
        retryable=retryable,
        operator_actionable=operator_actionable,
        safe_context=safe_context or {},
        diagnostic=diagnostic,
    )


# Default retry classification for each FailureCode.
_DEFAULT_RETRYABLE: frozenset[FailureCode] = frozenset(
    {
        FailureCode.ACKNOWLEDGEMENT_FAILED,
        FailureCode.DOWNLOAD_FAILED,
        FailureCode.PLATFORM_RATE_LIMITED,
        FailureCode.PLATFORM_UNAVAILABLE,
    }
)


def default_retryable(code: FailureCode) -> bool:
    """Whether the failure code may be retried under budget."""
    return code in _DEFAULT_RETRYABLE
