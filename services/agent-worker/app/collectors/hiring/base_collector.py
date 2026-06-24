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
import json
import logging
import zoneinfo
from abc import ABC, abstractmethod
from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.observability import calculate_run_status
from app.utils.hash_utils import make_source_hash

_KST = zoneinfo.ZoneInfo("Asia/Seoul")


def _kst_today() -> datetime.date:
    """오늘 날짜(KST 기준). observed_date 등 '오늘' 경계를 DB 서버 tz(CURRENT_DATE)가 아니라
    한국시간 자정 기준으로 고정한다(#253). UTC 서버에서 KST 00:00~09:00 수집분이 전날로
    오분류되는 문제 방지."""
    return datetime.datetime.now(_KST).date()


def _to_kst_date(value: datetime.date | datetime.datetime | str) -> datetime.date:
    """observed_date override(날짜/일시/ISO 문자열) → KST 달력 날짜.

    backfill 등이 per-row ``observed_date``(실제 게시일)를 주입할 때 KST 자정 경계로
    정규화한다. tz-aware 일시는 KST 로 환산 후 ``.date()``, naive 일시/날짜는 그대로 쓴다.
    (datetime 은 date 의 하위형이라 datetime 검사를 먼저 한다.)"""
    if isinstance(value, datetime.datetime):
        return (value.astimezone(_KST) if value.tzinfo else value).date()
    if isinstance(value, datetime.date):
        return value
    text_value = str(value).strip()
    try:
        parsed = datetime.datetime.fromisoformat(text_value)
        return (parsed.astimezone(_KST) if parsed.tzinfo else parsed).date()
    except ValueError:
        return datetime.date.fromisoformat(text_value[:10])

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


@dataclass(frozen=True)
class CollectorResult:
    """크롤 결과 1건 — parse 대상 data + 격리용 원본 payload(선택) (Phase 4).

    하이브리드 점진 전환용 그릇. 크롤러가 dict 를 반환하면(legacy) raw_payload 가
    없고, CollectorResult 를 반환하면(new) 격리 시 원본 HTML/JSON 을 보존해
    selector 수정 후 replay-reparse(유실 제로 backfill)가 가능해진다.
    """
    data: dict
    raw_payload: str | None = None   # 원본 HTML/JSON. legacy 크롤러는 None.
    source_label: str | None = None  # replay-reparse 파서 매핑용(없으면 data['source_type'])


class _SkipRecord(Exception):
    """오류가 아닌 정상 스킵(중복·매핑 없음). savepoint 컨텍스트가 자동 롤백."""


# 플레이스홀더/문자열 오염 거부 — casefold 완전일치만(부분문자열 X → "Null Safety
# Engineer" 같은 정상 제목은 안전). JS 리터럴("null"/"undefined")·Python str(None)
# 오염은 깨진 SPA/꼬인 API 응답에서 실제로 흘러들어온다.
_PLACEHOLDER_TITLES = {"(제목 없음)", "null", "undefined", "none"}


