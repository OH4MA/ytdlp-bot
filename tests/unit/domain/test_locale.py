"""FND-11: zh-TW locale catalog completeness."""

from __future__ import annotations

import re

import pytest

from ytdlp_bot.domain.enums import FailureCode, WarningCode, WorkerPhase
from ytdlp_bot.domain.locale import format_message, load_zh_tw_catalog


@pytest.mark.unit
def test_catalog_loads_and_has_legal_reminder() -> None:
    catalog = load_zh_tw_catalog()
    assert "legal.use_reminder" in catalog
    assert catalog["legal.use_reminder"]
    # Traditional Chinese characters present (heuristic).
    assert any("\u4e00" <= ch <= "\u9fff" for ch in catalog["help.main"])


@pytest.mark.unit
def test_failure_and_warning_keys_present() -> None:
    catalog = load_zh_tw_catalog()
    for code in FailureCode:
        key = f"failure.{code.value.lower()}"
        assert key in catalog, key
    for code in WarningCode:
        key = f"warning.{code.value}"
        assert key in catalog, key
    for phase in WorkerPhase:
        key = f"progress.phase.{phase.value}"
        assert key in catalog, key


@pytest.mark.unit
def test_placeholder_shape() -> None:
    catalog = load_zh_tw_catalog()
    for key, value in catalog.items():
        placeholders = re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", value)
        # format with empty placeholders should not raise for known keys.
        kwargs = {name: "x" for name in placeholders}
        formatted = format_message(catalog, message_key=key, **kwargs)
        assert isinstance(formatted, str)
