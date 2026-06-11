"""
base_collector.py
모든 채용 수집기의 부모 클래스 (Strategy Pattern 의 공통 로직)

핵심 기능
1. SQLAlchemy ORM Session 기반 DB 연결
2. signal-alpha 실제 ERD 3계층 적재
       collector_runs (실행 로그)
         └─ raw_documents (수집 원본 메타)
              └─ hiring_raw_details (채용 상세 + JSONB)
3. Savepoint(begin_nested)로 개별 레코드 에러 격리
   - 한 건 실패/스킵 → 해당 savepoint 만 롤백, 나머지 계속
   - 모든 레코드 누적 후 마지막에 단 1회 commit (재시도/재커밋 버그 없음)
4. ON CONFLICT DO NOTHING 으로 중복 방지
5. 기업명 정규화 → stocks 매칭 (ORDER BY CASE 우선순위) → COMPANY_STOCK_MAP 폴백
6. json.dumps(ensure_ascii=False) + ::jsonb 캐스팅으로 한글 안전 직렬화

─────────────────────────────────────────────────────────────────────────────
실제 ERD 컬럼 (database/migrations/004_collection_raw.sql 기준)
─────────────────────────────────────────────────────────────────────────────
  raw_documents
    stock_id          BIGINT NOT NULL  FK→stocks(id)
    collector_run_id  BIGINT           FK→collector_runs(id)
    source_type       VARCHAR(20) CHECK IN ('DART','REPORT','HIRING','PATENT','DATALAB')
    source_name       VARCHAR(100) NOT NULL   ← 기업명
    external_id       VARCHAR(200) NOT NULL   ← job_link
    source_hash       VARCHAR(64) UNIQUE NOT NULL  ← SHA-256
    title             TEXT NOT NULL           ← 직무명
    source_url        TEXT
    published_at      TIMESTAMPTZ NOT NULL    ← 'published_date' 아님
    UNIQUE (source_type, external_id)

  hiring_raw_details
    raw_document_id   BIGINT PK = FK
    stock_id          BIGINT NOT NULL  (복합 FK 구성)
    keyword           VARCHAR(100)            ← 'keywords' 아님
    job_category      VARCHAR(100)
    job_count         INTEGER                 ← 'hiring_count' 아님
    extra_payload     JSONB NOT NULL
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import zoneinfo
from abc import ABC, abstractmethod

_KST = zoneinfo.ZoneInfo("Asia/Seoul")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# stocks 미등록 기업 자동 등록용 매핑: "기업명" → (ticker, market, sector)
# ─────────────────────────────────────────────────────────────────────────────
COMPANY_STOCK_MAP: dict[str, tuple[str, str, str]] = {
    "삼성전자":         ("005930", "KOSPI",  "반도체"),
    "SK하이닉스":       ("000660", "KOSPI",  "반도체"),
    "한미반도체":       ("042700", "KOSPI",  "반도체장비"),
    "NAVER":            ("035420", "KOSPI",  "인터넷"),
    "카카오":           ("035720", "KOSPI",  "인터넷"),
    "크래프톤":         ("259960", "KOSPI",  "게임"),
    "현대자동차":       ("005380", "KOSPI",  "자동차"),
    "기아":             ("000270", "KOSPI",  "자동차"),
    "HL만도":           ("204320", "KOSPI",  "자동차부품"),
    "HYBE":             ("352820", "KOSPI",  "엔터"),
    "SM엔터테인먼트":   ("041510", "KOSPI",  "엔터"),
    "스튜디오드래곤":   ("253450", "KOSDAQ", "콘텐츠"),
    "삼성바이오로직스": ("207940", "KOSPI",  "바이오"),
    "셀트리온":         ("068270", "KOSPI",  "바이오"),
    "유한양행":         ("000100", "KOSPI",  "제약"),
}


class _SkipRecord(Exception):
    """오류가 아닌 정상 스킵(중복·매핑 없음). savepoint 컨텍스트가 자동 롤백."""


class BaseCollector(ABC):
    """채용 데이터 수집의 공통 인터페이스 (Strategy Pattern)."""

    def __init__(self, database_url: str):
        """
        Args:
            database_url: 예) postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha
        """
        self.engine = create_engine(database_url, echo=False, future=True)
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
            future=True,
        )
        safe = database_url.split("@")[-1] if "@" in database_url else database_url
        logger.info("✅ DB 엔진 생성 완료 (...@%s)", safe)

    # ── 하위 클래스가 구현 ────────────────────────────────────────────────────
    @abstractmethod
    def collect(self) -> list:
        """원본 데이터 수집 (Mock: JSON 읽기 / Web: 크롤링)."""

    @abstractmethod
    def parse(self, raw_data) -> dict:
        """수집 데이터 → 표준 포맷 dict 변환.

        반환 dict 표준 키:
            company_name, job_title, job_description, closing_date,
            source_url, job_link, unique_key, tech_stack,
            story, signal_strength, posting_date, source_type(라벨)
        """

    # ── 공통 유틸 ──────────────────────────────────────────────────────────────
    @staticmethod
    def _clean_company_name(raw_name: str) -> str:
        """기업명 정규화. '(주)카카오'→'카카오', '주식회사 네이버'→'네이버'."""
        cleaned = (raw_name or "").strip()
        for pattern in ["(주)", "주식회사", "㈜", "(유)", "(재)"]:
            cleaned = cleaned.replace(pattern, "")
        return " ".join(cleaned.split()).strip()  # 연속 공백 1칸으로

    @staticmethod
    def _source_hash(seed: str) -> str:
        """seed(unique_key 또는 job_link) → SHA-256 hex 64자. source_hash UNIQUE 충족."""
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    @staticmethod
    def _get_current_quarter() -> str:
        """현재 날짜(KST 기준) → "Q1"~"Q4". UTC 서버에서도 한국 분기 기준으로 정확히 동작."""
        month = datetime.datetime.now(_KST).month
        return f"Q{(month - 1) // 3 + 1}"

    # ── stocks 매칭 / 자동 등록 ─────────────────────────────────────────────────
    def _resolve_stock(self, db, raw_company_name: str) -> tuple[int, str] | None:
        """
        기업명 → (stock_id, sector).

        매칭 우선순위
          1) 정규화된 이름으로 정확/부분 일치 (ORDER BY CASE: 정확>부분, 짧은 이름 우선)
          2) COMPANY_STOCK_MAP 폴백 → stocks 자동 INSERT
          3) 둘 다 실패 → None (호출부에서 _SkipRecord)
        """
        clean = self._clean_company_name(raw_company_name)

        row = db.execute(
            text("""
                SELECT id, COALESCE(sector, '')
                FROM stocks
                WHERE name ILIKE :exact OR name ILIKE :like
                ORDER BY
                    CASE WHEN name ILIKE :exact THEN 0 ELSE 1 END,
                    LENGTH(name) ASC
                LIMIT 1
            """),
            {"exact": clean, "like": f"%{clean}%"},
        ).fetchone()

        if row:
            return int(row[0]), row[1]

        # 폴백: 매핑 테이블로 자동 등록
        if clean not in COMPANY_STOCK_MAP:
            return None

        ticker, market, sector = COMPANY_STOCK_MAP[clean]
        row = db.execute(
            text("""
                INSERT INTO stocks (ticker, name, market, sector)
                VALUES (:ticker, :name, :market, :sector)
                ON CONFLICT (ticker) DO UPDATE
                    SET name = EXCLUDED.name, sector = EXCLUDED.sector
                RETURNING id, COALESCE(sector, '')
            """),
            {"ticker": ticker, "name": clean, "market": market, "sector": sector},
        ).fetchone()
        logger.info("📋 stocks 자동 등록: %s (%s / %s)", clean, ticker, market)
        return int(row[0]), row[1]

    # ── collector_runs 라이프사이클 ─────────────────────────────────────────────
    def _create_collector_run(self, db) -> int:
        """collector_runs INSERT → id. collector_type='HIRING', run_mode='manual'."""
        run_id = db.execute(
            text("""
                INSERT INTO collector_runs (collector_type, run_mode, status, started_at)
                VALUES ('HIRING', 'manual', 'running', NOW())
                RETURNING id
            """)
        ).scalar_one()
        logger.info("🏃 collector_run 생성: id=%s", run_id)
        return int(run_id)

    def _finish_collector_run(
        self, db, run_id: int, status: str,
        inserted: int, skipped: int, failed: int, error_message: str | None = None,
    ) -> None:
        """collector_runs 최종 상태 업데이트."""
        db.execute(
            text("""
                UPDATE collector_runs
                SET status = :status, finished_at = NOW(),
                    collected_count = :collected, inserted_count = :inserted,
                    skipped_count = :skipped, failed_count = :failed,
                    error_message = :err
                WHERE id = :run_id
            """),
            {
                "status": status, "collected": inserted + skipped + failed,
                "inserted": inserted, "skipped": skipped, "failed": failed,
                "err": error_message, "run_id": run_id,
            },
        )

    # ── 단일 레코드 적재 (savepoint 안에서 호출) ────────────────────────────────
    def _insert_one(self, db, data: dict, run_id: int) -> None:
        """
        raw_documents + hiring_raw_details 1건 적재.
        중복/매핑없음은 _SkipRecord 로 신호 → 호출부 savepoint 가 롤백.
        """
        # 1) stock 매칭
        resolved = self._resolve_stock(db, data["company_name"])
        if resolved is None:
            logger.warning("⚠️  미등록 기업 스킵: %s", data["company_name"])
            raise _SkipRecord
        stock_id, sector = resolved

        # 계절성 기준선 조회 — 테이블 미생성·DB 점검 등 어떤 이슈에도 크롤링 계속
        quarter = self._get_current_quarter()
        q_col = f"q{quarter[1]}_factor"
        seasonal_baseline: float | None = None
        try:
            baseline_row = db.execute(
                text(
                    "SELECT avg_search_volume, q1_factor, q2_factor, q3_factor, q4_factor "
                    "FROM hiring_baseline WHERE stock_id = :sid"
                ),
                {"sid": stock_id},
            ).fetchone()
            if baseline_row:
                # SQLAlchemy 2.0 Row: _mapping.get()으로 문자열 변수 기반 컬럼 조회
                q_factor = baseline_row._mapping.get(q_col) or 1.0
                seasonal_baseline = float(baseline_row._mapping["avg_search_volume"]) * float(q_factor)
        except Exception as _baseline_err:
            logger.warning("⚠️  hiring_baseline 조회 실패 (계절 가중치 스킵): %s", _baseline_err)

        job_link = data.get("job_link") or data.get("source_url")
        if not job_link:
            logger.warning("⚠️  job_link 없음 스킵: %s", data.get("job_title"))
            raise _SkipRecord

        # published_at NOT NULL 방어: posting_date 누락 시 수집 시각으로 대체
        posting_date = data.get("posting_date") or datetime.datetime.now(_KST).isoformat()

        # source_hash seed: unique_key 우선, 없으면 job_link
        seed = data.get("unique_key") or job_link

        # 2) raw_documents INSERT (UNIQUE(source_type, external_id) 충돌 시 DO NOTHING)
        raw_doc_id = db.execute(
            text("""
                INSERT INTO raw_documents (
                    stock_id, collector_run_id, source_type, source_name,
                    external_id, source_hash, title, source_url,
                    published_at, collect_status, collector_ver
                )
                VALUES (
                    :stock_id, :run_id, 'HIRING', :source_name,
                    :external_id, :source_hash, :title, :source_url,
                    :published_at, 'success', '1.0'
                )
                ON CONFLICT (source_type, external_id) DO NOTHING
                RETURNING id
            """),
            {
                "stock_id": stock_id,
                "run_id": run_id,
                "source_name": self._clean_company_name(data["company_name"])[:100],
                "external_id": job_link[:200],
                "source_hash": self._source_hash(seed),
                "title": data["job_title"],
                "source_url": job_link,
                "published_at": posting_date,
            },
        ).scalar()

        if raw_doc_id is None:
            logger.info("ℹ️  중복 공고 스킵: %s - %s",
                        data["company_name"], data.get("job_title"))
            raise _SkipRecord

        # 3) hiring_raw_details INSERT
        extra_payload = {
            "job_title": data.get("job_title"),
            "job_description": data.get("job_description"),
            "tech_stack": data.get("tech_stack", []),
            "closing_date": data.get("closing_date"),
            "story": data.get("story"),
            "signal_strength": data.get("signal_strength"),
            "source_type": data.get("source_type"),   # 논리 라벨 (SEED_JOB / WEB ...)
            "unique_key": data.get("unique_key"),
            "quarter": quarter,
            "seasonal_baseline": seasonal_baseline,
        }
        db.execute(
            text("""
                INSERT INTO hiring_raw_details (
                    raw_document_id, stock_id, keyword, job_category,
                    job_count, extra_payload
                )
                VALUES (
                    :raw_document_id, :stock_id, :keyword, :job_category,
                    :job_count, :extra_payload ::jsonb
                )
                ON CONFLICT (raw_document_id) DO NOTHING
            """),
            {
                "raw_document_id": int(raw_doc_id),
                "stock_id": stock_id,
                "keyword": (data.get("job_title") or "UNKNOWN")[:100],
                "job_category": sector,
                "job_count": 1,
                "extra_payload": json.dumps(extra_payload, ensure_ascii=False),
            },
        )
        logger.info("✓ [%s] %s",
                    self._clean_company_name(data["company_name"]), data.get("job_title"))

    # ── 전체 적재 ──────────────────────────────────────────────────────────────
    def insert_to_db(self, parsed_data_list: list[dict]) -> int:
        """
        파싱 리스트 → DB 적재. Savepoint 격리 + 마지막 단일 commit.

        Returns:
            inserted_count (신규 적재 건수)
        """
        db = self.SessionLocal()
        inserted = skipped = failed = 0
        run_id: int | None = None

        try:
            # collector_run 선(先) 생성 후 커밋 → 이후 레코드 실패와 분리
            run_id = self._create_collector_run(db)
            db.commit()

            for data in parsed_data_list:
                try:
                    # ── Savepoint: 이 한 건만 격리 ──
                    with db.begin_nested():
                        self._insert_one(db, data, run_id)
                    inserted += 1
                except _SkipRecord:
                    skipped += 1            # savepoint 자동 롤백됨
                except Exception as exc:    # 진짜 오류
                    failed += 1
                    logger.error("❌ %s: %s", data.get("company_name"), exc)

            # 모든 savepoint 처리 후 최종 상태 반영 + 단 1회 commit
            status = "success" if failed == 0 else "partial"
            self._finish_collector_run(db, run_id, status, inserted, skipped, failed)
            db.commit()

            logger.info("=" * 70)
            logger.info("✅ 적재 완료 | 신규 %d · 중복/스킵 %d · 실패 %d (상태 %s)",
                        inserted, skipped, failed, status.upper())
            logger.info("=" * 70)
            return inserted

        except Exception as exc:
            db.rollback()
            logger.error("❌ DB 처리 실패: %s", exc)
            if run_id is not None:
                try:
                    self._finish_collector_run(
                        db, run_id, "failed", inserted, skipped, failed, str(exc))
                    db.commit()
                except Exception:
                    db.rollback()
            raise
        finally:
            db.close()

    # ── 실행 흐름 ──────────────────────────────────────────────────────────────
    def run(self) -> int:
        """collect → parse → insert_to_db."""
        logger.info("🔄 데이터 수집 시작...")
        raw = self.collect()
        logger.info("📊 %d건 파싱 중...", len(raw))
        parsed = [self.parse(item) for item in raw]
        logger.info("💾 DB 적재 중...")
        return self.insert_to_db(parsed)
