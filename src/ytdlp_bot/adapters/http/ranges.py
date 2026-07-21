"""Pure single-range HTTP Range header parsing."""

from __future__ import annotations

from dataclasses import dataclass


class RangeError(Exception):
    """Invalid or unsatisfiable range."""

    def __init__(self, message: str, *, unsatisfiable: bool = False) -> None:
        super().__init__(message)
        self.unsatisfiable = unsatisfiable


@dataclass(frozen=True, slots=True)
class ByteRange:
    start: int
    end: int  # inclusive

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_single_range(header: str | None, *, file_size: int) -> ByteRange | None:
    """Parse a single bytes range. None means full content.

    Supports: bytes=start-end, bytes=start-, bytes=-suffix.
    """
    if header is None or header.strip() == "":
        return None
    text = header.strip()
    if not text.lower().startswith("bytes="):
        raise RangeError("unsupported unit")
    spec = text[6:]
    if "," in spec:
        raise RangeError("multiple ranges unsupported")
    if "-" not in spec:
        raise RangeError("malformed range")
    left, right = spec.split("-", 1)
    if file_size <= 0:
        raise RangeError("empty file", unsatisfiable=True)

    if left == "" and right == "":
        raise RangeError("malformed range")

    if left == "":
        # suffix: last N bytes
        try:
            suffix = int(right)
        except ValueError as exc:
            raise RangeError("malformed range") from exc
        if suffix <= 0:
            raise RangeError("malformed range")
        if suffix >= file_size:
            return ByteRange(0, file_size - 1)
        return ByteRange(file_size - suffix, file_size - 1)

    try:
        start = int(left)
    except ValueError as exc:
        raise RangeError("malformed range") from exc
    if start < 0:
        raise RangeError("malformed range")
    if start >= file_size:
        raise RangeError("unsatisfiable", unsatisfiable=True)

    if right == "":
        return ByteRange(start, file_size - 1)

    try:
        end = int(right)
    except ValueError as exc:
        raise RangeError("malformed range") from exc
    if end < start:
        raise RangeError("reversed range")
    end = min(end, file_size - 1)
    return ByteRange(start, end)
