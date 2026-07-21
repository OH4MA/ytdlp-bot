"""Runtime settings catalog metadata (types and bounds only)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SettingValueType(StrEnum):
    INTEGER = "integer"
    STRING = "string"
    BOOLEAN = "boolean"
    ENUM = "enum"


class SettingEffect(StrEnum):
    """When a setting change takes effect."""

    IMMEDIATE = "immediate"
    NEW_ARTIFACTS = "new_artifacts"
    NEW_LINKS = "new_links"


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    """Catalog entry defining a runtime-overridable setting."""

    key: str
    value_type: SettingValueType
    mutable: bool
    sensitive: bool
    effect: SettingEffect
    min_value: int | None = None
    max_value: int | None = None
    allowed_values: frozenset[str] | None = None
    description: str = ""


# Keys that may be overridden at runtime (non-secret).
SETTINGS_CATALOG: dict[str, SettingDefinition] = {
    "retention_seconds": SettingDefinition(
        key="retention_seconds",
        value_type=SettingValueType.INTEGER,
        mutable=True,
        sensitive=False,
        effect=SettingEffect.NEW_ARTIFACTS,
        min_value=60,
        max_value=7 * 24 * 3600,
        description="Artifact retention in seconds for newly created artifacts",
    ),
    "link_expiry_seconds": SettingDefinition(
        key="link_expiry_seconds",
        value_type=SettingValueType.INTEGER,
        mutable=True,
        sensitive=False,
        effect=SettingEffect.NEW_LINKS,
        min_value=60,
        max_value=24 * 3600,
        description="Signed link lifetime for newly issued links",
    ),
    "capacity_bytes": SettingDefinition(
        key="capacity_bytes",
        value_type=SettingValueType.INTEGER,
        mutable=True,
        sensitive=False,
        effect=SettingEffect.IMMEDIATE,
        min_value=1024 * 1024 * 100,
        max_value=10 * 1024**4,
        description="Logical storage capacity in bytes",
    ),
    "access_mode": SettingDefinition(
        key="access_mode",
        value_type=SettingValueType.ENUM,
        mutable=True,
        sensitive=False,
        effect=SettingEffect.IMMEDIATE,
        allowed_values=frozenset({"allow_all", "whitelist"}),
        description="Access control mode",
    ),
    "worker_concurrency": SettingDefinition(
        key="worker_concurrency",
        value_type=SettingValueType.INTEGER,
        mutable=True,
        sensitive=False,
        effect=SettingEffect.IMMEDIATE,
        min_value=1,
        max_value=16,
        description="Maximum concurrent media workers",
    ),
}


def validate_setting_value(key: str, value: Any) -> Any:
    """Validate a runtime setting value against the catalog."""
    from ytdlp_bot.domain.enums import FailureCode
    from ytdlp_bot.domain.errors import ValidationError, failure

    def _bad(msg: str) -> ValidationError:
        return ValidationError(
            failure(FailureCode.INVALID_COMMAND, diagnostic=msg, safe_context={"key": key})
        )

    definition = SETTINGS_CATALOG.get(key)
    if definition is None:
        raise _bad("unknown setting key")
    if not definition.mutable:
        raise _bad("setting is not mutable")
    if definition.sensitive:
        raise _bad("sensitive setting cannot be set via admin")

    if definition.value_type is SettingValueType.INTEGER:
        if not isinstance(value, int) or isinstance(value, bool):
            raise _bad("expected integer")
        if definition.min_value is not None and value < definition.min_value:
            raise _bad("below minimum")
        if definition.max_value is not None and value > definition.max_value:
            raise _bad("above maximum")
        return value
    if definition.value_type is SettingValueType.ENUM:
        if not isinstance(value, str):
            raise _bad("expected string enum")
        if definition.allowed_values and value not in definition.allowed_values:
            raise _bad("value not in allowed set")
        return value
    if definition.value_type is SettingValueType.BOOLEAN:
        if not isinstance(value, bool):
            raise _bad("expected boolean")
        return value
    if definition.value_type is SettingValueType.STRING:
        if not isinstance(value, str) or len(value) > 256:
            raise _bad("expected bounded string")
        return value
    raise _bad("unsupported type")
