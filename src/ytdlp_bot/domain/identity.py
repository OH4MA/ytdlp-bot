"""Identity and opaque identifier value objects."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ytdlp_bot.domain.enums import FailureCode, Platform
from ytdlp_bot.domain.errors import ValidationError, failure

# Canonical decimal user IDs: no leading zeros except the single digit "0" is invalid.
_USER_ID_RE = re.compile(r"^[1-9][0-9]{0,39}$")
# Unpadded URL-safe base64-ish opaque IDs: at least 128 bits ≈ 22 chars.
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{22,64}$")
_CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9_:-]{1,128}$")
_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9_:-]{1,128}$")
_REQUEST_ID_MAX = 256
_STORAGE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{22,96}$")


def _invalid(field: str, reason: str) -> ValidationError:
    return ValidationError(
        failure(
            FailureCode.INVALID_COMMAND,
            diagnostic=f"invalid {field}: {reason}",
            safe_context={"field": field},
        )
    )


def validate_user_id(user_id: str) -> str:
    """Return canonical decimal user_id or raise ValidationError."""
    if not _USER_ID_RE.fullmatch(user_id):
        raise _invalid("user_id", "must be canonical decimal without leading zeros")
    return user_id


def validate_opaque_id(value: str, *, field: str = "id") -> str:
    """Validate job/artifact/confirmation opaque identifiers."""
    if not _OPAQUE_ID_RE.fullmatch(value):
        raise _invalid(field, "must be opaque URL-safe token length 22-64")
    return value


def validate_storage_key(value: str) -> str:
    """Validate service-generated storage keys."""
    if not _STORAGE_KEY_RE.fullmatch(value):
        raise _invalid("storage_key", "must be service-generated opaque key")
    return value


def validate_request_id(value: str) -> str:
    """Validate platform event request identifiers used for deduplication."""
    if not value or len(value) > _REQUEST_ID_MAX:
        raise _invalid("request_id", "must be non-empty and bounded")
    # Reject control characters.
    if any(ord(ch) < 32 for ch in value):
        raise _invalid("request_id", "must not contain control characters")
    return value


@dataclass(frozen=True, slots=True, order=False)
class Identity:
    """Immutable platform user identity."""

    platform: Platform
    user_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_id", validate_user_id(self.user_id))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Identity):
            return NotImplemented
        return self.platform is other.platform and self.user_id == other.user_id

    def __hash__(self) -> int:
        return hash((self.platform, self.user_id))

    def to_dict(self) -> dict[str, str]:
        return {"platform": self.platform.value, "user_id": self.user_id}

    @classmethod
    def parse(cls, text: str) -> Identity:
        """Parse 'platform:user_id' configuration form."""
        if ":" not in text:
            raise _invalid("identity", "expected platform:user_id")
        platform_raw, user_id = text.split(":", 1)
        try:
            platform = Platform(platform_raw)
        except ValueError as exc:
            raise _invalid("identity", "unknown platform") from exc
        return cls(platform=platform, user_id=user_id)


@dataclass(frozen=True, slots=True)
class JobId:
    """Opaque job identifier."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", validate_opaque_id(self.value, field="job_id"))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ArtifactId:
    """Opaque artifact identifier."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", validate_opaque_id(self.value, field="artifact_id"))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ConfirmationId:
    """Opaque administrator confirmation identifier."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", validate_opaque_id(self.value, field="confirmation_id"))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class MessageReference:
    """Durable platform message handle for progress edits."""

    platform: Platform
    chat_id: str
    message_id: str

    def __post_init__(self) -> None:
        if not _CHANNEL_ID_RE.fullmatch(self.chat_id):
            raise _invalid("chat_id", "invalid chat/channel id")
        if not _MESSAGE_ID_RE.fullmatch(self.message_id):
            raise _invalid("message_id", "invalid message id")

    def to_dict(self) -> dict[str, str]:
        return {
            "platform": self.platform.value,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
        }


@dataclass(frozen=True, slots=True)
class MessageContext:
    """Response target and effective delivery limits for a command."""

    platform: Platform
    chat_id: str
    response_target: str
    effective_upload_limit_bytes: int

    def __post_init__(self) -> None:
        if not _CHANNEL_ID_RE.fullmatch(self.chat_id):
            raise _invalid("chat_id", "invalid chat/channel id")
        if not self.response_target or len(self.response_target) > 128:
            raise _invalid("response_target", "invalid response target")
        if self.effective_upload_limit_bytes < 0:
            raise _invalid("effective_upload_limit_bytes", "must be non-negative int")

    def to_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform.value,
            "chat_id": self.chat_id,
            "response_target": self.response_target,
            "effective_upload_limit_bytes": self.effective_upload_limit_bytes,
        }
