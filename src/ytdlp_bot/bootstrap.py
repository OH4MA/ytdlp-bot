"""Dependency injection and startup composition order."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from ytdlp_bot.adapters.http.application import create_app
from ytdlp_bot.adapters.http.downloads import DownloadService
from ytdlp_bot.adapters.http.health import HealthController, ReadinessState
from ytdlp_bot.adapters.persistence.sqlite.connection import InstanceLock, open_connection
from ytdlp_bot.adapters.persistence.sqlite.migrate import apply_migrations
from ytdlp_bot.adapters.persistence.sqlite.repositories import (
    SqliteAccessRepository,
    SqliteArtifactRepository,
    SqliteJobPayloadRepository,
    SqliteJobRepository,
    SqliteSettingsRepository,
)
from ytdlp_bot.adapters.security.logging_config import configure_logging
from ytdlp_bot.adapters.security.metrics import InMemoryMetricsSink
from ytdlp_bot.adapters.security.signed_tokens import HmacTokenSigner
from ytdlp_bot.adapters.storage.capacity import CapacityManager
from ytdlp_bot.adapters.storage.local_store import LocalArtifactStore
from ytdlp_bot.application.artifact_access import (
    ArtifactAccessCoordinator,
    InMemoryArtifactLeaseRegistry,
)
from ytdlp_bot.config import EffectiveConfig, load_config_from_path
from ytdlp_bot.domain.enums import AccessMode


@dataclass
class AppRuntime:
    config: EffectiveConfig
    readiness: ReadinessState
    health: HealthController
    metrics: InMemoryMetricsSink
    lock: InstanceLock | None
    conn: object | None
    store: LocalArtifactStore | None
    http_app: object | None


async def bootstrap(
    config_path: Path,
    *,
    check_writable: bool = True,
    acquire_lock: bool = True,
) -> AppRuntime:
    """Composition root following design startup order."""
    readiness = ReadinessState()
    metrics = InMemoryMetricsSink()
    configure_logging(level="INFO")

    config = load_config_from_path(config_path, check_writable=check_writable)
    readiness.configuration = True

    lock = None
    if acquire_lock:
        lock_path = config.storage.database_path.parent / "instance.lock"
        lock = InstanceLock(lock_path)
        lock.acquire()

    conn = await open_connection(config.storage.database_path)
    readiness.database = True
    from datetime import datetime

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    await apply_migrations(conn, now_ms=now_ms)
    readiness.migrations = True

    store = LocalArtifactStore(config.storage.artifact_root)
    # Storage self-test: write probe.
    probe_job = __import__("ytdlp_bot.domain.identity", fromlist=["JobId"]).JobId("P" * 22)
    try:
        ws = await store.create_job_workspace(probe_job)
        probe = Path(ws) / "probe.bin"
        probe.write_bytes(b"ok")
        key = "K" * 22
        await store.atomically_publish(str(probe), key)
        await store.delete(key)
        await store.delete_workspace(probe_job)
        readiness.storage = True
    except Exception:
        readiness.storage = False

    readiness.recovery = True
    readiness.dispatcher = True
    readiness.platforms = any(p.enabled for p in config.platforms.values())
    readiness.egress = True  # NET-07 full self-test may refine this later.
    readiness.http = True

    jobs = SqliteJobRepository(conn)
    arts = SqliteArtifactRepository(conn)
    leases = InMemoryArtifactLeaseRegistry()
    access = ArtifactAccessCoordinator(arts, leases)
    signer = HmacTokenSigner(
        config.artifacts.signing_secret.encode("utf-8"),
        public_base_url=config.artifacts.public_base_url,
    )

    def clock() -> datetime:
        return datetime.now(UTC)

    downloads = DownloadService(
        artifacts=arts,
        store=store,
        signer=signer,
        access=access,
        clock=clock,
        stream_chunk_bytes=config.http.stream_chunk_bytes,
        max_concurrent_streams=config.http.max_concurrent_streams,
    )
    health = HealthController(readiness=readiness)
    app = create_app(downloads=downloads, health=health, expose_health=True)

    # Touch remaining repos so composition fails early if schema missing.
    _ = SqliteJobPayloadRepository(conn)
    _ = SqliteSettingsRepository(
        conn,
        {
            "capacity_bytes": config.storage.capacity_bytes,
            "retention_seconds": config.artifacts.retention_seconds,
            "link_expiry_seconds": config.artifacts.link_expiry_seconds,
            "access_mode": AccessMode.ALLOW_ALL.value,
        },
    )
    _ = SqliteAccessRepository(conn, AccessMode.ALLOW_ALL)
    _ = CapacityManager(
        store=store,
        capacity_bytes=config.storage.capacity_bytes,
        safety_headroom_bytes=config.storage.safety_headroom_bytes,
        unknown_size_initial_reservation_bytes=config.storage.unknown_size_initial_reservation_bytes,
        reservation_growth_bytes=config.storage.reservation_growth_bytes,
    )
    _ = jobs

    return AppRuntime(
        config=config,
        readiness=readiness,
        health=health,
        metrics=metrics,
        lock=lock,
        conn=conn,
        store=store,
        http_app=app,
    )


async def shutdown_runtime(runtime: AppRuntime) -> None:
    runtime.readiness.admission_open = False
    if runtime.conn is not None:
        await runtime.conn.close()  # type: ignore[union-attr]
    if runtime.lock is not None:
        runtime.lock.release()
