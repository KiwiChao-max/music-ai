"""Tests for the task artifact download helpers in `app.api.tasks`.

The heavy download paths (local FileResponse, S3 streaming) are covered
by the E2E suite. This module tests the pure byte-range parser and the
S3 chunk iterator that the S3 streaming path delegates to.
"""

from __future__ import annotations

import pytest

from app.api.tasks import _iter_s3_chunks, _parse_byte_range


# ---- _parse_byte_range ---------------------------------------------------
@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        # Absent / malformed / unsupported headers -> serve the full 200.
        (None, 100, None),
        ("", 100, None),
        ("items=0-10", 100, None),
        ("bytes=abc-10", 100, None),
        ("bytes=0-10,20-30", 100, None),  # multi-range not supported
        ("bytes=100-", 100, None),  # start beyond EOF
        ("bytes=5-3", 100, None),  # inverted bounds
        # Valid single ranges.
        ("bytes=0-99", 100, (0, 99)),
        ("bytes=10-19", 100, (10, 19)),
        ("bytes=0-0", 100, (0, 0)),
        ("bytes=50-", 100, (50, 99)),  # open-ended
        ("bytes=-10", 100, (90, 99)),  # suffix range: last N bytes
        ("bytes=0-999", 100, (0, 99)),  # end clamped to EOF
    ],
)
def test_parse_byte_range(header: str | None, size: int, expected) -> None:
    assert _parse_byte_range(header, size) == expected


def test_parse_byte_range_rejects_empty_file() -> None:
    assert _parse_byte_range("bytes=0-", 0) is None


# ---- _iter_s3_chunks ------------------------------------------------------
def test_iter_s3_chunks_yields_chunks_and_closes_body() -> None:
    class FakeBody:
        def __init__(self) -> None:
            self.closed = False

        def iter_chunks(self, *, chunk_size: int):
            assert chunk_size > 0
            yield b"ab"
            yield b"cd"

        def close(self) -> None:
            self.closed = True

    body = FakeBody()
    assert list(_iter_s3_chunks(body)) == [b"ab", b"cd"]
    assert body.closed
