"""Locale catalog loader (pure JSON, no I/O after load)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib import resources


def load_zh_tw_catalog() -> dict[str, str]:
    """Load the bundled zh-TW locale catalog."""
    package = "ytdlp_bot.locales"
    text = resources.files(package).joinpath("zh_TW.json").read_text(encoding="utf-8")
    raw: object = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("locale catalog must be an object")
    out: dict[str, str] = {}
    for raw_key, raw_value in raw.items():
        key = str(raw_key)
        if key == "locale":
            continue
        if not isinstance(raw_value, str):
            raise ValueError(f"invalid locale entry: {key!r}")
        out[key] = raw_value
    return out


def format_message(catalog: Mapping[str, str], message_key: str, **kwargs: object) -> str:
    """Interpolate a locale string with safe str() of kwargs."""
    template = catalog.get(message_key, catalog.get("value.unknown", "未知"))
    safe = {k: "" if v is None else str(v) for k, v in kwargs.items()}
    try:
        return template.format_map(safe)
    except (KeyError, ValueError):
        return template
