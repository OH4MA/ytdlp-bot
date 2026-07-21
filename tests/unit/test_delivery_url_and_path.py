"""AC08/AC09: local path resolution and signed URL in FinalOutcomeView."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.fakes.platform import FakePlatformPort
from ytdlp_bot.adapters.security.signed_tokens import DownloadLinkIssuer, HmacTokenSigner
from ytdlp_bot.adapters.storage.local_store import LocalArtifactStore
from ytdlp_bot.application.delivery import DeliveryService
from ytdlp_bot.domain.enums import ArtifactAccessState, MediaType, Platform
from ytdlp_bot.domain.identity import ArtifactId, JobId, MessageContext, MessageReference
from ytdlp_bot.domain.jobs import Artifact
from ytdlp_bot.domain.progress import FinalOutcomeView


@pytest.mark.unit
@pytest.mark.asyncio
async def test_signed_link_includes_url_in_final_view(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "s")
    key = "S" * 22
    # Create artifact file under store.
    jid = JobId("J" * 22)
    ws = await store.create_job_workspace(jid)
    src = Path(ws) / "big.bin"
    src.write_bytes(b"x" * 100)
    await store.atomically_publish(str(src), key)

    platform = FakePlatformPort()
    platform.upload_outcome = __import__(
        "ytdlp_bot.domain.enums", fromlist=["UploadOutcome"]
    ).UploadOutcome.TOO_LARGE
    # Force signed link by small limit on context.
    signer = HmacTokenSigner(b"S" * 32, public_base_url="https://dl.example.invalid")
    delivery = DeliveryService(
        platform=platform,
        link_issuer=DownloadLinkIssuer(signer),
        link_lifetime_seconds=3600,
        path_resolver=store,
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    art = Artifact(
        artifact_id=ArtifactId("A" * 22),
        job_id=jid,
        storage_key=key,
        display_name="big.bin",
        media_type=MediaType.VIDEO_MP4,
        byte_size=100,
        ready_at=now,
        expires_at=now + timedelta(hours=1),
        access_state=ArtifactAccessState.AVAILABLE,
    )
    ctx = MessageContext(
        platform=Platform.TELEGRAM,
        chat_id="1",
        response_target="1",
        effective_upload_limit_bytes=10,  # force signed link
    )
    ref = MessageReference(platform=Platform.TELEGRAM, chat_id="1", message_id="9")
    result = await delivery.deliver(
        job_id=jid,
        artifact=art,
        context=ctx,
        message_reference=ref,
        now=now,
    )
    assert result.plan.value == "signed_link"
    finals = [c for c in platform.calls if c[0] == "send_final"]
    assert finals
    final_call = finals[-1]
    assert isinstance(final_call, tuple)
    view = final_call[1][1]  # type: ignore[index]
    assert isinstance(view, FinalOutcomeView)
    assert view.download_url is not None
    assert view.download_url.startswith("https://dl.example.invalid/v1/artifacts/")
    assert "sig=" in view.download_url


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_upload_resolves_local_path(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "s")
    key = "S" * 22
    jid = JobId("J" * 22)
    ws = await store.create_job_workspace(jid)
    src = Path(ws) / "small.bin"
    src.write_bytes(b"hello")
    await store.atomically_publish(str(src), key)

    platform = FakePlatformPort()
    signer = HmacTokenSigner(b"S" * 32, public_base_url="https://dl.example.invalid")
    delivery = DeliveryService(
        platform=platform,
        link_issuer=DownloadLinkIssuer(signer),
        link_lifetime_seconds=3600,
        path_resolver=store,
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    art = Artifact(
        artifact_id=ArtifactId("A" * 22),
        job_id=jid,
        storage_key=key,
        display_name="small.bin",
        media_type=MediaType.VIDEO_MP4,
        byte_size=5,
        ready_at=now,
        expires_at=now + timedelta(hours=1),
    )
    ctx = MessageContext(
        platform=Platform.TELEGRAM,
        chat_id="1",
        response_target="1",
        effective_upload_limit_bytes=1_000_000,
    )
    ref = MessageReference(platform=Platform.TELEGRAM, chat_id="1", message_id="9")
    result = await delivery.deliver(
        job_id=jid,
        artifact=art,
        context=ctx,
        message_reference=ref,
        now=now,
    )
    assert result.plan.value == "direct_upload"
    uploads = [c for c in platform.calls if c[0] == "upload_artifact"]
    assert uploads
    upload_call = uploads[-1]
    assert isinstance(upload_call, tuple)
    desc = upload_call[1][1]  # type: ignore[index]
    assert desc.local_path is not None
    assert Path(desc.local_path).is_file()
    assert desc.storage_key == key
    assert desc.local_path != key
