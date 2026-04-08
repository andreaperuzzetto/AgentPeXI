"""Test unit per tools/file_store.py — mock aiobotocore."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.file_store import (
    FileUploadError,
    download_bytes,
    download_file,
    file_exists,
    get_presigned_url,
    list_files,
    upload_bytes,
    upload_file,
)


# ---------------------------------------------------------------------------
# Helper: mock del client aiobotocore
# ---------------------------------------------------------------------------

def _make_s3_client(raise_at: str | None = None):
    """
    Restituisce un mock del context manager aiobotocore client.
    Se raise_at è il nome di un metodo, quel metodo solleva Exception.
    """
    client = AsyncMock()

    if raise_at == "put_object":
        client.put_object.side_effect = Exception("MinIO error")
    if raise_at == "get_object":
        client.get_object.side_effect = Exception("NoSuchKey")
    if raise_at == "head_object":
        client.head_object.side_effect = Exception("NoSuchKey")

    # Simula response body per get_object
    body_mock = AsyncMock()
    body_mock.read = AsyncMock(return_value=b"%PDF-1.4 test content")
    body_mock.__aenter__ = AsyncMock(return_value=body_mock)
    body_mock.__aexit__ = AsyncMock(return_value=False)
    client.get_object.return_value = {"Body": body_mock}

    # Presigned URL
    client.generate_presigned_url = AsyncMock(
        return_value="https://minio.test/bucket/key?X-Amz=sig"
    )

    # list_objects_v2 paginator
    paginator = MagicMock()
    page = {"Contents": [{"Key": "clients/test/file.pdf"}]}

    async def _paginate(**_):
        yield page

    paginator.paginate = _paginate
    client.get_paginator = MagicMock(return_value=paginator)

    # Context manager
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client


# ---------------------------------------------------------------------------
# upload_file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_file_happy_path(tmp_path: Path):
    local = tmp_path / "test.pdf"
    local.write_bytes(b"fake pdf content")
    cm, client = _make_s3_client()

    with patch("tools.file_store._get_client", return_value=cm):
        result = await upload_file(local, "clients/test/proposals/v1.pdf")

    assert result == "clients/test/proposals/v1.pdf"
    client.put_object.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_file_raises_on_error(tmp_path: Path):
    local = tmp_path / "test.pdf"
    local.write_bytes(b"content")
    cm, _ = _make_s3_client(raise_at="put_object")

    with patch("tools.file_store._get_client", return_value=cm):
        with pytest.raises(FileUploadError):
            await upload_file(local, "clients/test/proposals/v1.pdf")


# ---------------------------------------------------------------------------
# upload_bytes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_bytes_happy_path():
    cm, client = _make_s3_client()

    with patch("tools.file_store._get_client", return_value=cm):
        result = await upload_bytes(b"binary content", "clients/test/artifact.png", "image/png")

    assert result == "clients/test/artifact.png"
    client.put_object.assert_awaited_once()


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_file_happy_path(tmp_path: Path):
    cm, _ = _make_s3_client()
    out = tmp_path / "output.pdf"

    with patch("tools.file_store._get_client", return_value=cm):
        result = await download_file("clients/test/proposals/v1.pdf", out)

    assert result == out
    assert out.read_bytes() == b"%PDF-1.4 test content"


@pytest.mark.asyncio
async def test_download_file_not_found(tmp_path: Path):
    cm, _ = _make_s3_client(raise_at="get_object")
    out = tmp_path / "output.pdf"

    with patch("tools.file_store._get_client", return_value=cm):
        with pytest.raises(Exception):  # FileNotFoundError o FileUploadError
            await download_file("clients/test/nonexistent.pdf", out)


# ---------------------------------------------------------------------------
# download_bytes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_bytes_happy_path():
    cm, _ = _make_s3_client()

    with patch("tools.file_store._get_client", return_value=cm):
        data = await download_bytes("clients/test/proposals/v1.pdf")

    assert data == b"%PDF-1.4 test content"


# ---------------------------------------------------------------------------
# get_presigned_url
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_presigned_url():
    cm, _ = _make_s3_client()

    with patch("tools.file_store._get_client", return_value=cm):
        url = await get_presigned_url("clients/test/proposal.pdf", expires_in_seconds=3600)

    assert url.startswith("https://")
    assert "minio.test" in url


# ---------------------------------------------------------------------------
# file_exists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_file_exists_true():
    cm, _ = _make_s3_client()

    with patch("tools.file_store._get_client", return_value=cm):
        exists = await file_exists("clients/test/proposals/v1.pdf")

    assert exists is True


@pytest.mark.asyncio
async def test_file_exists_false():
    cm, _ = _make_s3_client(raise_at="head_object")

    with patch("tools.file_store._get_client", return_value=cm):
        exists = await file_exists("clients/test/nonexistent.pdf")

    assert exists is False


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_files():
    cm, _ = _make_s3_client()

    with patch("tools.file_store._get_client", return_value=cm):
        keys = await list_files("clients/test/")

    assert "clients/test/file.pdf" in keys
