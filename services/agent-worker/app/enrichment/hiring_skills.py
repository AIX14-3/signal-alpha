"""Extract a tech-skill set from a hiring poster image via OCR.

Pure logic (``canonicalize_token`` / ``extract_skills`` / the ``_TECH`` dictionary)
is split from the DB+OCR orchestration (``HiringSkillEnricher``) so the keyword
contract — boundary/case rules that stop ``ML`` matching ``MLOps``/``HTML``, the
canonical comparison key — is unit-tested without any I/O.

This module is the **canonical home of the skill dictionary**; the research
benchmark harness (``scripts/research/ocr_harness.py``) imports from here so the
two never drift. Selection rationale (Tesseract): ``docs/archive/research/375-ocr-model-comparison.md``.

Cached skills (``hiring_raw_details.ocr_skills``, see migration 028) let the Hiring
analyzer weight postings by concrete tech demand instead of raw counts. Each
posting is enriched once (``ocr_status`` flips pending → success/failed/skipped).
"""
from __future__ import annotations

import json
import re
from typing import Any

# ── 기술 키워드 사전 (canonical, regex, case-sensitive?) ──────────────────────
# 짧고 모호한 토큰(C/Go/R/ML)은 경계·대소문자로 오탐을 줄인다. 수집·평가·enrichment
# 전 경로 공통 사전(공정). canonical 명칭은 ground_truth 라벨 표기와 정합한다.
_TECH: list[tuple[str, str, bool]] = [
    ("C++", r"c\s*\+\+", False),
    ("C#", r"c\s*#", False),
    ("C", r"(?<![\w+#])C(?![\w+#])", True),          # 대문자 C 단독 (C++/C# 제외)
    ("Python", r"\bpython\b", False),
    ("Java", r"\bjava\b(?!script)", False),
    ("JavaScript", r"\bjavascript\b", False),
    ("TypeScript", r"\btypescript\b", False),
    ("Go", r"\bgo(?:lang)?\b", False),
    ("Scala", r"\bscala\b", False),
    ("Kotlin", r"\bkotlin\b", False),
    ("Rust", r"\brust\b", False),
    ("Spring Boot", r"\bspring(?:\s*boot)?\b", False),  # GT 라벨 정합(canonical=Spring Boot)
    ("React", r"\breact(?:\.js)?\b", False),
    ("Vue", r"\bvue(?:\.js)?\b", False),
    ("Django", r"\bdjango\b", False),
    ("FastAPI", r"\bfastapi\b", False),
    (".NET", r"\.net\b", False),
    ("Node.js", r"\bnode(?:\.js)?\b", False),
    ("PyTorch", r"\bpytorch\b", False),
    ("TensorFlow", r"\btensorflow\b", False),
    ("MySQL", r"\bmysql\b", False),
    ("PostgreSQL", r"\b(?:postgres(?:ql)?)\b", False),
    ("Oracle", r"\boracle\b", False),
    ("Redis", r"\bredis\b", False),
    ("MongoDB", r"\bmongodb\b", False),
    ("Hadoop", r"\bhadoop\b", False),
    ("Spark", r"\bspark\b", False),
    ("Kafka", r"\bkafka\b", False),
    ("ELK", r"\b(?:elk|elasticsearch)\b", False),
    ("Docker", r"\bdocker\b", False),
    ("Kubernetes", r"\b(?:kubernetes|k8s)\b", False),
    ("Helm", r"\bhelm\b", False),
    ("ArgoCD", r"\bargo\s?cd\b", False),
    ("Jenkins", r"\bjenkins\b", False),
    ("GitOps", r"\bgitops\b", False),
    ("Git", r"\bgit\b(?!ops|hub|lab)", False),
    ("Terraform", r"\bterraform\b", False),
    ("CI/CD", r"\bci/?cd\b", False),
    ("Linux", r"\blinux\b", False),
    ("AWS", r"\baws\b", False),
    ("GCP", r"\bgcp\b", False),
    ("Azure", r"\bazure\b", False),
    ("Grafana", r"\bgrafana\b", False),
    ("Prometheus", r"\bprometheus\b", False),
    ("Loki", r"\bloki\b", False),
    ("Jaeger", r"\bjaeger\b", False),
    ("Bash", r"\bbash\b", False),
    ("CMake", r"\bcmake\b", False),
    ("DPDK", r"\bdpdk\b", False),
    ("IDA", r"\bida\b", False),
    ("MLOps", r"\bmlops\b", False),
    ("DevSecOps", r"\bdevsecops\b", False),
    ("DevOps", r"\bdevops\b", False),
    ("eBPF", r"\bebpf\b", False),
    # ── AI/ML 약어 (짧고 모호 → 대소문자·경계로 오탐 차단) ──
    ("ML", r"(?<![A-Za-z])ML(?![A-Za-z])", True),       # MLOps/HTML/XML 오탐 방지
    ("LLM", r"(?<![A-Za-z])LLM(?![A-Za-z])", True),
    ("sLM", r"\bs(?:mall\s*)?lm\b", False),             # sLM/SLM
    ("AI Agent", r"\bai\s*agent\b", False),
    ("Service Mesh", r"\bservice\s*mesh\b", False),
    # ── 한글 직무/기술 용어 (GT 라벨 정합) ──
    ("데이터분석", r"데이터\s*분석", False),
    ("클라우드 보안", r"클라우드\s*보안", False),
    ("영상처리", r"영상\s*처리", False),
    ("영상 알고리즘", r"영상\s*알고리즘", False),
    ("Windows App", r"\bwindows\s*app\b", False),
    ("HW개발", r"(?:\bhw\b|하드웨어)\s*개발", False),
]
_COMPILED = [(name, re.compile(pat, 0 if cs else re.IGNORECASE)) for name, pat, cs in _TECH]

