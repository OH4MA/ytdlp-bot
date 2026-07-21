"""Temporary artifact store for tests."""

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from ytdlp_bot.domain.identity import JobId
from ytdlp_bot.ports.storage import FileStat, FilesystemUsage


class TemporaryArtifactStore:
    """Filesystem-backed store under a temporary root (no symlink following)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.workspaces = root / "workspaces"
        self.artifacts = root / "artifacts"
        self.workspaces.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)

    def reset(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
        self.workspaces.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)

    def _workspace_dir(self, job_id: JobId) -> Path:
        return self.workspaces / job_id.value

    def _artifact_path(self, storage_key: str) -> Path:
        # Containment: reject path separators in key.
        if "/" in storage_key or "\\" in storage_key or ".." in storage_key:
            raise ValueError("invalid storage_key")
        return self.artifacts / storage_key

    async def create_job_workspace(self, job_id: JobId) -> str:
        path = self._workspace_dir(job_id)
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        return str(path)

    async def resolve_workspace_path(self, job_id: JobId, generated_relative_name: str) -> str:
        base = self._workspace_dir(job_id).resolve()
        if ".." in generated_relative_name or generated_relative_name.startswith("/"):
            raise ValueError("path escape")
        candidate = (base / generated_relative_name).resolve()
        if not str(candidate).startswith(str(base)):
            raise ValueError("path escape")
        return str(candidate)

    async def measure_workspace(self, job_id: JobId) -> int:
        total = 0
        base = self._workspace_dir(job_id)
        if not base.exists():
            return 0
        for dirpath, _, filenames in os.walk(base):
            for name in filenames:
                total += (Path(dirpath) / name).stat().st_size
        return total

    async def atomically_publish(self, source_path: str, storage_key: str) -> None:
        dest = self._artifact_path(storage_key)
        os.replace(source_path, dest)

    async def open_stream(self, storage_key: str) -> AsyncIterator[bytes]:
        path = self._artifact_path(storage_key)
        if path.is_symlink():
            raise ValueError("symlink refused")

        async def _gen() -> AsyncIterator[bytes]:
            with path.open("rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    yield chunk

        return _gen()

    async def stat(self, storage_key: str) -> FileStat:
        path = self._artifact_path(storage_key)
        st = path.lstat()
        return FileStat(size=st.st_size, is_symlink=path.is_symlink())

    async def delete(self, storage_key: str) -> None:
        path = self._artifact_path(storage_key)
        if path.exists() or path.is_symlink():
            path.unlink()

    async def delete_workspace(self, job_id: JobId) -> None:
        path = self._workspace_dir(job_id)
        if path.exists():
            shutil.rmtree(path)

    async def scan_orphans(self) -> Sequence[str]:
        return [p.name for p in self.artifacts.iterdir()]

    async def filesystem_usage(self) -> FilesystemUsage:
        used = 0
        for dirpath, _, filenames in os.walk(self.root):
            for name in filenames:
                used += (Path(dirpath) / name).stat().st_size
        return FilesystemUsage(used_bytes=used, free_bytes=10**12, total_bytes=10**12 + used)
