"""TOK module unit and security tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.fakes.system import DeterministicIdGenerator, FakeClock
from ytdlp_bot.adapters.security.signed_tokens import (
    HmacTokenSigner,
    TokenValidationError,
    canonical_display_name,
    encode_signing_payload,
    issue_download_link,
)
from ytdlp_bot.ports.system import TokenClaims

SECRET = b"S" * 32
BASE = "https://downloads.example.invalid"
AID = "A" * 22


@pytest.fixture
def signer() -> HmacTokenSigner:
    return HmacTokenSigner(SECRET, public_base_url=BASE)


@pytest.mark.unit
def test_canonical_payload_is_stable() -> None:
    a = encode_signing_payload(
        artifact_id=AID,
        display_name="clip.mp4",
        exp=1700000000,
        token_version=1,
        nonce_b64="n" * 22,
    )
    b = encode_signing_payload(
        artifact_id=AID,
        display_name="clip.mp4",
        exp=1700000000,
        token_version=1,
        nonce_b64="n" * 22,
    )
    assert a == b
    # Field permutation changes payload.
    c = encode_signing_payload(
        artifact_id=AID,
        display_name="clip.mp4",
        exp=1700000001,
        token_version=1,
        nonce_b64="n" * 22,
    )
    assert a != c


@pytest.mark.unit
def test_issue_and_verify_roundtrip(signer: HmacTokenSigner) -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    ids = DeterministicIdGenerator()
    link = issue_download_link(
        signer,
        artifact_id=AID,
        display_name="影片 clip.mp4",
        token_version=1,
        now=clock.now(),
        link_lifetime_seconds=3600,
        artifact_expires_at=clock.now() + timedelta(hours=12),
        nonce=ids.link_nonce()[:22].ljust(22, "A"),
    )
    assert link.url.startswith(BASE + "/v1/artifacts/")
    assert " " not in link.url
    # Parse query
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(link.url)
    qs = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=False).items()}
    path_parts = parsed.path.strip("/").split("/")
    assert path_parts[0:3] == ["v1", "artifacts", AID]
    display_path = path_parts[3]
    assert display_path == canonical_display_name("影片 clip.mp4")

    claims = signer.verify_request(
        artifact_id=AID,
        display_name_path=display_path,
        query_params=qs,
        now=clock.now(),
        artifact_token_version=1,
        artifact_expires_at=clock.now() + timedelta(hours=12),
        access_available=True,
    )
    assert claims.artifact_id == AID
    assert claims.token_version == 1


@pytest.mark.unit
def test_tamper_signature_rejected(signer: HmacTokenSigner) -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    link = issue_download_link(
        signer,
        artifact_id=AID,
        display_name="a.mp4",
        token_version=1,
        now=clock.now(),
        link_lifetime_seconds=3600,
        artifact_expires_at=clock.now() + timedelta(hours=1),
    )
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(link.url)
    qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    qs["sig"] = qs["sig"][:-1] + ("A" if qs["sig"][-1] != "A" else "B")
    with pytest.raises(TokenValidationError):
        signer.verify_request(
            artifact_id=AID,
            display_name_path="a.mp4",
            query_params=qs,
            now=clock.now(),
            artifact_token_version=1,
            artifact_expires_at=clock.now() + timedelta(hours=1),
            access_available=True,
        )


@pytest.mark.unit
def test_expiry_and_version(signer: HmacTokenSigner) -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    art_exp = clock.now() + timedelta(hours=2)
    link = issue_download_link(
        signer,
        artifact_id=AID,
        display_name="a.mp4",
        token_version=1,
        now=clock.now(),
        link_lifetime_seconds=3600,
        artifact_expires_at=art_exp,
    )
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(link.url)
    qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    clock.advance(timedelta(hours=2))
    with pytest.raises(TokenValidationError):
        signer.verify_request(
            artifact_id=AID,
            display_name_path="a.mp4",
            query_params=qs,
            now=clock.now(),
            artifact_token_version=1,
            artifact_expires_at=art_exp,
            access_available=True,
        )

    clock.reset(datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(TokenValidationError):
        signer.verify_request(
            artifact_id=AID,
            display_name_path="a.mp4",
            query_params=qs,
            now=clock.now(),
            artifact_token_version=2,  # revoked
            artifact_expires_at=art_exp,
            access_available=True,
        )


@pytest.mark.unit
def test_expiry_bounded_by_artifact(signer: HmacTokenSigner) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    art_exp = now + timedelta(minutes=10)
    link = issue_download_link(
        signer,
        artifact_id=AID,
        display_name="a.mp4",
        token_version=1,
        now=now,
        link_lifetime_seconds=3600,
        artifact_expires_at=art_exp,
    )
    assert link.claims.exp == int(art_exp.timestamp())


@pytest.mark.unit
def test_unknown_query_param_rejected(signer: HmacTokenSigner) -> None:
    claims = TokenClaims(artifact_id=AID, token_version=1, exp=2_000_000_000, nonce="n" * 22)
    # force valid nonce length via real issue
    now = datetime(2026, 1, 1, tzinfo=UTC)
    link = issue_download_link(
        signer,
        artifact_id=AID,
        display_name="a.mp4",
        token_version=1,
        now=now,
        link_lifetime_seconds=60,
        artifact_expires_at=now + timedelta(hours=1),
    )
    from urllib.parse import parse_qs, urlparse

    qs = {k: v[0] for k, v in parse_qs(urlparse(link.url).query).items()}
    qs["extra"] = "1"
    with pytest.raises(TokenValidationError):
        signer.verify_request(
            artifact_id=AID,
            display_name_path="a.mp4",
            query_params=qs,
            now=now,
            artifact_token_version=1,
            artifact_expires_at=now + timedelta(hours=1),
            access_available=True,
        )
    _ = claims
