"""Canonical command DTOs and strict normalization grammar."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ytdlp_bot.domain.enums import (
    AccessMode,
    AudioBitrate,
    CommandName,
    FailureCode,
    Platform,
    VideoQuality,
)
from ytdlp_bot.domain.errors import ValidationError, failure
from ytdlp_bot.domain.identity import (
    ArtifactId,
    Identity,
    JobId,
    MessageContext,
    validate_opaque_id,
    validate_request_id,
    validate_user_id,
)

_URL_MAX_LEN = 4096
# Whitespace used only around argument boundaries (Unicode spaces).
_WS_RE = re.compile(r"[\s\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+")
_COMMAND_TOKEN_RE = re.compile(r"^/?(ytdl|ytmp3|ytdl_status|ytdl_cancel|ytdl_help|ytdl_admin)$")
_SETTING_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DURATION_RE = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
_BYTE_SIZE_RE = re.compile(r"^(\d+)([KMGTP]?i?B)?$", re.IGNORECASE)

# Telegram bot username suffix after command: /ytdl@MyBot
_BOT_SUFFIX_RE = re.compile(r"^/([a-z0-9_]+)(?:@[A-Za-z0-9_]+)?$", re.IGNORECASE)


def _err(diagnostic: str, **ctx: str | int) -> ValidationError:
    return ValidationError(
        failure(
            FailureCode.INVALID_COMMAND,
            diagnostic=diagnostic,
            safe_context=ctx,
        )
    )


@dataclass(frozen=True, slots=True)
class YtdlArgs:
    """Arguments for /ytdl."""

    url: str
    quality: VideoQuality = VideoQuality.BEST


@dataclass(frozen=True, slots=True)
class Ytmp3Args:
    """Arguments for /ytmp3."""

    url: str
    bitrate: AudioBitrate = AudioBitrate.K320


@dataclass(frozen=True, slots=True)
class StatusArgs:
    """Arguments for /ytdl_status."""

    job_id: JobId | None = None
    renew: bool = False


@dataclass(frozen=True, slots=True)
class CancelArgs:
    """Arguments for /ytdl_cancel."""

    job_id: JobId


@dataclass(frozen=True, slots=True)
class HelpArgs:
    """Arguments for /ytdl_help (none)."""


@dataclass(frozen=True, slots=True)
class AdminRetentionSet:
    action: Literal["retention_set"] = "retention_set"
    duration_seconds: int = 0


@dataclass(frozen=True, slots=True)
class AdminLinkExpirySet:
    action: Literal["link_expiry_set"] = "link_expiry_set"
    duration_seconds: int = 0


@dataclass(frozen=True, slots=True)
class AdminCapacitySet:
    action: Literal["capacity_set"] = "capacity_set"
    capacity_bytes: int = 0
    confirmation_id: str | None = None


@dataclass(frozen=True, slots=True)
class AdminAccessModeSet:
    action: Literal["access_mode_set"] = "access_mode_set"
    mode: AccessMode = AccessMode.ALLOW_ALL


@dataclass(frozen=True, slots=True)
class AdminWhitelistAdd:
    action: Literal["whitelist_add"] = "whitelist_add"
    identity: Identity = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class AdminWhitelistRemove:
    action: Literal["whitelist_remove"] = "whitelist_remove"
    identity: Identity = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class AdminWhitelistList:
    action: Literal["whitelist_list"] = "whitelist_list"
    platform: Platform | None = None


@dataclass(frozen=True, slots=True)
class AdminStatus:
    action: Literal["status"] = "status"


@dataclass(frozen=True, slots=True)
class AdminCancel:
    action: Literal["cancel"] = "cancel"
    job_id: JobId = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class AdminArtifactDelete:
    action: Literal["artifact_delete"] = "artifact_delete"
    artifact_id: ArtifactId = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class AdminSettingReset:
    action: Literal["setting_reset"] = "setting_reset"
    setting_key: str = ""
    confirmation_id: str | None = None


AdminAction = (
    AdminRetentionSet
    | AdminLinkExpirySet
    | AdminCapacitySet
    | AdminAccessModeSet
    | AdminWhitelistAdd
    | AdminWhitelistRemove
    | AdminWhitelistList
    | AdminStatus
    | AdminCancel
    | AdminArtifactDelete
    | AdminSettingReset
)


@dataclass(frozen=True, slots=True)
class AdminArgs:
    """Typed administrator action payload."""

    action: AdminAction


CommandArguments = YtdlArgs | Ytmp3Args | StatusArgs | CancelArgs | HelpArgs | AdminArgs


@dataclass(frozen=True, slots=True)
class CommandRequest:
    """Normalized platform-neutral command request."""

    request_id: str
    identity: Identity
    context: MessageContext
    command: CommandName
    arguments: CommandArguments
    received_at: datetime

    def __post_init__(self) -> None:
        validate_request_id(self.request_id)
        if self.received_at.tzinfo is None:
            raise _err("received_at must be timezone-aware UTC")
        _assert_args_match(self.command, self.arguments)


def _assert_args_match(command: CommandName, arguments: CommandArguments) -> None:
    expected: type = {
        CommandName.YTDL: YtdlArgs,
        CommandName.YTMP3: Ytmp3Args,
        CommandName.YTDL_STATUS: StatusArgs,
        CommandName.YTDL_CANCEL: CancelArgs,
        CommandName.YTDL_HELP: HelpArgs,
        CommandName.YTDL_ADMIN: AdminArgs,
    }[command]
    if not isinstance(arguments, expected):
        raise _err("arguments type does not match command", command=command.value)


# ---------------------------------------------------------------------------
# Command result views (platform-neutral presentation DTOs)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AcceptedJob:
    kind: Literal["accepted_job"] = "accepted_job"
    job_id: JobId = None  # type: ignore[assignment]
    state: str = "queued"
    message_key: str = "job.accepted"


@dataclass(frozen=True, slots=True)
class StatusView:
    kind: Literal["status"] = "status"
    job_id: JobId | None = None
    state: str | None = None
    phase: str | None = None
    percent: int | None = None
    warning_codes: tuple[str, ...] = ()
    error_code: str | None = None
    renew_url: str | None = None
    artifact_available: bool = False
    message_key: str = "status.view"
    jobs: tuple[StatusView, ...] = ()


@dataclass(frozen=True, slots=True)
class HelpView:
    kind: Literal["help"] = "help"
    message_key: str = "help.main"


@dataclass(frozen=True, slots=True)
class AdminView:
    kind: Literal["admin"] = "admin"
    message_key: str = "admin.view"
    safe_fields: dict[str, str | int | bool] | None = None


@dataclass(frozen=True, slots=True)
class UserError:
    kind: Literal["user_error"] = "user_error"
    code: FailureCode = FailureCode.INVALID_COMMAND
    message_key: str = "failure.invalid_command"
    safe_context: dict[str, str | int | bool] | None = None


CommandResult = AcceptedJob | StatusView | HelpView | AdminView | UserError


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def normalize_command_token(token: str) -> CommandName:
    """Normalize a slash command token (optional leading slash / bot suffix)."""
    raw = token.strip()
    match = _BOT_SUFFIX_RE.fullmatch(raw) if raw.startswith("/") else None
    name = match.group(1).lower() if match else raw.lower().lstrip("/")
    if not _COMMAND_TOKEN_RE.fullmatch(name) and not _COMMAND_TOKEN_RE.fullmatch(f"/{name}"):
        # Accept bare names matching CommandName values.
        pass
    try:
        return CommandName(name)
    except ValueError as exc:
        raise _err("unknown command", token=name[:32]) from exc


def _split_args(text: str) -> list[str]:
    """Split on Unicode whitespace without expanding shell metacharacters."""
    stripped = text.strip()
    if not stripped:
        return []
    return [part for part in _WS_RE.split(stripped) if part]


def parse_duration_seconds(value: str) -> int:
    """Parse compact duration like 12h, 1d, 30m, 3600s into seconds."""
    text = value.strip().lower()
    if text.isdigit():
        seconds = int(text)
        if seconds <= 0:
            raise _err("duration must be positive")
        return seconds
    match = _DURATION_RE.fullmatch(text)
    if not match or text == "":
        raise _err("invalid duration", value=value[:32])
    days, hours, minutes, secs = (int(g or 0) for g in match.groups())
    total = days * 86400 + hours * 3600 + minutes * 60 + secs
    if total <= 0:
        raise _err("duration must be positive")
    return total


def parse_byte_size(value: str) -> int:
    """Parse integer byte sizes with optional KiB/MiB/GiB/TiB suffixes."""
    text = value.strip().replace(" ", "")
    match = _BYTE_SIZE_RE.fullmatch(text)
    if not match:
        raise _err("invalid byte size", value=value[:32])
    amount = int(match.group(1))
    unit = (match.group(2) or "B").lower()
    multipliers = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000**2,
        "mib": 1024**2,
        "gb": 1000**3,
        "gib": 1024**3,
        "tb": 1000**4,
        "tib": 1024**4,
        "pb": 1000**5,
        "pib": 1024**5,
    }
    if unit not in multipliers:
        raise _err("invalid byte unit", value=value[:32])
    result = amount * multipliers[unit]
    if result <= 0:
        raise _err("byte size must be positive")
    return result


def _parse_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"true", "1", "yes", "on"}:
        return True
    if lowered in {"false", "0", "no", "off"}:
        return False
    raise _err("invalid boolean", value=value[:16])


def _validate_url_field(url: str) -> str:
    if not isinstance(url, str) or not url:
        raise _err("url is required")
    if len(url) > _URL_MAX_LEN:
        raise _err("url exceeds maximum length", max_len=_URL_MAX_LEN)
    # Domain keeps the raw URL; NET module validates scheme/host/SSRF later.
    if any(ord(ch) < 32 for ch in url):
        raise _err("url contains control characters")
    return url


def parse_ytdl_args(parts: list[str]) -> YtdlArgs:
    if not parts:
        raise _err("url is required for /ytdl")
    url = _validate_url_field(parts[0])
    quality = VideoQuality.BEST
    if len(parts) > 1:
        try:
            quality = VideoQuality(parts[1])
        except ValueError as exc:
            raise _err("invalid quality", value=parts[1][:16]) from exc
    if len(parts) > 2:
        raise _err("extra arguments rejected", command="ytdl")
    return YtdlArgs(url=url, quality=quality)


def parse_ytmp3_args(parts: list[str]) -> Ytmp3Args:
    if not parts:
        raise _err("url is required for /ytmp3")
    url = _validate_url_field(parts[0])
    bitrate = AudioBitrate.K320
    if len(parts) > 1:
        try:
            bitrate = AudioBitrate(parts[1])
        except ValueError as exc:
            raise _err("invalid bitrate", value=parts[1][:16]) from exc
    if len(parts) > 2:
        raise _err("extra arguments rejected", command="ytmp3")
    return Ytmp3Args(url=url, bitrate=bitrate)


def parse_status_args(parts: list[str]) -> StatusArgs:
    job_id: JobId | None = None
    renew = False
    if not parts:
        return StatusArgs()
    # job_id is first positional when present.
    if parts[0].lower() not in {"renew", "true", "false"}:
        job_id = JobId(validate_opaque_id(parts[0], field="job_id"))
        rest = parts[1:]
    else:
        rest = parts
    for token in rest:
        if token.lower() in {"renew", "renew=true"}:
            renew = True
        elif token.lower().startswith("renew="):
            renew = _parse_bool(token.split("=", 1)[1])
        elif token.lower() in {"true", "false"} and job_id is not None:
            renew = _parse_bool(token)
        else:
            raise _err("unexpected status argument", value=token[:32])
    if renew and job_id is None:
        raise _err("renew requires job_id")
    return StatusArgs(job_id=job_id, renew=renew)


def parse_cancel_args(parts: list[str]) -> CancelArgs:
    if len(parts) != 1:
        raise _err("cancel requires exactly one job_id")
    return CancelArgs(job_id=JobId(validate_opaque_id(parts[0], field="job_id")))


def parse_help_args(parts: list[str]) -> HelpArgs:
    if parts:
        raise _err("help accepts no arguments")
    return HelpArgs()


def parse_admin_args(parts: list[str]) -> AdminArgs:
    if not parts:
        raise _err("admin action is required")
    head = parts[0].lower()
    rest = parts[1:]

    if head == "status" and not rest:
        return AdminArgs(action=AdminStatus())
    if head == "cancel":
        if len(rest) != 1:
            raise _err("admin cancel requires job_id")
        return AdminArgs(
            action=AdminCancel(job_id=JobId(validate_opaque_id(rest[0], field="job_id")))
        )
    if head == "artifact" and rest and rest[0].lower() == "delete":
        if len(rest) != 2:
            raise _err("admin artifact delete requires artifact_id")
        return AdminArgs(
            action=AdminArtifactDelete(
                artifact_id=ArtifactId(validate_opaque_id(rest[1], field="artifact_id"))
            )
        )
    if head == "retention" and rest and rest[0].lower() == "set":
        if len(rest) != 2:
            raise _err("admin retention set requires duration")
        return AdminArgs(action=AdminRetentionSet(duration_seconds=parse_duration_seconds(rest[1])))
    if head in {"link-expiry", "link_expiry"} and rest and rest[0].lower() == "set":
        if len(rest) != 2:
            raise _err("admin link-expiry set requires duration")
        return AdminArgs(
            action=AdminLinkExpirySet(duration_seconds=parse_duration_seconds(rest[1]))
        )
    if head == "capacity" and rest and rest[0].lower() == "set":
        if len(rest) not in {2, 3}:
            raise _err("admin capacity set requires size and optional confirmation")
        conf = rest[2] if len(rest) == 3 else None
        if conf is not None:
            conf = validate_opaque_id(conf, field="confirmation_id")
        return AdminArgs(
            action=AdminCapacitySet(
                capacity_bytes=parse_byte_size(rest[1]),
                confirmation_id=conf,
            )
        )
    if head in {"access-mode", "access_mode"} and rest and rest[0].lower() == "set":
        if len(rest) != 2:
            raise _err("admin access-mode set requires mode")
        try:
            mode = AccessMode(rest[1].lower())
        except ValueError as exc:
            raise _err("invalid access mode", value=rest[1][:16]) from exc
        return AdminArgs(action=AdminAccessModeSet(mode=mode))
    if head == "whitelist":
        if not rest:
            raise _err("whitelist subcommand required")
        sub = rest[0].lower()
        if sub == "list":
            platform: Platform | None = None
            if len(rest) == 2:
                try:
                    platform = Platform(rest[1].lower())
                except ValueError as exc:
                    raise _err("invalid platform", value=rest[1][:16]) from exc
            elif len(rest) > 2:
                raise _err("extra whitelist list arguments")
            return AdminArgs(action=AdminWhitelistList(platform=platform))
        if sub in {"add", "remove"}:
            if len(rest) != 3:
                raise _err(f"whitelist {sub} requires platform and user_id")
            try:
                platform = Platform(rest[1].lower())
            except ValueError as exc:
                raise _err("invalid platform", value=rest[1][:16]) from exc
            identity = Identity(platform=platform, user_id=validate_user_id(rest[2]))
            if sub == "add":
                return AdminArgs(action=AdminWhitelistAdd(identity=identity))
            return AdminArgs(action=AdminWhitelistRemove(identity=identity))
        raise _err("unknown whitelist subcommand", value=sub[:16])
    if head == "setting" and rest and rest[0].lower() == "reset":
        if len(rest) not in {2, 3}:
            raise _err("setting reset requires key and optional confirmation")
        key = rest[1]
        if not _SETTING_KEY_RE.fullmatch(key):
            raise _err("invalid setting key", value=key[:32])
        conf = rest[2] if len(rest) == 3 else None
        if conf is not None:
            conf = validate_opaque_id(conf, field="confirmation_id")
        return AdminArgs(action=AdminSettingReset(setting_key=key, confirmation_id=conf))

    raise _err("unknown admin action", value=head[:32])


def parse_text_command(text: str) -> tuple[CommandName, CommandArguments]:
    """Parse a Telegram-style text command into canonical DTOs.

    The URL value is not altered beyond surrounding whitespace split; scheme
    validation is performed later by the network safety module.
    """
    parts = _split_args(text)
    if not parts:
        raise _err("empty command")
    command = normalize_command_token(parts[0])
    arg_parts = parts[1:]
    parsers = {
        CommandName.YTDL: parse_ytdl_args,
        CommandName.YTMP3: parse_ytmp3_args,
        CommandName.YTDL_STATUS: parse_status_args,
        CommandName.YTDL_CANCEL: parse_cancel_args,
        CommandName.YTDL_HELP: parse_help_args,
        CommandName.YTDL_ADMIN: parse_admin_args,
    }
    return command, parsers[command](arg_parts)


def build_command_arguments(
    command: CommandName,
    *,
    url: str | None = None,
    quality: str | None = None,
    bitrate: str | None = None,
    job_id: str | None = None,
    renew: bool = False,
    admin_action: AdminAction | None = None,
) -> CommandArguments:
    """Build arguments from Discord-style typed options."""
    if command is CommandName.YTDL:
        if url is None:
            raise _err("url is required")
        q = VideoQuality(quality) if quality else VideoQuality.BEST
        return YtdlArgs(url=_validate_url_field(url), quality=q)
    if command is CommandName.YTMP3:
        if url is None:
            raise _err("url is required")
        b = AudioBitrate(bitrate) if bitrate else AudioBitrate.K320
        return Ytmp3Args(url=_validate_url_field(url), bitrate=b)
    if command is CommandName.YTDL_STATUS:
        jid = JobId(job_id) if job_id else None
        if renew and jid is None:
            raise _err("renew requires job_id")
        return StatusArgs(job_id=jid, renew=renew)
    if command is CommandName.YTDL_CANCEL:
        if job_id is None:
            raise _err("job_id is required")
        return CancelArgs(job_id=JobId(job_id))
    if command is CommandName.YTDL_HELP:
        return HelpArgs()
    if command is CommandName.YTDL_ADMIN:
        if admin_action is None:
            raise _err("admin action is required")
        return AdminArgs(action=admin_action)
    raise _err("unsupported command")
