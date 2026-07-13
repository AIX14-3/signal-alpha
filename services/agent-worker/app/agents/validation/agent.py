"""정규화·분석 검증 로직 — 결정론 프로파일 + (opt-in) LLM 검토.

무엇을 검증하나:
  1. **정규화 적절성** (결정론): 행 수·날짜 범위·신선도 + 소스별 이상 휴리스틱.
     실측된 오염을 직접 겨냥한다 — REPORT 목표주가 1·3·5원 파싱오류(20~25%),
     PRICE 세션 결손, DATALAB 무분산(수집 고장), DART shares_delta 전결측.
  2. **분석 적절성** (LLM, opt-in): 채점 결과(점수·근거)가 증거에 실제로 근거하는지,
     정규화 이상을 신호로 오독하지 않았는지 — 코호트당 1콜.

검증은 점수를 바꾸지 않는다. 산출은 (a) ``needs_review``/``risk_flags`` 승격 재료
(b) ``validation_logs`` 기록(관측 가능한 sink)뿐이다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

PROMPT_VERSION = "data-quality-v1"

# REPORT 목표주가 파싱오류 실측(1원·3원·5원): 한국 상장주 목표주가로 1,000원 미만은
# 사실상 파싱 산물이다. 이 비율이 높으면 수집/파싱 단계 오염.
REPORT_SUSPECT_PRICE_FLOOR = 1000.0

VALIDATION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "assessments": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "ticker": {"type": "STRING"},
                    "normalization_ok": {"type": "BOOLEAN"},
                    "analysis_ok": {"type": "BOOLEAN"},
                    "issues": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["ticker", "normalization_ok", "analysis_ok", "issues"],
            },
        }
    },
    "required": ["assessments"],
}

_PROMPT = """너는 데이터 품질 감사자다. 아래는 한 소스({source})의 종목별
정규화 데이터 프로파일과, LLM 채점기가 낸 점수·근거다. 두 가지만 판정하라:

1. normalization_ok — 정규화된 데이터가 온전한가? (파싱 산물로 보이는 값,
   비정상 결측/중복, 시계열 단절, 단위 오류 흔적이 있으면 false)
2. analysis_ok — 점수와 근거가 **주어진 데이터에 실제로 근거**하는가?
   (근거 문장이 데이터에 없는 사실을 말하거나, 데이터 이상을 신호로
   오독했으면 false. 점수의 방향에 동의하는지를 묻는 게 아니다.)

issues 에는 발견한 문제만 짧게 적어라(없으면 빈 배열). 점수를 다시 매기지 마라.

## 입력
{payload}

