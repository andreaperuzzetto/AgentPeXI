from __future__ import annotations

import os
from pathlib import Path

import aiobotocore.session
import structlog

from tools import AgentToolError

log = structlog.get_logger()


class FileUploadError(AgentToolError):
    def __init__(self, message: str = "") -> None:
        super().__init__(code="tool_file_upload_error", message=message)


def _bucket() -> str:
    return os.environ["MINIO_BUCKET"]


def _get_client():  # returns async context manager
    session = aiobotocore.session.get_session()
    return session.create_client(
        "s3",
        endpoint_url=os.environ["MINIO_ENDPOINT"],
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
    )


async def upload_file(local_path: str | Path, object_key: str) -> str:
    local_path = Path(local_path)
    try:
        async with _get_client() as client:
            with local_path.open("rb") as fh:
                await client.put_object(
                    Bucket=_bucket(),
                    Key=object_key,
                    Body=fh,
                )
        log.info("file_store.uploaded", object_key=object_key)
        return object_key
    except Exception as exc:
        raise FileUploadError(str(exc)) from exc


async def upload_bytes(
    data: bytes,
    object_key: str,
    content_type: str = "application/octet-stream",
) -> str:
    try:
        async with _get_client() as client:
            await client.put_object(
                Bucket=_bucket(),
                Key=object_key,
                Body=data,
                ContentType=content_type,
            )
        log.info("file_store.uploaded_bytes", object_key=object_key)
        return object_key
    except Exception as exc:
        raise FileUploadError(str(exc)) from exc


async def download_file(object_key: str, local_path: str | Path) -> Path:
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with _get_client() as client:
            response = await client.get_object(Bucket=_bucket(), Key=object_key)
            async with response["Body"] as stream:
                data = await stream.read()
        local_path.write_bytes(data)
        return local_path
    except client.exceptions.NoSuchKey:
        raise FileNotFoundError(f"Object not found: {object_key}")
    except Exception as exc:
        raise FileUploadError(str(exc)) from exc


async def download_bytes(object_key: str) -> bytes:
    try:
        async with _get_client() as client:
            response = await client.get_object(Bucket=_bucket(), Key=object_key)
            async with response["Body"] as stream:
                return await stream.read()
    except Exception as exc:
        raise FileNotFoundError(f"Object not found: {object_key}") from exc


async def get_presigned_url(object_key: str, expires_in_seconds: int = 3600) -> str:
    async with _get_client() as client:
        return await client.generate_presigned_url(
            "get_object",
            Params={"Bucket": _bucket(), "Key": object_key},
            ExpiresIn=expires_in_seconds,
        )


async def file_exists(object_key: str) -> bool:
    try:
        async with _get_client() as client:
            await client.head_object(Bucket=_bucket(), Key=object_key)
        return True
    except Exception:
        return False


async def list_files(prefix: str) -> list[str]:
    keys: list[str] = []
    async with _get_client() as client:
        paginator = client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
    return keys
