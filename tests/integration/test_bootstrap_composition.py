"""Bootstrap wires command service, readiness, and HTTP app."""

from __future__ import annotations

from pathlib import Path

import pytest

from ytdlp_bot.bootstrap import bootstrap, shutdown_runtime
from ytdlp_bot.config import minimal_valid_toml


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bootstrap_composes_services(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "cfg"
    data = tmp_path / "data"
    art = data / "artifacts"
    db = data / "state" / "service.sqlite3"
    cfg_dir.mkdir()
    data.mkdir()
    (data / "state").mkdir()
    secrets = {
        "env:TG_TOKEN": "telegram-token-canary-NOT-FOR-LOGS",
        "env:SIGNING_SECRET": "S" * 32,
    }

    def reader(ref: str) -> str:
        return secrets[ref]

    text = minimal_valid_toml(artifact_root=str(art), database_path=str(db))
    # Disable live platforms for unit composition.
    text = text.replace("enabled = true", "enabled = false", 1)
    text = text.replace(
        "[platforms.discord]\nenabled = false",
        "[platforms.discord]\nenabled = false\n",
    )
    # Need at least one platform enabled for config validation.
    text = text.replace("enabled = false", "enabled = true", 1)
    cfg_path = cfg_dir / "config.toml"
    cfg_path.write_text(text)

    # Patch secret reader via load path: write env
    import os

    os.environ["TG_TOKEN"] = secrets["env:TG_TOKEN"]
    os.environ["SIGNING_SECRET"] = secrets["env:SIGNING_SECRET"]

    runtime = await bootstrap(
        cfg_path,
        check_writable=True,
        acquire_lock=True,
        fixture_workers=True,
        start_background=False,
    )
    try:
        assert runtime.command_service is not None
        assert runtime.http_app is not None
        assert runtime.readiness.configuration is True
        assert runtime.readiness.database is True
        assert runtime.readiness.migrations is True
        assert runtime.readiness.storage is True
        # egress requires dns + loopback block
        assert runtime.readiness.egress is True
    finally:
        await shutdown_runtime(runtime)
