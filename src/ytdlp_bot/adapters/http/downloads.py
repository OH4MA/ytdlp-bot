"""Authenticated artifact download handlers (GET/HEAD + range)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import unquote

from ytdlp_bot.adapters.http.headers import (
    GENERIC_NOT_FOUND_BODY,
    GENERIC_NOT_FOUND_HEADERS,
    build_download_headers,
)
from ytdlp_bot.adapters.http.ranges import RangeError, parse_single_range
from ytdlp_bot.adapters.security.signed_tokens import HmacTokenSigner, TokenValidationError
from ytdlp_bot.adapters.storage.local_store import LocalArtifactStore, StorageError
from ytdlp_bot.application.artifact_access import ArtifactAccessCoordinator
from ytdlp_bot.domain.enums import ArtifactAccessState
from ytdlp_bot.domain.identity import ArtifactId
from ytdlp_bot.ports.repositories import ArtifactRepository


@dataclass
class DownloadService:
    artifacts: ArtifactRepository
    store: LocalArtifactStore
    signer: HmacTokenSigner
    access: ArtifactAccessCoordinator
    clock: Callable[[], datetime]
    stream_chunk_bytes: int = 65536
    max_concurrent_streams: int = 8

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.max_concurrent_streams)

    async def handle(
        self,
        *,
        method: str,
        artifact_id: str,
        display_name_path: str,
        query: dict[str, str],
        range_header: str | None,
        holder_id: str,
    ) -> tuple[int, dict[str, str], Any]:
        """Return status, headers, and body provider (bytes | async iterator | None)."""
        if method not in {"GET", "HEAD"}:
            return 405, {"Allow": "GET, HEAD"}, b""

        try:
            art = await self.artifacts.get(ArtifactId(artifact_id))
            if art is None:
                raise TokenValidationError("missing")
            display_name = unquote(display_name_path)
            self.signer.verify_request(
                artifact_id=artifact_id,
                display_name_path=display_name_path,
                query_params=query,
                now=self.clock(),
                artifact_token_version=art.token_version,
                artifact_expires_at=art.expires_at,
                access_available=art.access_state is ArtifactAccessState.AVAILABLE,
            )
            # Display name in signature is the decoded name; path must match canonical form.
            if art.display_name != display_name:
                raise TokenValidationError("display mismatch")

            leased = await self.access.try_begin_stream(
                ArtifactId(artifact_id),
                holder_id=holder_id,
                expected_token_version=art.token_version,
                expected_display_name=art.display_name,
            )
            if not leased:
                raise TokenValidationError("lease denied")
        except (TokenValidationError, ValueError, StorageError):
            return 404, dict(GENERIC_NOT_FOUND_HEADERS), GENERIC_NOT_FOUND_BODY

        try:
            st = await self.store.stat(art.storage_key)
            if st.is_symlink:
                raise StorageError("symlink")
            file_size = st.size
            try:
                selected = parse_single_range(range_header, file_size=file_size)
            except RangeError as exc:
                if exc.unsatisfiable:
                    meta = build_download_headers(
                        media_type=art.media_type.value,
                        file_size=file_size,
                        display_name=art.display_name,
                        byte_range=None,
                        unsatisfiable=True,
                    )
                    await self.access.end_stream(ArtifactId(artifact_id), holder_id=holder_id)
                    return meta.status, meta.headers, b""
                await self.access.end_stream(ArtifactId(artifact_id), holder_id=holder_id)
                return 404, dict(GENERIC_NOT_FOUND_HEADERS), GENERIC_NOT_FOUND_BODY

            byte_range = None if selected is None else (selected.start, selected.end)
            meta = build_download_headers(
                media_type=art.media_type.value,
                file_size=file_size,
                display_name=art.display_name,
                byte_range=byte_range,
            )
            if method == "HEAD":
                await self.access.end_stream(ArtifactId(artifact_id), holder_id=holder_id)
                return meta.status, meta.headers, b""

            await self._semaphore.acquire()
            body = self._stream_body(
                storage_key=art.storage_key,
                start=selected.start if selected else 0,
                length=selected.length if selected else file_size,
                artifact_id=ArtifactId(artifact_id),
                holder_id=holder_id,
            )
            return meta.status, meta.headers, body
        except Exception:
            await self.access.end_stream(ArtifactId(artifact_id), holder_id=holder_id)
            return 404, dict(GENERIC_NOT_FOUND_HEADERS), GENERIC_NOT_FOUND_BODY

    def _stream_body(
        self,
        *,
        storage_key: str,
        start: int,
        length: int,
        artifact_id: ArtifactId,
        holder_id: str,
    ) -> Any:
        service = self

        async def _gen() -> Any:
            fd = -1
            try:
                fd, _size = await service.store.open_file(storage_key)
                os.lseek(fd, start, os.SEEK_SET)
                remaining = length
                while remaining > 0:
                    chunk = os.read(fd, min(service.stream_chunk_bytes, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
            finally:
                if fd >= 0:
                    os.close(fd)
                await service.access.end_stream(artifact_id, holder_id=holder_id)
                service._semaphore.release()

        return _gen()
