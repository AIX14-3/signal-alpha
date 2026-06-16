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
5. 기업명 정규화 → stocks 매칭 (ORDER BY CASE 우선순위)
   미등록 기업은 _SkipRecord 로 스킵 — DB가 Single Source of Truth
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

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

_KST = zoneinfo.ZoneInfo("Asia/Seoul")

# 라이브러리 모듈은 logging.basicConfig 를 호출하지 않는다(호스트 앱 로깅 설정을
# 덮어쓰는 부작용 방지). 로깅 핸들러/레벨 설정은 진입점(main.py, 파이프라인 스크립트)이 담당.
logger = logging.getLogger(__name__)


def get_target_companies(database_url: str) -> list[str]:
    """
    DB의 is_target=TRUE 기업 목록을 동적으로 반환 (Single Source of Truth).

    - 조회 성공 → DB 결과 반환
    - is_target=TRUE 행 없음 → 빈 리스트 반환 (경고 로그)
    - DB 연결 실패 등 예외 → 빈 리스트 반환 (에러 로그)

    기업 추가/제거는 SQL UPDATE 한 줄이면 충분하고 코드 수정이 불필요하다.
    (stocks 초기 데이터: database/migrations/016_seed_stocks_targets.sql)
    """
    engine = create_engine(database_url, echo=False, future=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT name FROM stocks WHERE is_target = TRUE ORDER BY name")
            ).fetchall()
        if rows:
            names = [row[0] for row in rows]
            logger.info("🎯 수집 대상 %d개 기업 (DB is_target=TRUE)", len(names))
            return names
        logger.warning("⚠️  is_target=TRUE 기업이 DB에 없습니다. 016_seed_stocks_targets.sql 을 실행하세요.")
        return []
    except Exception as exc:
        logger.error("❌ 수집 대상 기업 조회 실패: %s", exc)
        return []
    finally:
        engine.dispose()


class _SkipRecord(Exception):
    """오류가 아닌 정상 스킵(중복·매핑 없음). savepoint 컨텍스트가 자동 롤백."""


