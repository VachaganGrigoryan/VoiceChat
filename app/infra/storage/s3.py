from __future__ import annotations

from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from app.core.config import settings
from app.infra.storage.base import Storage, StoredFile


class S3Storage(Storage):
    def __init__(self):
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key or None,
            aws_secret_access_key=settings.s3_secret_key or None,
            region_name=settings.s3_region or None,
        )
        self.bucket = settings.s3_bucket

    def _normalize_key(self, key: str) -> str:
        return key.replace("\\", "/").lstrip("/")

    async def save(
            self,
            *,
            filename: str,
            content: bytes,
            mime: str,
            key: str | None = None,
    ) -> StoredFile:
        ext = Path(filename).suffix.lower() or ".bin"

        key = self._normalize_key(key)
        if not Path(key).suffix:
            key = f"{key}{ext}"

        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=mime,
        )

        return StoredFile(
            storage="s3",
            key=key,
            url=self.get_presigned_url(key),
            size_bytes=len(content),
            mime=mime,
        )

    async def delete(self, key: str) -> None:
        try:
            self.client.delete_object(
                Bucket=self.bucket,
                Key=self._normalize_key(key),
            )
        except ClientError:
            return

    def get_file_url(self, key: str) -> str:
        return self.get_presigned_url(self._normalize_key(key))

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        normalized = self._normalize_key(key)
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": normalized,
            },
            ExpiresIn=expires_in,
        )