## 출력 — JSON 객체 하나만
{{"assessments": [{{"ticker": "...", "normalization_ok": true, "analysis_ok": true, "issues": []}}]}}
"""


@dataclass(frozen=True)
class StockValidation:
    ticker: str
    ok: bool
    issues: list[str] = field(default_factory=list)
    checked_by: str = "profile"  # profile | profile+llm


def profile_rows(source: str, pit: list[dict], asof: date) -> dict[str, Any]:
    """소스별 결정론 데이터 프로파일 — 순수 산술, LLM 무관, 항상 계산 가능."""
    dates = sorted(str(r.get(k) or "")[:10] for r in pit for k in (_DATE_KEYS.get(source),) if r.get(k))
    out: dict[str, Any] = {
        "rows": len(pit),
        "date_min": dates[0] if dates else None,
        "date_max": dates[-1] if dates else None,
        "staleness_days": (asof - date.fromisoformat(dates[-1])).days if dates else None,
        "anomalies": [],
    }
    anomalies: list[str] = out["anomalies"]
    if not pit:
        anomalies.append("no_rows")
        return out

    if source == "REPORT":
        prices = [float(r["target_price"]) for r in pit if r.get("target_price") is not None]
        suspects = [p for p in prices if p < REPORT_SUSPECT_PRICE_FLOOR]
        rate = round(len(suspects) / len(prices), 3) if prices else 0.0
        out["suspect_target_price_rate"] = rate
        if rate > 0.05:
            anomalies.append(f"suspect_target_price_rate={rate}")
    elif source == "PRICE":
        gaps = _max_gap_days(dates)
        out["max_gap_days"] = gaps
        if gaps is not None and gaps > 10:
            anomalies.append(f"max_session_gap_days={gaps}")
    elif source == "DATALAB":
        values = {r.get("search_index") for r in pit if r.get("search_index") is not None}
        out["distinct_values"] = len(values)
        if len(values) <= 1 and len(pit) >= 10:
            anomalies.append("zero_variance_series")
        out["risk_rows"] = sum(1 for r in pit if (r.get("polarity") or "demand") == "risk")
    elif source == "DART":
        nulls = sum(1 for r in pit if r.get("shares_delta") is None)
        out["null_shares_delta_rate"] = round(nulls / len(pit), 3)
        if nulls == len(pit):
            anomalies.append("all_shares_delta_null")
    elif source == "HIRING":
        if len(pit) < 3:
            anomalies.append("sparse_rows")
    return out


class DataQualityAgent:
    """결정론 프로파일 → (client 있으면) LLM 검토 → 종목별 verdict."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def llm_model(self) -> str | None:
        return getattr(self._client, "model", None) if self._client else None

    def profile(
        self, source: str, asof: date, pit_by_ticker: dict[str, list[dict]]
    ) -> dict[str, dict[str, Any]]:
        return {t: profile_rows(source, pit, asof) for t, pit in pit_by_ticker.items()}

    async def review(
        self,
        *,
        source: str,
        asof: date,
        profiles: dict[str, dict[str, Any]],
        scored: dict[str, dict[str, Any]] | None = None,
    ) -> list[StockValidation]:
        """profiles(+채점 결과) → 종목별 verdict. LLM 실패는 결정론 결과로 강등(발행 무영향)."""
        deterministic = {
            t: list(p.get("anomalies") or []) for t, p in profiles.items()
        }
        if self._client is None:
            return [
                StockValidation(ticker=t, ok=not issues, issues=issues)
                for t, issues in deterministic.items()
            ]

        payload = {
            "source": source,
            "asof": str(asof),
            "stocks": [
                {
                    "ticker": t,
                    "profile": profiles[t],
                    **({"scored": scored[t]} if scored and t in scored else {}),
                }
                for t in profiles
            ],
        }
        prompt = _PROMPT.format(
            source=source, payload=json.dumps(payload, ensure_ascii=False, default=str)
        )
        try:
            response = await self._client.generate_json(prompt, VALIDATION_SCHEMA)
            by_ticker = {
                str(a.get("ticker")): a
                for a in (response or {}).get("assessments", [])
                if isinstance(a, dict)
            }
        except Exception as exc:  # noqa: BLE001 — 검증 실패가 발행을 못 막는다
            logger.warning("data-quality LLM 검토 실패(%s) — 결정론 프로파일만 사용: %s", source, exc)
            return [
                StockValidation(ticker=t, ok=not issues, issues=issues)
                for t, issues in deterministic.items()
            ]

        out: list[StockValidation] = []
        for t, det_issues in deterministic.items():
            a = by_ticker.get(t) or {}
            llm_issues = [str(i).strip() for i in (a.get("issues") or []) if str(i).strip()]
            ok = (
                bool(a.get("normalization_ok", True))
                and bool(a.get("analysis_ok", True))
                and not det_issues
            )
            out.append(
                StockValidation(
                    ticker=t,
                    ok=ok,
                    issues=det_issues + llm_issues,
                    checked_by="profile+llm",
                )
            )
        return out


_DATE_KEYS = {
    "DATALAB": "observed_date",
    "HIRING": "observed_date",
    "PATENT": "application_date",
    "DART": "report_date",
    "REPORT": "publish_date",
    "PRICE": "trade_date",
}


def _max_gap_days(sorted_dates: list[str]) -> int | None:
    if len(sorted_dates) < 2:
        return None
    gaps = []
    for a, b in zip(sorted_dates, sorted_dates[1:]):
        try:
            gaps.append((date.fromisoformat(b) - date.fromisoformat(a)).days)
        except ValueError:
            continue
    return max(gaps) if gaps else None
