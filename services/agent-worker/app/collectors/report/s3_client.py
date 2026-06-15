from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from app.core.config import get_settings


class ReportS3Client:
    def __init__(self) -> None:
        settings = get_settings()
        self._s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
        )
        self._bucket = settings.s3_report_bucket

    def upload_pdf(self, pdf_bytes: bytes, s3_key: str) -> str:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=s3_key,
            Body=pdf_bytes,
            ContentType="application/pdf",
        )
        return s3_key

    def download_pdf(self, s3_key: str) -> bytes:
        response = self._s3.get_object(Bucket=self._bucket, Key=s3_key)
        return response["Body"].read()

    def exists(self, s3_key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket, Key=s3_key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return False
            raise