def validation_failure_reason(data: dict) -> str | None:
    """필수 필드(company_name·job_title·job_link) 입력 검증 게이트.

    DB 진입 전에 빈/깨진 레코드를 걸러내기 위한 순수 함수. 위반 시 사유 문자열을,
    유효하면 None 을 반환한다. job_link 폴백 규칙은 _insert_one 과 동일(job_link →
    source_url)하게 맞춰 검증을 단일 지점으로 일원화한다.
    """
    if not (data.get("company_name") or "").strip():
        return "company_name 누락"
    title = (data.get("job_title") or "").strip()
    if not title or title.casefold() in _PLACEHOLDER_TITLES:
        return "job_title 누락/플레이스홀더"
    if not (data.get("job_link") or data.get("source_url") or "").strip():
        return "job_link 누락"
    return None


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
    def _get_current_quarter() -> str:
        """현재 날짜(KST 기준) → "Q1"~"Q4". UTC 서버에서도 한국 분기 기준으로 정확히 동작."""
        month = datetime.datetime.now(_KST).month
        return f"Q{(month - 1) // 3 + 1}"

    # ── stocks 매칭 ──────────────────────────────────────────────────────────────
    def _match_stock_row(self, db, raw_company_name: str) -> tuple[int, str] | None:
        """기업명 → (stock_id, sector). 매칭 실패 시 None. (로그/스킵 판단은 호출부 책임)

        단일 매칭 SQL의 Single Source of Truth. insert 단계 _resolve_stock 과
        수집 단계 _filter_registered 가 **반드시 이 헬퍼를 공유**해 매칭 의미가
        절대 갈라지지 않게 한다(수집단계 선거부가 insert 게이트보다 엄격/느슨해지면
        유효 공고 유실 또는 효과 상실 — #176).

        매칭 우선순위:
          정규화된 이름으로 정확/부분 일치 (ORDER BY CASE: 정확>부분, 짧은 이름 우선)

        이름(name)뿐 아니라 약칭(short_name)으로도 매칭한다. 잡코리아 등은 종목을
        약칭으로 표기하는 경우가 많다(예: stock 'HYBE' ↔ 공고 회사명 '하이브',
        'NAVER' ↔ '네이버'). short_name 이 NULL 인 종목은 ILIKE 결과가 NULL 이라
        자동으로 제외되므로 안전하다.

        is_target 조건을 두지 않는다 — stocks 에 존재하면 매칭한다(수집단계 필터가
        is_target 으로 좁히면 insert 게이트 대비 회귀가 되므로 동일하게 유지).
        """
        clean = self._clean_company_name(raw_company_name)

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
        return None

    def _resolve_stock(self, db, raw_company_name: str) -> tuple[int, str] | None:
        """
        기업명 → (stock_id, sector). 미등록은 스킵(None, 호출부에서 _SkipRecord).

        매칭은 _match_stock_row 에 위임(공유 SQL). DB가 Single Source of Truth:
          초기 데이터 → database/migrations/016_seed_stocks_targets.sql
          기업 추가   → INSERT INTO stocks ...
        """
        resolved = self._match_stock_row(db, raw_company_name)
        if resolved is None:
            logger.warning("⚠️  DB stocks 에 미등록 기업 → 스킵: %s",
                           self._clean_company_name(raw_company_name))
        return resolved

    def _filter_registered(self, db, company_names: set[str]) -> set[str]:
        """수집단계 선거부용: 후보 회사명 집합 → stocks 에 등록된 **원본** 회사명 집합.

        각 후보를 insert 단계와 동일한 _match_stock_row 로 판정하므로, 여기서 통과한
        레코드는 insert 단계 _resolve_stock 도 반드시 통과한다(89건 회귀 불가). 반환은
        호출부가 원본 레코드를 그대로 필터링할 수 있도록 입력받은 *원본 문자열*을 담는다.
        """
        registered: set[str] = set()
        for name in company_names:
            if self._match_stock_row(db, name) is not None:
                registered.add(name)
        return registered

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

        # job_link 빈값은 insert_to_db 의 검증 게이트(validation_failure_reason)가
        # DB 진입 전에 선방어한다 — 여기 도달하는 레코드는 job_link 가 보장된다.
        job_link = data.get("job_link") or data.get("source_url")

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
                "source_hash": make_source_hash(self.SOURCE_TYPE, seed),
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
            # 직군 수요 신호(자소설 duty-groups 등) — 소스에 없으면 None/[] 로 무해.
            "duty_groups": data.get("duty_groups"),
            "duty_group_ids": data.get("duty_group_ids"),
            "employment_page_url": data.get("employment_page_url"),
        }
        # observed_date: 기본은 오늘(KST). backfill 등이 per-row override(`observed_date`,
        # 실제 게시일)를 주입하면 그 날짜(KST)로 적재해 과거 시계열을 보존한다.
        # 라이브 수집은 override 없음 → _kst_today() 로 기존 거동 유지(#253).
        observed_override = data.get("observed_date")
        observed_date = (
            _to_kst_date(observed_override) if observed_override is not None else _kst_today()
        )
        db.execute(
            text("""
                INSERT INTO hiring_raw_details (
                    raw_document_id, stock_id, keyword, job_category,
                    job_count, observed_date, extra_payload
                )
                VALUES (
                    :raw_document_id, :stock_id, :keyword, :job_category,
                    :job_count, :observed_date, :extra_payload ::jsonb
                )
                ON CONFLICT (raw_document_id) DO NOTHING
            """),
            {
                "raw_document_id": int(raw_doc_id),
                "stock_id": stock_id,
                "keyword": (data.get("job_title") or "UNKNOWN")[:100],
                "job_category": sector,
                "job_count": 1,
                "observed_date": observed_date,
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

    # ── 격리 (Phase 4) ──────────────────────────────────────────────────────────
    def _quarantine_record(
        self, db, run_id: int | None, data: dict, raw_payload: str | None, reason: str
    ) -> None:
        """failed 로 거부/오류난 크롤 레코드를 hiring_quarantine 에 격리(best-effort).

        격리 INSERT 자체도 savepoint(begin_nested)로 감싼다 — 격리가 또 실패해도
        (payload 직렬화 등) 메인 트랜잭션·마지막 단일 commit 을 오염시키지 않는다.
        격리 실패는 로깅만 하고 수집 루프는 계속한다. 호출부는 트랜잭션이 usable
        상태(게이트=savepoint 진입 전, 오류=savepoint 자동 롤백 후)임을 보장한다.

        raw_payload 는 크롤러가 CollectorResult 로 원본을 실어 보낼 때만 채워지고,
        legacy(dict 반환) 크롤러는 NULL 이다(replay-data 는 record_payload 로 가능).
        """
        try:
            with db.begin_nested():
                db.execute(
                    text("""
                        INSERT INTO hiring_quarantine (
                            collector_run_id, source_type, source_label, company_name,
                            violation_reason, record_payload, raw_payload
                        )
                        VALUES (
                            :run_id, :stype, :label, :company,
                            :reason, :payload ::jsonb, :raw
                        )
                    """),
                    {
                        "run_id": run_id,
                        "stype": self.SOURCE_TYPE,
                        "label": data.get("source_type"),
                        "company": data.get("company_name"),
                        "reason": reason[:200],
                        "payload": json.dumps(data, ensure_ascii=False, default=str),
                        "raw": raw_payload,
                    },
                )
        except Exception as q_exc:
            logger.warning("⚠️  격리 INSERT 실패(무시): %s", q_exc)

    # ── 전체 적재 ──────────────────────────────────────────────────────────────
    def insert_to_db(self, parsed_data_list: list) -> int:
        """
        파싱 리스트 → DB 적재. Savepoint 격리 + 마지막 단일 commit.

        항목은 dict(legacy) 또는 (dict, raw_payload) 튜플(하이브리드, Phase 4)이다.
        failed 로 거부/오류난 레코드는 hiring_quarantine 에 격리한다(유실 방지).

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

            for item in parsed_data_list:
                # 하이브리드: (dict, raw_payload) 튜플 또는 dict(raw 없음) 정규화.
                data, raw_payload = item if isinstance(item, tuple) else (item, None)
                # ── 입력 검증 게이트: DB(savepoint) 진입 전 빈/깨진 레코드 거부 ──
                #    무효 레코드는 데이터 품질 결함이므로 failed 로 집계(중복/미등록
                #    benign skip 과 구분) → run status 가 partial/failed 로 떠 모니터링에
                #    노출된다. savepoint 진입 전이라 트랜잭션 비용도 없다.
                reason = validation_failure_reason(data)
                if reason:
                    failed += 1
                    logger.warning(
                        "🚫 무효 레코드 거부(%s) | company=%r title=%r link=%r",
                        reason, data.get("company_name"), data.get("job_title"),
                        data.get("job_link") or data.get("source_url"),
                    )
                    # 게이트는 savepoint 진입 전 → 트랜잭션 정상 상태에서 격리(Phase 4).
                    self._quarantine_record(db, run_id, data, raw_payload, reason)
                    continue
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
                    # _insert_one 의 SQL 오류는 begin_nested savepoint 가 자동 롤백 →
                    # 여기(except, savepoint 밖)선 트랜잭션이 usable 상태라 격리 안전
                    # (PostgreSQL InFailedSqlTransaction 함정 방어).
                    self._quarantine_record(
                        db, run_id, data, raw_payload, f"_insert_one: {exc}"
                    )

            # 모든 savepoint 처리 후 최종 상태 반영 + 단 1회 commit
            status = calculate_run_status(inserted, skipped, failed)
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
        # 하이브리드(Phase 4): collect()는 list[dict](legacy) 또는
        # list[CollectorResult](new)를 반환한다. parse 는 .data 에 적용하고, 격리용
        # 원본 payload 는 (parsed, raw_payload) 페어로 insert_to_db 까지 실어 보낸다.
        pairs: list[tuple[dict, str | None]] = []
        for item in raw:
            result = item if isinstance(item, CollectorResult) else CollectorResult(data=item)
            pairs.append((self.parse(result.data), result.raw_payload))
        logger.info("💾 DB 적재 중...")
        return self.insert_to_db(pairs)
