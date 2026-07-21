"""Artifact filesystem storage port."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from ytdlp_bot.domain.identity import JobId


@dataclass(frozen=True, slots=True)
class FileStat:
    size: int
    is_symlink: bool = False


@dataclass(frozen=True, slots=True)
class FilesystemUsage:
    used_bytes: int
    free_bytes: int
    total_bytes: int


class ArtifactStore(Protocol):
    async def create_job_workspace(self, job_id: JobId) -> str: ...

    async def resolve_workspace_path(self, job_id: JobId, generated_relative_name: str) -> str: ...

    async def measure_workspace(self, job_id: JobId) -> int: ...

    async def atomically_publish(self, source_path: str, storage_key: str) -> None: ...

    async def open_stream(self, storage_key: str) -> AsyncIterator[bytes]: ...

    async def stat(self, storage_key: str) -> FileStat: ...

    async def delete(self, storage_key: str) -> None: ...

    async def delete_workspace(self, job_id: JobId) -> None: ...

    async def scan_orphans(self) -> Sequence[str]: ...

    async def filesystem_usage(self) -> FilesystemUsage: ...
