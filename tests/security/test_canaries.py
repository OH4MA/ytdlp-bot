"""OBS secret canary suite: redaction of tokens, URLs, and bearer material."""

from __future__ import annotations

import pytest

from ytdlp_bot.adapters.security.redaction import REDACTED, redact_string, redact_value


@pytest.mark.security
def test_secret_canaries_redacted() -> None:
    payload = {
        "bot_token": "123456:ABC-DEF",
        "signing_secret": "S" * 32,
        "authorization": "Bearer super-secret-token",
        "source_url": "https://user:pass@cdn.example.com/path?token=xyz",
        "source_display": "https://cdn.example.com/video",
        "nested": {"password": "hunter2", "ok": 1},
        "message": "download failed for https://cdn.example.com/a/b?sig=1 with Bearer abc.def",
    }
    out = redact_value(payload)
    text = str(out)
    assert "super-secret-token" not in text
    assert "hunter2" not in text
    assert "user:pass" not in text
    assert "ABC-DEF" not in text
    assert out["bot_token"] == REDACTED
    assert out["signing_secret"] == REDACTED
    assert "cdn.example.com" in str(out.get("source_display", ""))
    assert "xyz" not in redact_string("Authorization: Bearer xyz", field="authorization")
