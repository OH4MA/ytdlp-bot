"""CAP storage containment and no-symlink rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from ytdlp_bot.adapters.storage.local_store import LocalArtifactStore, StorageError
from ytdlp_bot.domain.identity import JobId


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workspace_containment(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "root")
    jid = JobId("J" * 22)
    ws = await store.create_job_workspace(jid)
    assert Path(ws).exists()
    with pytest.raises(StorageError):
        await store.resolve_workspace_path(jid, "../escape")
    with pytest.raises(StorageError):
        await store.resolve_workspace_path(jid, "/abs")
    p = await store.resolve_workspace_path(jid, "ok.bin")
    Path(p).write_bytes(b"x")
    key = "S" * 22
    await store.atomically_publish(p, key)
    st = await store.stat(key)
    assert st.size == 1
    # symlink open refused
    art = tmp_path / "root" / "artifacts" / key
    art.unlink()
    art.symlink_to("/etc/hosts")
    with pytest.raises(StorageError):
        await store.open_file(key)