_IMAGE_URL_KEYS = ("image_urls", "image_url", "_image_urls")


# ── 순수 함수 (단위테스트 대상) ───────────────────────────────────────────────
def canonicalize_token(tok: str) -> str:
    """비교용 정규화 키. 추출/GT 표기차를 흡수해 '같은 저울'로 맞춘다.

    소문자화 + ``.js`` 접미 제거 + 전체 공백 제거. 특수문자(``+``/``#``/``.``)는 보존해
    ``C++``/``C#``/``.NET`` 토큰 유실을 막는다.
        React.js → react,  "Spring Boot" → springboot,  "클라우드 보안" → 클라우드보안
    """
    t = tok.strip().lower()
    t = re.sub(r"\.js\b", "", t)   # react.js→react, node.js→node
    t = re.sub(r"\s+", "", t)      # 공백 제거 → 한글/복합어 표기차 흡수
    return t


def extract_skills(text: str) -> set[str]:
    """OCR 텍스트 → 사전 매칭된 canonical 기술 집합(전 경로 공통)."""
    return {name for name, rx in _COMPILED if rx.search(text or "")}


def image_urls_of(extra_payload: Any) -> list[str]:
    """hiring_raw_details.extra_payload 에서 포스터 이미지 URL 목록을 뽑는다.

    수집기(jasoseol ``_image_urls``)가 ``image_urls`` 리스트로 저장한다. 단수 ``image_url``
    문자열도 흡수한다. 없으면 빈 리스트(→ enrich 시 skipped).
    """
    payload = _as_dict(extra_payload)
    for key in _IMAGE_URL_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        if isinstance(value, (list, tuple)):
            urls = [str(u).strip() for u in value if str(u or "").strip()]
            if urls:
                return urls
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class HiringSkillEnricher:
    """Enrich pending hiring postings: poster image(s) → OCR → cached skill set.

    ``repository`` must expose ``list_unenriched_hiring_details(limit,
    raw_document_ids)`` and ``update_hiring_ocr_skills(raw_document_id, skills,
    status)``; ``ocr`` must expose async ``image_to_text(image_urls) -> str``.
    Both are injected so this class is testable with fakes (no real OCR / DB).

    Mirrors ``PatentEnricher``: the OCR processor returns raw text and the pure
    ``extract_skills`` derives the cached set, so the keyword contract stays
    unit-tested. ``raw_document_ids`` scopes the run to the rows a matching
    NORMALIZE_HIRING just promoted; pass None for the global pending sweep.
    """

    def __init__(
        self,
        repository: Any,
        ocr: Any,
        *,
        batch_limit: int = 200,
        raw_document_ids: list[int] | None = None,
    ) -> None:
        self._repository = repository
        self._ocr = ocr
        self._batch_limit = batch_limit
        self._raw_document_ids = raw_document_ids

    async def run(self) -> dict[str, int]:
        rows = await self._repository.list_unenriched_hiring_details(
            limit=self._batch_limit, raw_document_ids=self._raw_document_ids
        )
        stats = {"total": len(rows), "success": 0, "failed": 0, "skipped": 0}
        for row in rows:
            await self._enrich_one(row, stats)
        return stats

    async def _enrich_one(self, row: Any, stats: dict[str, int]) -> None:
        raw_document_id = row["raw_document_id"]
        image_urls = image_urls_of(row.get("extra_payload"))
        if not image_urls:
            await self._repository.update_hiring_ocr_skills(
                raw_document_id=raw_document_id, skills=None, status="skipped"
            )
            stats["skipped"] += 1
            return
        try:
            text = await self._ocr.image_to_text(image_urls)
            skills = sorted(extract_skills(text))
        except Exception:
            await self._repository.update_hiring_ocr_skills(
                raw_document_id=raw_document_id, skills=None, status="failed"
            )
            stats["failed"] += 1
            return
        await self._repository.update_hiring_ocr_skills(
            raw_document_id=raw_document_id, skills=skills, status="success"
        )
        stats["success"] += 1
