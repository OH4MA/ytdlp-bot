"""Playlist ZIP archive construction."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ytdlp_bot.domain.jobs import archive_name_padding

_TRUSTED_EXTENSIONS = frozenset({"mp4", "mp3", "zip"})
_MAX_TITLE_UTF8 = 180


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    index: int
    source_path: Path
    sanitized_title: str
    extension: str


def sanitize_filename_title(title: str) -> str:
    """Unicode-safe title for ZIP member names (no path separators)."""
    # Normalize separators and control characters.
    cleaned = title.replace("\x00", "").replace("/", "_").replace("\\", "_")
    cleaned = re.sub(r"[\r\n\t]+", " ", cleaned)
    cleaned = "".join(ch if ch.isprintable() else "_" for ch in cleaned)
    cleaned = re.sub(r"[<>:\"|?*]+", "_", cleaned)
    cleaned = cleaned.strip(" .")
    if not cleaned:
        cleaned = "entry"
    # Bound by UTF-8 bytes then characters.
    encoded = cleaned.encode("utf-8")
    if len(encoded) > _MAX_TITLE_UTF8:
        cleaned = encoded[:_MAX_TITLE_UTF8].decode("utf-8", errors="ignore").rstrip()
    return cleaned[:80] or "entry"


def trusted_extension(extension: str) -> str:
    ext = extension.lstrip(".").lower()
    if ext not in _TRUSTED_EXTENSIONS:
        return "bin"
    return ext


def build_archive_member_name(index: int, *, total: int | None, title: str, extension: str) -> str:
    width = archive_name_padding(total)
    safe = sanitize_filename_title(title)
    ext = trusted_extension(extension)
    return f"{index:0{width}d}_{safe}.{ext}"


def write_playlist_zip(
    output_path: Path,
    entries: list[ArchiveEntry],
    *,
    failures: list[tuple[int, str, str]],
    total: int | None,
) -> Path:
    """Write successful entries + optional UTF-8 failure manifest."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        used: set[str] = set()
        for entry in entries:
            name = build_archive_member_name(
                entry.index,
                total=total,
                title=entry.sanitized_title,
                extension=entry.extension,
            )
            base, ext = name.rsplit(".", 1)
            candidate = name
            n = 1
            while candidate in used:
                candidate = f"{base}_{n}.{ext}"
                n += 1
            used.add(candidate)
            zf.write(entry.source_path, arcname=candidate)
        if failures:
            lines = ["index\tsource_id\treason"]
            for idx, source_id, reason in failures:
                lines.append(f"{idx}\t{source_id}\t{reason}")
            zf.writestr("FAILURES.txt", "\n".join(lines) + "\n")
    return output_path
