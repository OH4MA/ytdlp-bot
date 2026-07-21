"""FND-03: identity and opaque identifiers."""

from __future__ import annotations

import pytest

from ytdlp_bot.domain.enums import Platform
from ytdlp_bot.domain.errors import ValidationError
from ytdlp_bot.domain.identity import (
    ArtifactId,
    Identity,
    JobId,
    MessageContext,
    MessageReference,
)


@pytest.mark.unit
def test_identity_equality_includes_platform() -> None:
    a = Identity(platform=Platform.TELEGRAM, user_id="12345")
    b = Identity(platform=Platform.DISCORD, user_id="12345")
    c = Identity(platform=Platform.TELEGRAM, user_id="12345")
    assert a != b
    assert a == c
    assert hash(a) == hash(c)
    assert a.to_dict() == {"platform": "telegram", "user_id": "12345"}


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["", "0123", "0", "abc", "-1", "1.5", " 1"])
def test_invalid_user_ids(bad: str) -> None:
    with pytest.raises(ValidationError):
        Identity(platform=Platform.TELEGRAM, user_id=bad)


@pytest.mark.unit
def test_identity_parse() -> None:
    ident = Identity.parse("discord:999888777666")
    assert ident.platform is Platform.DISCORD
    assert ident.user_id == "999888777666"
    with pytest.raises(ValidationError):
        Identity.parse("nope")


@pytest.mark.unit
def test_opaque_ids() -> None:
    good = "A" * 22
    assert JobId(good).value == good
    assert ArtifactId(good).value == good
    with pytest.raises(ValidationError):
        JobId("short")
    with pytest.raises(ValidationError):
        JobId("has spaces!!!!!!!!!!!!!")


@pytest.mark.unit
def test_message_reference_and_context() -> None:
    ref = MessageReference(platform=Platform.TELEGRAM, chat_id="-100123", message_id="42")
    assert ref.to_dict()["message_id"] == "42"
    ctx = MessageContext(
        platform=Platform.TELEGRAM,
        chat_id="-100123",
        response_target="-100123",
        effective_upload_limit_bytes=50_000_000,
    )
    assert ctx.effective_upload_limit_bytes == 50_000_000
    with pytest.raises(ValidationError):
        MessageContext(
            platform=Platform.TELEGRAM,
            chat_id="-100123",
            response_target="-100123",
            effective_upload_limit_bytes=-1,
        )
