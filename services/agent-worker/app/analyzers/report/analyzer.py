"""
Report Analyzer
RawEvidence(리포트 메타데이터)를 읽어 SourceResult를 만든다.
- 목표주가 평균 / 상향·하향 트렌드 / 증권사 간 의견 충돌 탐지
- pgvector Top-K 검색으로 핵심 근거 청크 추출
- 분석 결과를 report_signal 테이블에 저장
- 외부 수집(크롤링, API 호출)을 직접 수행하지 않는다.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.schemas.evidence import RawEvidence
from app.schemas.source_result import EvidenceItem, ReportMeta, SourceResult

if TYPE_CHECKING:
    pass

STOCK_NAME_MAP = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "네이버",
}

OPINION_DIRECTION = {
    "buy": "positive",
    "neutral": "neutral",
    "sell": "negative",
}


def _avg_target(reports: list[dict]) -> float | None:
    prices = [r["target_price"] for r in reports if r.get("target_price")]
    return round(sum(prices) / len(prices)) if prices else None


def _target_trend(reports: list[dict]) -> str:
    """날짜순 정렬 후 앞 절반 vs 뒷 절반 평균 비교로 트렌드 판단"""
    dated = sorted(
        [r for r in reports if r.get("target_price") and r.get("date")],
        key=lambda r: r["date"],
    )
    if len(dated) < 2:
        return "unknown"
    mid = len(dated) // 2
    old_avg = sum(r["target_price"] for r in dated[:mid]) / mid
    new_avg = sum(r["target_price"] for r in dated[mid:]) / (len(dated) - mid)
    if new_avg > old_avg * 1.03:
        return "up"
    if new_avg < old_avg * 0.97:
        return "down"
    return "flat"


def _conflict_detected(reports: list[dict]) -> bool:
    """최신 리포트 목표주가 간 최대-최소 괴리율 25% 이상이면 conflict"""
    prices = [r["target_price"] for r in reports if r.get("target_price")]
    if len(prices) < 2:
        return False
    gap = (max(prices) - min(prices)) / min(prices)
    return gap >= 0.25


def _get_current_price(stock_code: str) -> float | None:
    """price_raw 테이블에서 최신 종가 조회 (규태 C-6 완성 후 동작)"""
    try:
        import psycopg2
        settings = get_settings()
        conn = psycopg2.connect(settings.database_url)
        cur = conn.cursor()
        cur.execute(
            "SELECT close FROM price_raw WHERE stock_code = %s ORDER BY date DESC LIMIT 1",
            (stock_code,),
        )
        row = cur.fetchone()
        conn.close()
        return float(row[0]) if row else None
    except Exception:
        return None


def _save_report_signal(stock_code: str, result: SourceResult) -> None:
    """분석 결과를 report_signal 테이블에 저장"""
    try:
        import psycopg2
        settings = get_settings()
        meta = result.report_meta
        conn = psycopg2.connect(settings.database_url)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO report_signal
                (stock_code, direction, score, avg_target, upside_pct,
                 target_trend, conflict_detected, opinions, risk_flags, summary, data_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                stock_code,
                result.direction,
                result.score,
                meta.avg_target if meta else None,
                meta.upside_pct if meta else None,
                meta.target_trend if meta else None,
                meta.conflict_detected if meta else None,
                json.dumps(meta.opinions if meta else [], ensure_ascii=False),
                json.dumps(result.risk_flags, ensure_ascii=False),
                result.summary,
                result.data_status,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _rag_search(stock_code: str, top_k: int = 3) -> list[str]:
    """pgvector에서 해당 종목 리포트 청크 Top-K 검색"""
    try:
        import psycopg2
        from sentence_transformers import SentenceTransformer

        settings = get_settings()
        model = SentenceTransformer("BAAI/bge-m3")
        stock_name = STOCK_NAME_MAP.get(stock_code, stock_code)
        query = f"{stock_name} 목표주가 투자의견 핵심 근거"
        q_emb = model.encode([query], normalize_embeddings=True)[0].tolist()

        conn = psycopg2.connect(settings.database_url)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT chunk_text
            FROM report_chunks
            WHERE stock_code = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (stock_code, q_emb, top_k),
        )
        rows = cur.fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def _direction_from_opinions(reports: list[dict]) -> tuple[str, float]:
    """투자의견 분포로 방향성과 score 산출"""
    counts = {"positive": 0, "neutral": 0, "negative": 0}
    for r in reports:
        d = OPINION_DIRECTION.get(r.get("opinion", ""), "neutral")
        counts[d] += 1

    total = sum(counts.values()) or 1
    pos_rate = counts["positive"] / total
    neg_rate = counts["negative"] / total

    if pos_rate >= 0.6:
        direction, score = "positive", 50 + round(pos_rate * 40)
    elif neg_rate >= 0.6:
        direction, score = "negative", 50 + round(neg_rate * 40)
    elif abs(pos_rate - neg_rate) <= 0.2:
        direction, score = "mixed", 50
    else:
        direction, score = "neutral", 50

    return direction, float(score)


class ReportAnalyzer:
    source = "report"

    def analyze(self, stock_code: str, evidence: list[RawEvidence]) -> SourceResult:
        if not evidence:
            return SourceResult(
                source="report",
                stock_code=stock_code,
                direction="unknown",
                score=0.0,
                summary="수집된 리포트 데이터가 없습니다.",
                data_status="failed",
            )

        reports = [
            {
                "firm": e.metadata.get("firm", ""),
                "date": e.published_at or "",
                "title": e.title,
                "url": e.url or "",
                "target_price": int(e.metadata["target_price"])
                if e.metadata.get("target_price")
                else None,
                "opinion": e.metadata.get("opinion", "unknown"),
                "key_rationale": e.content.split("\n")[0] if e.content else "",
            }
            for e in evidence
        ]

        avg_target = _avg_target(reports)
        trend = _target_trend(reports)
        conflict = _conflict_detected(reports)
        direction, score = _direction_from_opinions(reports)

        current_price = _get_current_price(stock_code)
        upside_pct = (
            round((avg_target - current_price) / current_price * 100, 2)
            if avg_target and current_price
            else None
        )

        # pgvector Top-K 청크로 핵심 근거 보강
        rag_chunks = _rag_search(stock_code, top_k=3)

        evidence_items = [
            EvidenceItem(
                title=r["title"],
                summary=r["key_rationale"][:120] if r["key_rationale"] else "",
                url=r["url"] or None,
                published_at=r["date"],
                source_name=r["firm"],
            )
            for r in sorted(reports, key=lambda x: x["date"], reverse=True)[:5]
        ]

        opinions_list = [
            {"firm": r["firm"], "target": r["target_price"], "view": r["opinion"]}
            for r in reports
            if r["target_price"]
        ]

        risk_flags = []
        if conflict:
            risk_flags.append("증권사 간 목표주가 괴리율 25% 이상 — 추가 확인 필요")
        if trend == "down":
            risk_flags.append("최근 목표주가 하향 흐름")

        trend_kor = {"up": "상향", "down": "하향", "flat": "유지", "unknown": "확인 불가"}
        avg_str = f"{avg_target:,}원" if avg_target else "정보 없음"
        summary_parts = [
            f"최근 {len(reports)}건의 리포트를 분석했습니다.",
            f"목표주가 평균은 {avg_str}이며, 최근 흐름은 {trend_kor.get(trend, '')}입니다.",
        ]
        if rag_chunks:
            summary_parts.append(
                "핵심 근거: " + rag_chunks[0][:100].replace("\n", " ") + "…"
            )
        if conflict:
            summary_parts.append(
                "증권사 간 목표주가 차이가 크므로 원문 확인이 권장됩니다."
            )

        result = SourceResult(
            source="report",
            stock_code=stock_code,
            direction=direction,
            score=score,
            summary=" ".join(summary_parts),
            evidence_items=evidence_items,
            risk_flags=risk_flags,
            data_status="ok",
            report_meta=ReportMeta(
                avg_target=avg_target,
                upside_pct=upside_pct,
                target_trend=trend,
                conflict_detected=conflict,
                opinions=opinions_list,
            ),
        )
        _save_report_signal(stock_code, result)
        return result
