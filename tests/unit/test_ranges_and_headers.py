"""HTTP-01..03 pure range and header tests."""

from __future__ import annotations

import pytest

from ytdlp_bot.adapters.http.headers import build_download_headers, content_disposition
from ytdlp_bot.adapters.http.ranges import RangeError, parse_single_range


@pytest.mark.unit
@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        (None, 100, None),
        ("bytes=0-0", 100, (0, 0)),
        ("bytes=0-99", 100, (0, 99)),
        ("bytes=10-", 100, (10, 99)),
        ("bytes=-10", 100, (90, 99)),
        ("bytes=-200", 100, (0, 99)),
        ("bytes=50-60", 100, (50, 60)),
    ],
)
def test_parse_ranges(header: str | None, size: int, expected: tuple[int, int] | None) -> None:
    result = parse_single_range(header, file_size=size)
    if expected is None:
        assert result is None
    else:
        assert result is not None
        assert (result.start, result.end) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "header",
    [
        "bits=0-1",
        "bytes=0-1,2-3",
        "bytes=5-1",
        "bytes=abc-1",
        "bytes=",
    ],
)
def test_invalid_ranges(header: str) -> None:
    with pytest.raises(RangeError):
        parse_single_range(header, file_size=100)


@pytest.mark.unit
def test_unsatisfiable() -> None:
    with pytest.raises(RangeError) as exc:
        parse_single_range("bytes=100-100", file_size=100)
    assert exc.value.unsatisfiable is True


@pytest.mark.unit
def test_content_disposition_safe() -> None:
    h = content_disposition('clip "x".mp4')
    assert "\r" not in h and "\n" not in h
    assert "filename=" in h
    assert "filename*=" in h
    with pytest.raises(ValueError):
        content_disposition("bad\nname")


@pytest.mark.unit
def test_response_headers_snapshot() -> None:
    full = build_download_headers(
        media_type="video/mp4",
        file_size=100,
        display_name="a.mp4",
        byte_range=None,
    )
    assert full.status == 200
    assert full.headers["Content-Length"] == "100"
    assert full.headers["Cache-Control"] == "private, no-store"
    partial = build_download_headers(
        media_type="video/mp4",
        file_size=100,
        display_name="a.mp4",
        byte_range=(0, 9),
    )
    assert partial.status == 206
    assert partial.headers["Content-Range"] == "bytes 0-9/100"
    assert partial.headers["Content-Length"] == "10"
