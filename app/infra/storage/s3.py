from __future__ import annotations

import uuid
from pathlib import Path

import boto3

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

    async def save(self, *, filename: str, content: bytes, mime: str) -> StoredFile:
        ext = Path(filename).suffix.lower() or ".bin"
        key = f"voice/{uuid.uuid4().hex}{ext}"

        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=mime,
        )

        # For MinIO in docker, endpoint is often http://minio:9000 internally,
        # but clients need a public URL. For now return endpoint_url + bucket + key.
        # base = settings.s3_endpoint_url.rstrip("/")
        # url = f"{base}/{self.bucket}/{key}"
        url = self.get_presigned_url(key)

        return StoredFile(
            storage="s3",
            key=key,
            url=url,
            size_bytes=len(content),
            mime=mime,
        )

    def get_file_url(self, key: str) -> str:
        return self.get_presigned_url(key)

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
            },
            ExpiresIn=expires_in,
        )