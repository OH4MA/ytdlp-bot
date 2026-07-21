"""Local filesystem artifact store with containment and no symlink following."""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from ytdlp_bot.domain.identity import JobId
from ytdlp_bot.ports.storage import FileStat, FilesystemUsage


class StorageError(Exception):
    """Safe storage failure."""


class LocalArtifactStore:
    """ArtifactStore implementation under trusted roots."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.workspaces = (self.root / "workspaces").resolve()
        self.artifacts = (self.root / "artifacts").resolve()
        self.workspaces.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.artifacts.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        os.chmod(self.workspaces, 0o700)
        os.chmod(self.artifacts, 0o700)

    def _workspace_dir(self, job_id: JobId) -> Path:
        path = (self.workspaces / job_id.value).resolve()
        if self.workspaces not in path.parents and path != self.workspaces:
            raise StorageError("workspace path escape")
        return path

    def _artifact_path(self, storage_key: str) -> Path:
        if "/" in storage_key or "\\" in storage_key or ".." in storage_key or not storage_key:
            raise StorageError("invalid storage_key")
        path = (self.artifacts / storage_key).resolve()
        if self.artifacts not in path.parents and path != self.artifacts:
            raise StorageError("artifact path escape")
        return path

    def resolve_artifact_path(self, storage_key: str) -> str:
        """Return absolute regular-file path for an opaque storage key."""
        path = self._artifact_path(storage_key)
        if path.is_symlink() or not path.is_file():
            raise StorageError("artifact missing or not a regular file")
        return str(path)

    async def create_job_workspace(self, job_id: JobId) -> str:
        path = self._workspace_dir(job_id)
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
        os.chmod(path, 0o700)
        return str(path)

    async def resolve_workspace_path(self, job_id: JobId, generated_relative_name: str) -> str:
        if (
            not generated_relative_name
            or generated_relative_name.startswith("/")
            or ".." in Path(generated_relative_name).parts
        ):
            raise StorageError("invalid relative name")
        base = self._workspace_dir(job_id)
        candidate = (base / generated_relative_name).resolve()
        if base not in candidate.parents and candidate != base:
            raise StorageError("path escape")
        return str(candidate)

    async def measure_workspace(self, job_id: JobId) -> int:
        base = self._workspace_dir(job_id)
        if not base.exists():
            return 0
        total = 0
        for dirpath, _, filenames in os.walk(base, followlinks=False):
            for name in filenames:
                fp = Path(dirpath) / name
                try:
                    st = fp.lstat()
                except OSError:
                    continue
                if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                    continue
                total += st.st_size
        return total

    async def atomically_publish(self, source_path: str, storage_key: str) -> None:
        src = Path(source_path)
        if not src.is_file() or src.is_symlink():
            raise StorageError("source must be a regular file")
        dest = self._artifact_path(storage_key)
        # Same-filesystem atomic replace.
        os.replace(src, dest)
        # Best-effort fsync.
        try:
            with open(dest, "rb") as fh:
                os.fsync(fh.fileno())
            dir_fd = os.open(self.artifacts, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
        os.chmod(dest, 0o600)

    async def open_stream(self, storage_key: str) -> AsyncIterator[bytes]:
        path = self._artifact_path(storage_key)
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise StorageError("refusing non-regular file")

        async def _gen() -> AsyncIterator[bytes]:
            # Re-open without following symlinks where possible.
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                with os.fdopen(fd, "rb") as fh:
                    while True:
                        chunk = fh.read(65536)
                        if not chunk:
                            break
                        yield chunk
            except Exception:
                # fd closed by fdopen on success; if fdopen fails, close.
                with contextlib_suppress():
                    os.close(fd)
                raise

        return _gen()

    async def open_file(self, storage_key: str) -> tuple[int, int]:
        """Return (fd, size) for range seeks. Caller must close fd."""
        path = self._artifact_path(storage_key)
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise StorageError("refusing non-regular file")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        return fd, st.st_size

    async def stat(self, storage_key: str) -> FileStat:
        path = self._artifact_path(storage_key)
        st = path.lstat()
        return FileStat(size=st.st_size, is_symlink=stat.S_ISLNK(st.st_mode))

    async def exists(self, storage_key: str) -> bool:
        path = self._artifact_path(storage_key)
        return path.is_file() and not path.is_symlink()

    async def delete(self, storage_key: str) -> None:
        path = self._artifact_path(storage_key)
        if path.exists() or path.is_symlink():
            path.unlink()

    async def delete_workspace(self, job_id: JobId) -> None:
        path = self._workspace_dir(job_id)
        if path.exists():
            shutil.rmtree(path)

    async def scan_orphans(self) -> Sequence[str]:
        return sorted(p.name for p in self.artifacts.iterdir() if p.is_file())

    async def filesystem_usage(self) -> FilesystemUsage:
        usage = shutil.disk_usage(self.root)
        used = 0
        for base in (self.workspaces, self.artifacts):
            for dirpath, _, filenames in os.walk(base, followlinks=False):
                for name in filenames:
                    try:
                        used += (Path(dirpath) / name).lstat().st_size
                    except OSError:
                        continue
        return FilesystemUsage(
            used_bytes=used,
            free_bytes=usage.free,
            total_bytes=usage.total,
        )


class contextlib_suppress:
    def __enter__(self) -> contextlib_suppress:
        return self

    def __exit__(self, *exc: object) -> bool:
        return True
