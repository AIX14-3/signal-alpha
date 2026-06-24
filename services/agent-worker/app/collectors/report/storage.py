from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Protocol


class ReportStorageClient(Protocol):
    def upload_pdf(self, pdf_bytes: bytes, key: str) -> str: ...

    def download_pdf(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...


class GcsReportStorageClient:
    def __init__(
        self,
        *,
        bucket_name: str,
        client: Any | None = None,
    ) -> None:
        self._bucket_name = bucket_name
        self._client = client or _build_gcs_client()

    def upload_pdf(self, pdf_bytes: bytes, key: str) -> str:
        blob = self._blob(key)
        blob.upload_from_string(pdf_bytes, content_type="application/pdf")
        return key

    def download_pdf(self, key: str) -> bytes:
        return self._blob(key).download_as_bytes()

    def exists(self, key: str) -> bool:
        return bool(self._blob(key).exists())

    def _blob(self, key: str) -> Any:
        return self._client.bucket(self._bucket_name).blob(key)


class LocalReportStorageClient:
    def __init__(self, *, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir).expanduser().resolve()

    def upload_pdf(self, pdf_bytes: bytes, key: str) -> str:
        path = self._path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pdf_bytes)
        return key

    def download_pdf(self, key: str) -> bytes:
        return self._path_for_key(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path_for_key(key).is_file()

    def _path_for_key(self, key: str) -> Path:
        key_path = PurePosixPath(key)
        if key_path.is_absolute() or any(part in {"", ".", ".."} for part in key_path.parts):
            raise ValueError(f"Invalid report storage key: {key}")

        path = (self._base_dir / Path(*key_path.parts)).resolve()
        try:
            path.relative_to(self._base_dir)
        except ValueError as exc:
            raise ValueError(f"Invalid report storage key: {key}") from exc
        return path


def get_report_storage_client(
    settings: Any,
    *,
    gcs_client: Any | None = None,
) -> ReportStorageClient:
    backend = str(getattr(settings, "report_storage_backend", "gcs")).strip().lower()
    if backend == "local":
        base_dir = getattr(settings, "report_local_storage_dir", "")
        if not base_dir:
            raise ValueError("REPORT_LOCAL_STORAGE_DIR is required for local report storage.")
        return LocalReportStorageClient(base_dir=base_dir)

    if backend != "gcs":
        raise ValueError(f"Unsupported report storage backend: {backend}")

    bucket_name = str(getattr(settings, "gcs_report_bucket", "")).strip()
    if not bucket_name:
        raise ValueError("GCS_REPORT_BUCKET is required for report storage.")

    return GcsReportStorageClient(bucket_name=bucket_name, client=gcs_client)


def _build_gcs_client() -> Any:
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-storage is required for REPORT_STORAGE_BACKEND=gcs."
        ) from exc
    return storage.Client()
