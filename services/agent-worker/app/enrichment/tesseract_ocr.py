"""Tesseract OCR processor for hiring poster images (the ENRICH_HIRING engine).

Selected over PaddleOCR/Surya in ``docs/archive/research/375-ocr-model-comparison.md`` §4
(highest F1, ~37x faster on CPU). Downloads the poster image(s) referenced by
``hiring_raw_details.extra_payload.image_urls`` and runs Tesseract (kor+eng).

Heavy/native imports (``pytesseract``/``PIL``) are deferred to call time so this
module imports cleanly even where the ``tesseract`` binary or Pillow are absent —
the enrich handler then degrades (skips OCR) instead of crashing the worker, the
same best-effort boundary as the patent LLM enricher.

Runtime requirements (worker image / env):
  - ``tesseract`` binary + ``kor``/``eng`` traineddata, ``TESSDATA_PREFIX`` set.
  - ``pip``/``uv`` deps: ``pytesseract``, ``Pillow``.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_LANG = "kor+eng"
_MAX_IMAGE_BYTES = 12 * 1024 * 1024   # 포스터 이미지 상한(이상치/악성 대용량 차단)
_MAX_IMAGES = 8                       # 공고당 OCR 이미지 수 상한(레이턴시 방어)
_HTTP_TIMEOUT = 20.0


class TesseractOCRProcessor:
    """Download poster image(s) → Tesseract → concatenated text.

    Exposes async ``image_to_text(image_urls) -> str`` (the contract
    ``HiringSkillEnricher`` depends on). The blocking download + OCR run in a
    worker thread so the event loop is not stalled. ``http_client`` (an httpx
    AsyncClient) is injectable for tests; a short-lived one is created per call
    otherwise.
    """

    def __init__(
        self,
        *,
        lang: str = _DEFAULT_LANG,
        http_client: Any | None = None,
        max_images: int = _MAX_IMAGES,
    ) -> None:
        self._lang = lang
        self._http_client = http_client
        self._max_images = max_images

    async def image_to_text(self, image_urls: list[str]) -> str:
        texts: list[str] = []
        for url in image_urls[: self._max_images]:
            data = await self._download(url)
            if not data:
                continue
            text = await asyncio.to_thread(self._ocr_bytes, data)
            if text.strip():
                texts.append(text)
        return "\n".join(texts)

    async def _download(self, url: str) -> bytes | None:
        if self._http_client is not None:
            return await self._download_with(self._http_client, url)
        import httpx

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            return await self._download_with(client, url)

    async def _download_with(self, client: Any, url: str) -> bytes | None:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — 다운로드 실패는 해당 이미지만 건너뜀
            logger.warning("hiring OCR image download failed: %s (%s)", url, exc)
            return None
        data = resp.content
        if len(data) > _MAX_IMAGE_BYTES:
            logger.warning("hiring OCR image too large (%d bytes): %s", len(data), url)
            return None
        return data

    def _ocr_bytes(self, data: bytes) -> str:
        """Blocking: image bytes → RGB → Tesseract 텍스트. webp 등은 PIL 이 흡수."""
        import pytesseract
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return pytesseract.image_to_string(im.convert("RGB"), lang=self._lang)
