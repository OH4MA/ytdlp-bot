"""HMAC-SHA-256 signed download tokens.

Complete URLs, nonces, and signatures must never be persisted or logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, unquote, urlencode, urlparse

from ytdlp_bot.ports.system import TokenClaims, TokenSigner

TOKEN_SCHEMA_VERSION = 1
DOWNLOAD_OPERATION = "download"  # shared by GET and HEAD
_NONCE_BYTES = 16
_SIG_BYTES = 32
_MAX_QUERY_LEN = 512
_MAX_COMPONENT = 256
_HARD_MAX_TTL_SECONDS = 7 * 24 * 3600
_ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{22,64}$")
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class TokenValidationError(Exception):
    """Generic public validation failure (no existence detail)."""

    def __init__(self, diagnostic: str = "token invalid") -> None:
        # English diagnostic only; never include query/token material.
        super().__init__(diagnostic)
        self.public_code = "not_found"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str, *, expected_len: int | None = None) -> bytes:
    if not _B64URL_RE.fullmatch(text):
        raise TokenValidationError("malformed encoding")
    pad = "=" * (-len(text) % 4)
    try:
        raw = base64.urlsafe_b64decode(text + pad)
    except Exception as exc:
        raise TokenValidationError("malformed encoding") from exc
    if expected_len is not None and len(raw) != expected_len:
        raise TokenValidationError("malformed encoding")
    return raw


def _lp(field: bytes) -> bytes:
    """Length-prefixed field (uint32 big-endian length + bytes)."""
    if len(field) > 0xFFFF:
        raise TokenValidationError("field too long")
    return len(field).to_bytes(4, "big") + field


def canonical_display_name(display_name: str) -> str:
    """Canonical percent-encoding for path/signature (safe filename subset)."""
    # Encode everything except unreserved RFC 3986.
    return quote(display_name, safe="-_.~")


def encode_signing_payload(
    *,
    artifact_id: str,
    display_name: str,
    exp: int,
    token_version: int,
    nonce_b64: str,
) -> bytes:
    """Deterministic length-prefixed canonical byte sequence."""
    parts = [
        _lp(str(TOKEN_SCHEMA_VERSION).encode("ascii")),
        _lp(DOWNLOAD_OPERATION.encode("ascii")),
        _lp(artifact_id.encode("ascii")),
        _lp(canonical_display_name(display_name).encode("ascii")),
        _lp(str(exp).encode("ascii")),
        _lp(str(token_version).encode("ascii")),
        _lp(nonce_b64.encode("ascii")),
    ]
    return b"".join(parts)


@dataclass(frozen=True, slots=True)
class IssuedLink:
    """Issued link material for delivery (caller builds user message)."""

    url: str
    claims: TokenClaims
    expires_at: datetime


class HmacTokenSigner:
    """Production TokenSigner using HMAC-SHA-256."""

    def __init__(self, secret: bytes, *, public_base_url: str) -> None:
        if len(secret) < 32:
            raise ValueError("signing secret too short")
        self._secret = secret
        base = public_base_url.rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme != "https" or parsed.query or parsed.fragment:
            raise ValueError("public_base_url must be canonical https")
        self._base = base

    def sign(self, claims: TokenClaims, *, display_name: str) -> IssuedLink:
        if not _ARTIFACT_ID_RE.fullmatch(claims.artifact_id):
            raise ValueError("invalid artifact_id")
        nonce = claims.nonce or _b64url_encode(secrets.token_bytes(_NONCE_BYTES))
        if len(_b64url_decode(nonce, expected_len=_NONCE_BYTES)) != _NONCE_BYTES:
            raise ValueError("invalid nonce")
        payload = encode_signing_payload(
            artifact_id=claims.artifact_id,
            display_name=display_name,
            exp=claims.exp,
            token_version=claims.token_version,
            nonce_b64=nonce,
        )
        sig = hmac.new(self._secret, payload, hashlib.sha256).digest()
        sig_b64 = _b64url_encode(sig)
        enc_name = canonical_display_name(display_name)
        path = f"/v1/artifacts/{claims.artifact_id}/{enc_name}"
        query = urlencode(
            {
                "exp": str(claims.exp),
                "v": str(claims.token_version),
                "n": nonce,
                "sig": sig_b64,
            },
            doseq=False,
        )
        url = f"{self._base}{path}?{query}"
        full_claims = TokenClaims(
            artifact_id=claims.artifact_id,
            token_version=claims.token_version,
            exp=claims.exp,
            nonce=nonce,
            job_id=claims.job_id,
        )
        return IssuedLink(
            url=url,
            claims=full_claims,
            expires_at=datetime.fromtimestamp(claims.exp, tz=UTC),
        )

    def verify(self, query_params: dict[str, str]) -> TokenClaims | None:
        """Verify signature and return claims, or None on any failure."""
        try:
            return self._verify_strict(query_params)
        except TokenValidationError:
            return None

    def _verify_strict(self, query_params: dict[str, str]) -> TokenClaims:
        # Path context may be injected under reserved keys by verify_request.
        artifact_id = query_params.get("_artifact_id", "")
        display_name = query_params.get("_display_name", "")
        public_params = {
            k: v for k, v in query_params.items() if not k.startswith("_")
        }

        if sum(len(k) + len(v) for k, v in public_params.items()) > _MAX_QUERY_LEN:
            raise TokenValidationError("query too long")
        required = {"exp", "v", "n", "sig"}
        if set(public_params) != required:
            raise TokenValidationError("query shape")
        for _key, value in public_params.items():
            if not value or len(value) > _MAX_COMPONENT:
                raise TokenValidationError("component bound")

        exp = _parse_strict_int(public_params["exp"])
        version = _parse_strict_int(public_params["v"])
        if version < 1:
            raise TokenValidationError("version")
        nonce = public_params["n"]
        sig = public_params["sig"]
        _b64url_decode(nonce, expected_len=_NONCE_BYTES)
        sig_raw = _b64url_decode(sig, expected_len=_SIG_BYTES)

        if not artifact_id:
            raise TokenValidationError("missing path context")

        payload = encode_signing_payload(
            artifact_id=artifact_id,
            display_name=display_name,
            exp=exp,
            token_version=version,
            nonce_b64=nonce,
        )
        expected = hmac.new(self._secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, sig_raw):
            raise TokenValidationError("bad signature")
        return TokenClaims(
            artifact_id=artifact_id,
            token_version=version,
            exp=exp,
            nonce=nonce,
        )

    def verify_request(
        self,
        *,
        artifact_id: str,
        display_name_path: str,
        query_params: dict[str, str],
        now: datetime,
        artifact_token_version: int,
        artifact_expires_at: datetime,
        access_available: bool,
    ) -> TokenClaims:
        """Full validation including artifact state and time bounds."""
        # Reconstruct display name for canonical comparison.
        try:
            display_name = unquote(display_name_path)
        except Exception as exc:
            raise TokenValidationError("display name") from exc
        if canonical_display_name(display_name) != display_name_path:
            # Path must already be canonical encoding.
            raise TokenValidationError("noncanonical display name")

        params = dict(query_params)
        params["_artifact_id"] = artifact_id
        params["_display_name"] = display_name
        claims = self._verify_strict(params)

        now_ts = int(now.timestamp())
        art_exp = int(artifact_expires_at.timestamp())
        if claims.exp > art_exp:
            raise TokenValidationError("exp beyond artifact")
        if claims.exp - now_ts > _HARD_MAX_TTL_SECONDS:
            raise TokenValidationError("exp hard bound")
        if now_ts >= claims.exp:
            raise TokenValidationError("token expired")
        if now_ts >= art_exp:
            raise TokenValidationError("artifact expired")
        if not access_available:
            raise TokenValidationError("artifact unavailable")
        if claims.token_version != artifact_token_version:
            raise TokenValidationError("version revoked")
        if claims.artifact_id != artifact_id:
            raise TokenValidationError("artifact mismatch")
        return claims


def _parse_strict_int(text: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]{0,15}|0", text):
        # Allow 0 only for tests of rejection paths later; exp must be positive.
        if text == "0":
            return 0
        raise TokenValidationError("integer")
    if text != "0" and text.startswith("0"):
        raise TokenValidationError("integer padding")
    try:
        return int(text)
    except ValueError as exc:
        raise TokenValidationError("integer") from exc


def issue_download_link(
    signer: HmacTokenSigner,
    *,
    artifact_id: str,
    display_name: str,
    token_version: int,
    now: datetime,
    link_lifetime_seconds: int,
    artifact_expires_at: datetime,
    job_id: str | None = None,
    nonce: str | None = None,
) -> IssuedLink:
    """Issue a signed URL with expiry = min(now+lifetime, artifact expiry)."""
    now_ts = int(now.timestamp())
    art_exp = int(artifact_expires_at.timestamp())
    exp = min(now_ts + link_lifetime_seconds, art_exp)
    if exp <= now_ts:
        raise TokenValidationError("artifact already expired")
    claims = TokenClaims(
        artifact_id=artifact_id,
        token_version=token_version,
        exp=exp,
        nonce=nonce or _b64url_encode(secrets.token_bytes(_NONCE_BYTES)),
        job_id=job_id,
    )
    return signer.sign(claims, display_name=display_name)


# Satisfy TokenSigner protocol structural typing via methods above.
_ = TokenSigner