class BaseCollector(ABC):
    """채용 데이터 수집의 공통 인터페이스 (Strategy Pattern)."""

    # 매직 스트링 격리 — 상속 클래스에서 override 가능 (예: 'PATENT', 'DART')
    SOURCE_TYPE: str = "HIRING"
    COLLECTOR_VER: str = "1.0"

    def __init__(self, database_url: str):
        """
        Args:
            database_url: 예) postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha
        """
        self.database_url = database_url   # run() 에서 get_target_companies() 호출에 사용
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
    def collect(self, target_companies: list[str]) -> list:
        """원본 데이터 수집 (Mock: JSON 읽기 / Web: 크롤링).

        Args:
            target_companies: run()이 DB에서 조회해서 주입하는 수집 대상 기업 목록.
                              하위 클래스는 이 리스트만 순회하면 된다.
        """

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

    # ── stocks 매칭 ──────────────────────────────────────────────────────────────
    def _resolve_stock(self, db, raw_company_name: str) -> tuple[int, str] | None:
        """
        기업명 → (stock_id, sector).

        매칭 우선순위:
          1) 정규화된 이름으로 정확/부분 일치 (ORDER BY CASE: 정확>부분, 짧은 이름 우선)
          2) 일치 없음 → None 반환 (호출부에서 _SkipRecord)

        미등록 기업은 스킵한다. DB가 Single Source of Truth:
          초기 데이터 → database/migrations/016_seed_stocks_targets.sql
          기업 추가   → INSERT INTO stocks ...
        """
        clean = self._clean_company_name(raw_company_name)

        # 이름(name)뿐 아니라 약칭(short_name)으로도 매칭한다. 잡코리아 등은 종목을
        # 약칭으로 표기하는 경우가 많다(예: stock 'HYBE' ↔ 공고 회사명 '하이브',
        # 'NAVER' ↔ '네이버'). short_name 이 NULL 인 종목은 ILIKE 결과가 NULL 이라
        # 자동으로 제외되므로 안전하다.
        row = db.execute(
            text("""
                SELECT id, COALESCE(sector, '')
                FROM stocks
                WHERE name ILIKE :exact OR name ILIKE :like
                   OR short_name ILIKE :exact OR short_name ILIKE :like
                ORDER BY
                    CASE WHEN name ILIKE :exact OR short_name ILIKE :exact
                         THEN 0 ELSE 1 END,
                    LENGTH(name) ASC
                LIMIT 1
            """),
            {"exact": clean, "like": f"%{clean}%"},
        ).fetchone()

        if row:
            return int(row[0]), row[1]

        logger.warning("⚠️  DB stocks 에 미등록 기업 → 스킵: %s", clean)
        return None

    # ── collector_runs 라이프사이클 ─────────────────────────────────────────────
    def _create_collector_run(self, db) -> int:
        """collector_runs INSERT → id."""
        run_id = db.execute(
            text("""
                INSERT INTO collector_runs (collector_type, run_mode, status, started_at)
                VALUES (:ctype, 'manual', 'running', NOW())
                RETURNING id
            """),
            {"ctype": self.SOURCE_TYPE},
        ).scalar_one()
        logger.info("🏃 collector_run 생성: id=%s (type=%s)", run_id, self.SOURCE_TYPE)
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
            raise _SkipRecord
        stock_id, sector = resolved

        # 계절성 기준선 조회 — 테이블 미생성·DB 점검 등 어떤 이슈에도 크롤링 계속
        quarter = self._get_current_quarter()
        q_col = f"q{quarter[1]}_factor"
        seasonal_baseline: float | None = None
        try:
            # 중첩 savepoint 로 격리: hiring_baseline 미생성·조회 실패가 발생해도
            # 레코드의 외부 savepoint(트랜잭션)는 abort 되지 않고 INSERT 가 계속된다.
            with db.begin_nested():
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
                    :stock_id, :run_id, :stype, :source_name,
                    :external_id, :source_hash, :title, :source_url,
                    :published_at, 'success', :ver
                )
                ON CONFLICT (source_type, external_id) DO NOTHING
                RETURNING id
            """),
            {
                "stock_id": stock_id,
                "run_id": run_id,
                "stype": self.SOURCE_TYPE,
                "source_name": self._clean_company_name(data["company_name"])[:100],
                "external_id": job_link[:200],
                "source_hash": self._source_hash(seed),
                "title": data["job_title"],
                "source_url": job_link,
                "published_at": posting_date,
                "ver": self.COLLECTOR_VER,
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
                    job_count, observed_date, extra_payload
                )
                VALUES (
                    :raw_document_id, :stock_id, :keyword, :job_category,
                    :job_count, CURRENT_DATE, :extra_payload ::jsonb
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

        # 4) processing_queue 등록 (계약: raw_documents + detail + queue 를 한 트랜잭션에).
        #    이 INSERT 가 실패하면 begin_nested() savepoint 가 raw/detail 까지 롤백 →
        #    호출부에서 failed 로 집계. raw/detail 만 남고 큐가 없는 상태는 발생하지 않는다.
        #    재실행 시에는 raw_documents 가 ON CONFLICT 로 스킵(_SkipRecord)되어
        #    이 지점에 도달하지 않으므로 중복 큐도 생기지 않는다.
        db.execute(
            text("""
                INSERT INTO processing_queue (
                    stock_id, task_type, status, priority,
                    source_raw_ids, task_context
                )
                VALUES (
                    :stock_id, :task_type, 'pending', 'batch',
                    :source_raw_ids ::bigint[], :task_context ::jsonb
                )
            """),
            {
                "stock_id": stock_id,
                "task_type": f"NORMALIZE_{self.SOURCE_TYPE}",
                "source_raw_ids": [int(raw_doc_id)],
                "task_context": json.dumps(
                    {
                        "collector_run_id": run_id,
                        "source_type": self.SOURCE_TYPE,
                        "collector_ver": self.COLLECTOR_VER,
                    },
                    ensure_ascii=False,
                ),
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

            # 구조화 성공률 요약 (가시성 Phase 1) — collected = inserted+skipped+failed
            from app.observability import RunStats, format_run_summary

            run_stats = RunStats.from_counts(
                collected=inserted + skipped + failed,
                inserted=inserted,
                skipped=skipped,
                failed=failed,
            )
            logger.info("=" * 70)
            logger.info(format_run_summary(self.SOURCE_TYPE, run_stats))
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
        """
        아키텍처 완성: DB에서 수집 대상을 동적으로 조회해 수집 프로세스에 주입.

          1. get_target_companies() → DB is_target=TRUE 기업 목록
          2. collect(target_companies) → 하위 크롤러에 리스트 전달
          3. parse() → 표준 포맷 변환
          4. insert_to_db() → DB 적재
        """
        logger.info("🔄 데이터 수집 파이프라인 가동...")

        target_companies = get_target_companies(self.database_url)
        if not target_companies:
            logger.error("❌ 수집 대상 기업이 없어 파이프라인을 종료합니다. "
                         "016_seed_stocks_targets.sql 과 015_stocks_is_target.sql 을 실행하세요.")
            return 0

        raw = self.collect(target_companies)
        logger.info("📊 %d건 파싱 중...", len(raw))
        parsed = [self.parse(item) for item in raw]
        logger.info("💾 DB 적재 중...")
        return self.insert_to_db(parsed)
