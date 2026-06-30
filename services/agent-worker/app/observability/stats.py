"""Pure observability helpers — collector/queue success-rate math and formatting.

No DB, no clock-dependent logic except the explicit KST date conversion. Kept
pure so the rate semantics are unit-testable in isolation.

Count semantics (intentionally distinct — a skip is NOT a failure):
  collected = total fetched
  inserted  = newly stored
  skipped   = duplicate / unmapped (benign, not an error)
  failed    = real error
  ingest_success_rate = (inserted + skipped) / collected   # non-error ratio
  failure_rate        = failed / collected
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

# Collection day boundaries follow KST (collectors stamp KST); see base_collector.
_KST = ZoneInfo("Asia/Seoul")


def ingest_success_rate(collected: int, inserted: int, skipped: int) -> float | None:
    """(inserted + skipped) / collected in [0,1], or None when nothing collected."""
    if collected <= 0:
        return None
    return (inserted + skipped) / collected


def failure_rate(collected: int, failed: int) -> float | None:
    """failed / collected in [0,1], or None when nothing collected."""
    if collected <= 0:
        return None
    return failed / collected


def calculate_run_status(inserted: int, skipped: int, failed: int) -> str:
    """collector_runs.status 판정 — usable(inserted|skipped) / failed 기반.

    A skip is NOT a failure (see module docstring): usable = inserted | skipped.
    빈 결과(0건)도 실패가 아니다 — 진짜 에러(예외)는 호출자가 except 에서 직접
    status='failed' 로 기록하므로, 이 함수에 도달한 (0,0,0)은 "정상 실행했으나
    그날 검색량/특허가 없었다"는 뜻이다(특히 DataLab 롱테일 키워드). 빈결과를 failed
    로 세면 실패율이 크게 부풀려진다(prod DATALAB 실패의 ~99%가 이 케이스였음).
      - usable & failed == 0   → "success"
      - usable & failed > 0    → "partial"
      - 전건 적재 실패(0,0,>0)  → "failed"
      - 빈 런(0,0,0)           → "success"  (데이터 없음 ≠ 실패)

    참고: collector_runs.status CHECK 제약은 {running,success,partial,failed}만 허용
    하므로 빈결과 전용 상태("no_data")는 쓰지 않고 success 로 흡수한다.
    """
    has_usable = inserted > 0 or skipped > 0
    if has_usable:
        return "success" if failed == 0 else "partial"
    # usable 0건: 적재 시도가 전부 실패했을 때만 failed, 그 외(아예 0건)는 success.
    return "failed" if failed > 0 else "success"


@dataclass(frozen=True)
class RunStats:
    """Internal value object for one run's counts + derived rates.

    Not an HTTP response model — routes return plain dicts (house convention).
    Used by the logging formatter and aggregation.
    """

    collected: int
    inserted: int
    skipped: int
    failed: int

    @classmethod
    def from_counts(
        cls,
        *,
        collected: int,
        inserted: int,
        skipped: int,
        failed: int,
    ) -> RunStats:
        return cls(
            collected=int(collected or 0),
            inserted=int(inserted or 0),
            skipped=int(skipped or 0),
            failed=int(failed or 0),
        )

    @property
    def ingest_success_rate(self) -> float | None:
        return ingest_success_rate(self.collected, self.inserted, self.skipped)

    @property
    def failure_rate(self) -> float | None:
        return failure_rate(self.collected, self.failed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "collected": self.collected,
            "inserted": self.inserted,
            "skipped": self.skipped,
            "failed": self.failed,
            "ingest_success_rate": _pct(self.ingest_success_rate),
            "failure_rate": _pct(self.failure_rate),
        }


def format_run_summary(label: str, stats: RunStats) -> str:
    """Human-readable one-line summary for logs.

    e.g. "📊 HIRING 수집 요약 | 수집 100 · 신규 70 · 중복/스킵 15 · 실패 15 |
          적재성공률 85.0% · 실패율 15.0%"
    """
    head = (
        f"📊 {label} 수집 요약 | 수집 {stats.collected} · 신규 {stats.inserted} · "
        f"중복/스킵 {stats.skipped} · 실패 {stats.failed}"
    )
    if stats.collected <= 0:
        return f"{head} | 데이터 없음"
    return (
        f"{head} | 적재성공률 {_pct(stats.ingest_success_rate):.1f}% · "
        f"실패율 {_pct(stats.failure_rate):.1f}%"
    )


def since_to_utc_start(value: Any | None) -> datetime | None:
    """Interpret a ``since`` day as KST midnight and return an aware UTC datetime.

    DB timestamps are TIMESTAMPTZ (stored UTC). A bare ``YYYY-MM-DD`` compared
    against them would be cast at the server timezone's midnight, dropping/
    misattributing the KST 00:00–09:00 window. We anchor the day in KST and
    convert, so ``started_at >= <result>`` means "from KST midnight of that day".

    None / "" → None (no lower bound).
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=_KST)
        return aware.astimezone(timezone.utc)
    if isinstance(value, date):
        day = value
    else:
        day = date.fromisoformat(str(value).strip()[:10])
    kst_midnight = datetime.combine(day, time.min, tzinfo=_KST)
    return kst_midnight.astimezone(timezone.utc)


def _pct(rate: float | None) -> float:
    """Rate [0,1] → percentage rounded to 1 dp; None → 0.0."""
    if rate is None:
        return 0.0
    return round(rate * 100, 1)
