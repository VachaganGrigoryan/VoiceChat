from __future__ import annotations

from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from app.core.config import settings
from app.infra.storage.base import Storage, StoredFile, UploadTarget


class S3Storage(Storage):
    name = "s3"

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

    async def read(self, key: str) -> bytes:
        normalized = self._normalize_key(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=normalized)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404"}:
                raise FileNotFoundError(normalized) from exc
            raise

        return response["Body"].read()

    def create_upload_target(
        self,
        *,
        key: str,
        mime: str,
        expires_in: int,
    ) -> UploadTarget:
        normalized = self._normalize_key(key)
        url = self.client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket,
                "Key": normalized,
                "ContentType": mime,
            },
            ExpiresIn=expires_in,
        )
        return UploadTarget(
            url=url,
            method="PUT",
            headers={"Content-Type": mime},
        )
