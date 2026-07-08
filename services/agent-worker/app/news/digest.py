"""뉴스 다이제스트 — 관련도 규칙 선별 + 프롬프트 조립 + LLM 출력 검증.

파이프라인(daemon 이 호출):
  1. select_candidates  — stock_news 후보를 종목명 규칙으로 1차 필터·최신순 캡(순수 함수).
  2. source_hash        — 후보 기사집합 해시(동일 집합 재요약 멱등 skip 키).
  3. build_digest_prompt — 후보 + 종목명을 프롬프트로.
  4. (LLM 호출은 daemon 이 AnthropicJsonClient 로) → dict.
  5. validate_digest    — {selected_ids, digest_text} 스키마 + 길이 + 투자권유 필터.

디스플레이 전용(display-only): 신호 점수/방향과 무관. 방향/감성 라벨·예측·투자권유는
validate 단계에서 reject_advice 로 차단한다(narrate 채널과 동급).
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from app.narrate.base import NarrateError, build_prompt, reject_advice

PROMPT_VERSION = "news-digest-v1"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "news_digest_v1.md"

# digest_text 길이 상한(프롬프트와 정합) — 초과 시 검증 실패로 폐기.
MAX_DIGEST_CHARS = 120

# 구조화 출력 스키마(output_config.format). selected_ids=영향도 선별 기사 id, digest_text=종합 1문장.
DIGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "selected_ids": {"type": "array", "items": {"type": "integer"}},
        "digest_text": {"type": "string"},
    },
    "required": ["selected_ids", "digest_text"],
    "additionalProperties": False,
}


def select_candidates(
    articles: list[dict[str, Any]],
    *,
    stock_name: str | None,
    ticker: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """종목 관련 기사만 최신순으로 최대 ``limit`` 개 후보로 추린다(순수 함수, LLM 없음).

    관련도 = 제목/스니펫에 종목명(또는 티커)이 등장. 종목명이 없으면(방어) 필터를 건너뛴다.
    각 후보에 1-based ``id`` 를 부여 — LLM 의 selected_ids 가 이 id 를 참조한다.
    수집 단계에서 이미 제목 dedup·lookback 필터를 거쳤으므로 여기선 관련도·최신·캡만 한다.
    """
    needles = [n.strip().lower() for n in (stock_name, ticker) if n and n.strip()]

    def _relevant(article: dict[str, Any]) -> bool:
        if not needles:
            return True
        haystack = f"{article.get('title') or ''} {article.get('summary') or ''}".lower()
        return any(needle in haystack for needle in needles)

    relevant = [a for a in articles if _relevant(a)]
    # 최신순(발행시각 없는 항목은 뒤로). datetime 비교 안정화를 위해 None 을 최소값으로.
    relevant.sort(key=_published_sort_key, reverse=True)

    candidates: list[dict[str, Any]] = []
    for index, article in enumerate(relevant[: max(0, limit)], start=1):
        candidates.append(
            {
                "id": index,
                "article_hash": article.get("article_hash"),
                "title": article.get("title") or "",
                "summary": article.get("summary") or "",
                "published_at": article.get("published_at"),
            }
        )
    return candidates


def _published_sort_key(article: dict[str, Any]) -> datetime:
    value = article.get("published_at")
    if isinstance(value, datetime):
        return value
    return datetime.min


def source_hash(candidates: list[dict[str, Any]]) -> str:
    """후보 기사집합의 해시 — 동일 집합이면 재요약 skip(비용 멱등). 순서 무관."""
    hashes = sorted(str(c.get("article_hash") or c.get("title") or "") for c in candidates)
    return hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()


def build_digest_prompt(candidates: list[dict[str, Any]], *, stock_name: str) -> str:
    """후보를 프롬프트로. 스니펫만 넣어 토큰을 아끼고 article_hash 는 내부용이라 제외한다."""
    payload = {
        "stock_name": stock_name,
        "articles": [
            {
                "id": c["id"],
                "title": c["title"],
                "summary": c["summary"],
                "published_at": c["published_at"],
            }
            for c in candidates
        ],
    }
    return build_prompt(PROMPT_PATH, payload)


def validate_digest(
    payload: Any,
    *,
    candidate_ids: set[int],
    max_len: int = MAX_DIGEST_CHARS,
) -> tuple[str, int]:
    """LLM 출력(dict) → (digest_text, article_count). 위반 시 NarrateError.

    - digest_text: 비어있지 않고 길이 상한 이내, 투자권유·예측 표현 없음(reject_advice).
    - selected_ids: 후보 id 부분집합(환각 id 거부). article_count = 유효 선별 수.
    빈 digest(관련 기사 없음)는 "생성 안 함" 신호로 NarrateError 를 던져 daemon 이 skip 한다.
    """
    if not isinstance(payload, dict):
        raise NarrateError("digest: response was not a JSON object")

    digest_text = str(payload.get("digest_text") or "").strip()
    if not digest_text:
        raise NarrateError("digest: empty digest_text (no relevant news)")
    if len(digest_text) > max_len:
        raise NarrateError(f"digest: digest_text exceeds {max_len} chars")

    raw_ids = payload.get("selected_ids") or []
    if not isinstance(raw_ids, list):
        raise NarrateError("digest: selected_ids was not a list")
    selected = {int(i) for i in raw_ids if isinstance(i, (int, float)) and int(i) in candidate_ids}
    if not selected:
        raise NarrateError("digest: no valid selected_ids")

    # 투자권유/예측 필터(narrate 채널과 동급) — 위반 시 폐기.
    reject_advice([digest_text])

    return digest_text, len(selected)
