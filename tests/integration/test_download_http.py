"""Phase 1 exit path: SQLite artifact → signed URL → range stream."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp import ClientSession

from ytdlp_bot.adapters.http.application import create_app, start_test_server
from ytdlp_bot.adapters.http.downloads import DownloadService
from ytdlp_bot.adapters.http.health import HealthController, ReadinessState
from ytdlp_bot.adapters.persistence.sqlite.connection import open_connection
from ytdlp_bot.adapters.persistence.sqlite.migrate import apply_migrations
from ytdlp_bot.adapters.persistence.sqlite.repositories import (
    SqliteArtifactRepository,
    SqliteJobRepository,
)
from ytdlp_bot.adapters.security.signed_tokens import HmacTokenSigner, issue_download_link
from ytdlp_bot.adapters.storage.local_store import LocalArtifactStore
from ytdlp_bot.application.artifact_access import (
    ArtifactAccessCoordinator,
    InMemoryArtifactLeaseRegistry,
)
from ytdlp_bot.domain.enums import JobState, MediaMode, MediaType, Platform
from ytdlp_bot.domain.identity import ArtifactId, Identity, JobId, MessageContext
from ytdlp_bot.domain.jobs import Artifact, Job


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sign_and_range_stream(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "service.sqlite3"
    store = LocalArtifactStore(tmp_path / "data")
    conn = await open_connection(db_path)
    await apply_migrations(conn, now_ms=1)

    jobs = SqliteJobRepository(conn)
    arts = SqliteArtifactRepository(conn)
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    job = await jobs.create(
        Job(
            job_id=JobId("J" * 22),
            idempotency_key="telegram:1",
            owner=Identity(platform=Platform.TELEGRAM, user_id="1"),
            message_context=MessageContext(
                platform=Platform.TELEGRAM,
                chat_id="1",
                response_target="1",
                effective_upload_limit_bytes=1_000_000,
            ),
            request_mode=MediaMode.VIDEO,
            selected_preset="best",
            source_display="https://example.com",
            state=JobState.COMPLETED,
            dispatchable=False,
            acknowledged_at=now,
            created_at=now,
            updated_at=now,
            ready_at=now,
        )
    )
    # Publish a real file.
    ws = await store.create_job_workspace(job.job_id)
    src = Path(ws) / "out.bin"
    payload = bytes(range(256))
    src.write_bytes(payload)
    key = "S" * 22
    await store.atomically_publish(str(src), key)
    art = await arts.create_available(
        Artifact(
            artifact_id=ArtifactId("A" * 22),
            job_id=job.job_id,
            storage_key=key,
            display_name="影片 clip.mp4",
            media_type=MediaType.VIDEO_MP4,
            byte_size=len(payload),
            ready_at=now,
            expires_at=now + timedelta(hours=12),
        )
    )

    signer = HmacTokenSigner(b"S" * 32, public_base_url="https://downloads.example.invalid")
    link = issue_download_link(
        signer,
        artifact_id=art.artifact_id.value,
        display_name=art.display_name,
        token_version=art.token_version,
        now=now,
        link_lifetime_seconds=3600,
        artifact_expires_at=art.expires_at,
    )
    parsed = urlparse(link.url)
    qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    display_path = parsed.path.rstrip("/").split("/")[-1]

    leases = InMemoryArtifactLeaseRegistry()
    access = ArtifactAccessCoordinator(arts, leases)
    downloads = DownloadService(
        artifacts=arts,
        store=store,
        signer=signer,
        access=access,
        clock=lambda: now,
    )
    health = HealthController(
        readiness=ReadinessState(
            configuration=True,
            database=True,
            migrations=True,
            recovery=True,
            http=True,
            dispatcher=True,
            platforms=True,
            egress=True,
            storage=True,
        )
    )
    app = create_app(downloads=downloads, health=health, expose_health=True)
    runner, base = await start_test_server(app)
    try:
        async with ClientSession() as session:
            # Full GET
            url = f"{base}/v1/artifacts/{art.artifact_id.value}/{display_path}"
            async with session.get(url, params=qs) as resp:
                assert resp.status == 200
                body = await resp.read()
                assert body == payload
                assert resp.headers["Accept-Ranges"] == "bytes"
                assert resp.headers["Cache-Control"] == "private, no-store"

            # Range GET
            async with session.get(url, params=qs, headers={"Range": "bytes=0-9"}) as resp:
                assert resp.status == 206
                body = await resp.read()
                assert body == payload[:10]
                assert resp.headers["Content-Range"] == "bytes 0-9/256"

            # HEAD
            async with session.head(url, params=qs) as resp:
                assert resp.status == 200
                assert await resp.read() == b""

            # Bad signature → generic 404 (mutate multiple chars; avoid cache reuse)
            bad = dict(qs)
            sig = bad["sig"]
            # Invert several base64url characters so HMAC cannot coincidentally match.
            flipped = "".join("A" if ch != "A" else "B" for ch in sig)
            bad["sig"] = flipped
            async with session.get(url, params=bad) as resp:
                assert resp.status == 404
                assert b"not_found" in await resp.read()

            # Health
            async with session.get(f"{base}/readyz") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["ready"] is True
    finally:
        await runner.cleanup()
        await conn.close()
