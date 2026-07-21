"""Safe Content-Disposition and download response headers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def content_disposition(display_name: str) -> str:
    """Build injection-safe Content-Disposition with ASCII fallback + UTF-8 filename*."""
    if _CTRL.search(display_name):
        raise ValueError("control characters in display name")
    # ASCII fallback: strip non-ascii and quotes.
    fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in {'"', "\\"} else "_" for ch in display_name
    )
    if not fallback:
        fallback = "download"
    if len(fallback) > 150:
        fallback = fallback[:150]
    encoded = quote(display_name, safe="")
    if len(encoded) > 500:
        raise ValueError("display name too long for header")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


@dataclass(frozen=True, slots=True)
class DownloadHeaders:
    status: int
    headers: dict[str, str]


def build_download_headers(
    *,
    media_type: str,
    file_size: int,
    display_name: str,
    byte_range: tuple[int, int] | None,
    unsatisfiable: bool = False,
) -> DownloadHeaders:
    base = {
        "Accept-Ranges": "bytes",
        "Content-Type": media_type,
        "Content-Disposition": content_disposition(display_name),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
    }
    if unsatisfiable:
        base["Content-Range"] = f"bytes */{file_size}"
        base["Content-Length"] = "0"
        return DownloadHeaders(status=416, headers=base)
    if byte_range is None:
        base["Content-Length"] = str(file_size)
        return DownloadHeaders(status=200, headers=base)
    start, end = byte_range
    length = end - start + 1
    base["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    base["Content-Length"] = str(length)
    return DownloadHeaders(status=206, headers=base)


GENERIC_NOT_FOUND_BODY = b'{"error":"not_found"}'
GENERIC_NOT_FOUND_HEADERS = {
    "Content-Type": "application/json",
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
}
