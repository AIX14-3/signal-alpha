"""
mock_alternative_collector.py
개발용 채용공고 더미 데이터 적재 스크립트  (SQLAlchemy Core 버전)

signal-alpha ERD 3계층 구조에 맞게 INSERT합니다.

    collector_runs  (실행 로그)
        └─ raw_documents  (수집 원본 메타)
                └─ hiring_raw_details  (채용 상세 + JSONB)

─────────────────────────────────────────────────────────────
실제 ERD 컬럼 vs 흔한 오해 정정
─────────────────────────────────────────────────────────────
  raw_documents
    source_type  VARCHAR(20) CHECK IN ('DART','REPORT','HIRING','PATENT','DATALAB')
                 ← "document_type" 또는 "HIRING_POSTING" 아님
    published_at TIMESTAMPTZ
                 ← "published_date" 아님
    source_hash  VARCHAR(64) UNIQUE NOT NULL  ← SHA-256, 필수
    external_id  VARCHAR(200) NOT NULL        ← job_link 값
    source_name  VARCHAR(100) NOT NULL        ← company_name 값

  hiring_raw_details
    keyword      VARCHAR(100)  ← "keywords" 복수형 아님
    job_count    INTEGER       ← "hiring_count" 아님
    extra_payload JSONB NOT NULL ← 반드시 {} 이상 전달
─────────────────────────────────────────────────────────────

사전 조건:
  ① PostgreSQL 실행 중   : docker compose up postgres -d
  ② 마이그레이션 완료    : 001 ~ 004 SQL 파일 순서대로 적용
  ③ Fixture JSON 생성    : python database/seeds/alternative_raw_fixture.py

의존성 설치 (dev extras):
  pip install sqlalchemy psycopg2-binary

실행 (signal-alpha 루트에서):
  # 로컬 직접 연결 (Docker 외부)
  DATABASE_URL=postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha \\
  python services/agent-worker/tests/fixtures/mock_alternative_collector.py

  # Docker Compose 내부 서비스끼리 연결할 때는 호스트를 'postgres' 로 변경
  DATABASE_URL=postgresql://signal_alpha:signal_alpha_password@postgres:5432/signal_alpha
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text

# ─────────────────────────────────────────────────────────────────────────────
# 내부 전용 예외: 스킵 경로를 예외 흐름으로 통일
# (savepoint 컨텍스트 매니저가 자동 rollback 처리)
# ─────────────────────────────────────────────────────────────────────────────
class _SkipRecord(Exception):
    """처리는 건너뛰지만 오류가 아닌 경우 (중복·매핑 없음)."""


# ─────────────────────────────────────────────────────────────────────────────
# 종목 매핑 테이블
# stocks 테이블에 없는 기업은 이 매핑으로 자동 INSERT
# 형식: "기업명" → (ticker, market, sector)
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

# ─────────────────────────────────────────────────────────────────────────────
# 경로 설정
# ─────────────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[4]   # signal-alpha/ 루트
FIXTURE_PATH  = _PROJECT_ROOT / "database" / "seeds" / "alternative_raw_fixture.json"

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE_URL 기본값 (.env.example 기준 로컬 접속)
# Docker Compose 내부에서 실행할 때는 호스트를 'postgres' 로 변경
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_DATABASE_URL = (
    "postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha"
)


# ═════════════════════════════════════════════════════════════════════════════
# 헬퍼 함수
# ═════════════════════════════════════════════════════════════════════════════

def _source_hash(unique_key: str) -> str:
    """unique_key → SHA-256 hex 64자  (raw_documents.source_hash UNIQUE 충족)."""
    return hashlib.sha256(unique_key.encode("utf-8")).hexdigest()


def _load_fixture(path: Path) -> list[dict]:
    """Fixture JSON 로드. 미존재 시 친절한 안내 메시지 포함 예외."""
    if not path.exists():
        raise FileNotFoundError(
            f"Fixture 파일 없음: {path}\n"
            "먼저 실행하세요 →  python database/seeds/alternative_raw_fixture.py"
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)["seed_job_data"]


def _get_or_create_stock(conn, company_name: str) -> tuple[int, str] | None:
    """
    stocks 테이블에서 name 으로 조회 → (stock_id, sector) 반환.
    없으면 COMPANY_STOCK_MAP 으로 자동 INSERT.
    매핑에도 없으면 None 반환 → 호출부에서 _SkipRecord 발생.

    Note:
        stocks.name  ← 기업명 컬럼 (company_name 이 아님)
        stocks.ticker ← 고유 식별자
    """
    row = conn.execute(
        text("SELECT id, COALESCE(sector, '') FROM stocks WHERE name = :name"),
        {"name": company_name},
    ).fetchone()

    if row:
        return int(row[0]), row[1]

    # stocks 에 없음 → 자동 등록
    if company_name not in COMPANY_STOCK_MAP:
        return None

    ticker, market, sector = COMPANY_STOCK_MAP[company_name]
    row = conn.execute(
        text("""
            INSERT INTO stocks (ticker, name, market, sector)
            VALUES (:ticker, :name, :market, :sector)
            ON CONFLICT (ticker) DO UPDATE
                SET name   = EXCLUDED.name,
                    sector = EXCLUDED.sector
            RETURNING id, COALESCE(sector, '')
        """),
        {"ticker": ticker, "name": company_name, "market": market, "sector": sector},
    ).fetchone()
    print(f"  📋 stocks 자동 등록: {company_name} ({ticker} / {market})")
    return int(row[0]), row[1]


def _create_collector_run(conn) -> int:
    """
    collector_runs 에 실행 레코드를 INSERT하고 id 를 반환합니다.

    고정값:
        collector_type = 'HIRING'   (CHECK 제약: DART/REPORT/HIRING/PATENT/DATALAB/PRICE)
        run_mode       = 'manual'   (CHECK 제약: batch/immediate/manual)
        status         = 'running'
    """
    run_id = conn.execute(
        text("""
            INSERT INTO collector_runs (collector_type, run_mode, status, started_at)
            VALUES ('HIRING', 'manual', 'running', NOW())
            RETURNING id
        """)
    ).scalar_one()
    print(f"  🏃 collector_run 생성: id={run_id}")
    return int(run_id)


def _finish_collector_run(
    conn,
    run_id: int,
    status: str,
    inserted: int,
    skipped: int,
    failed: int,
    error_message: str | None = None,
) -> None:
    """collector_runs 레코드를 최종 상태로 업데이트합니다."""
    conn.execute(
        text("""
            UPDATE collector_runs
            SET status          = :status,
                finished_at     = NOW(),
                collected_count = :collected,
                inserted_count  = :inserted,
                skipped_count   = :skipped,
                failed_count    = :failed,
                error_message   = :error_msg
            WHERE id = :run_id
        """),
        {
            "status":    status,
            "collected": inserted + skipped + failed,
            "inserted":  inserted,
            "skipped":   skipped,
            "failed":    failed,
            "error_msg": error_message,
            "run_id":    run_id,
        },
    )


# ═════════════════════════════════════════════════════════════════════════════
# Step 1 · raw_documents INSERT
# ═════════════════════════════════════════════════════════════════════════════

def _insert_raw_document(
    conn,
    *,
    stock_id: int,
    run_id: int,
    company_name: str,
    job_link: str,
    job_title: str,
    posting_date: str,
    unique_key: str,
) -> int | None:
    """
    raw_documents 에 1건 INSERT.
    중복(ON CONFLICT)이면 None 반환.

    컬럼 매핑:
        source_type  = 'HIRING'       ← CHECK IN ('DART','REPORT','HIRING',...)
        source_name  = company_name   ← NOT NULL
        external_id  = job_link       ← UNIQUE(source_type, external_id)
        source_hash  = SHA-256(unique_key) ← UNIQUE
        title        = job_title
        source_url   = job_link
        published_at = posting_date   ← TIMESTAMPTZ  (NOT 'published_date')
    """
    row = conn.execute(
        text("""
            INSERT INTO raw_documents (
                stock_id,
                collector_run_id,
                source_type,
                source_name,
                external_id,
                source_hash,
                title,
                source_url,
                published_at,
                collect_status,
                collector_ver
            )
            VALUES (
                :stock_id,
                :run_id,
                'HIRING',
                :source_name,
                :external_id,
                :source_hash,
                :title,
                :source_url,
                :published_at,
                'success',
                '1.0'
            )
            ON CONFLICT (source_type, external_id) DO NOTHING
            RETURNING id
        """),
        {
            "stock_id":     stock_id,
            "run_id":       run_id,
            "source_name":  company_name,
            "external_id":  job_link,
            "source_hash":  _source_hash(unique_key),
            "title":        job_title,
            "source_url":   job_link,
            "published_at": posting_date,
        },
    ).fetchone()

    return int(row[0]) if row else None   # None → 중복


# ═════════════════════════════════════════════════════════════════════════════
# Step 2 · hiring_raw_details INSERT
# ═════════════════════════════════════════════════════════════════════════════

def _insert_hiring_detail(
    conn,
    *,
    raw_document_id: int,
    stock_id: int,
    job_title: str,
    sector: str,
    job: dict,
) -> None:
    """
    hiring_raw_details 에 1건 INSERT.

    컬럼 매핑:
        raw_document_id = 부모 raw_documents.id  (PK = FK)
        stock_id        = 복합 FK 구성용
        keyword         = job_title   ← "keywords" 복수 아님, VARCHAR(100)
        job_category    = sector      ← stocks.sector 값
        job_count       = 1           ← "hiring_count" 아님, 개별 공고 1건
        extra_payload   = JSONB dict  ← NOT NULL 필수

    extra_payload 구조:
        {
          "job_title":       "AI/머신러닝 연구원",
          "job_description": "...",
          "tech_stack":      ["Python", ...],
          "closing_date":    "2026-07-07T00:00:00",
          "story":           "AI/HBM 인력 채용 대폭 확대",
          "signal_strength": "high_volume",
          "unique_key":      "SEED_JOB_..."
        }
    """
    extra_payload = {
        "job_title":       job["job_title"],
        "job_description": job["job_description"],
        "tech_stack":      job["tech_stack"],
        "closing_date":    job["closing_date"],
        "story":           job["story"],
        "signal_strength": job["signal_strength"],
        "unique_key":      job["unique_key"],
    }

    conn.execute(
        text("""
            INSERT INTO hiring_raw_details (
                raw_document_id,
                stock_id,
                keyword,
                job_category,
                job_count,
                extra_payload
            )
            VALUES (
                :raw_document_id,
                :stock_id,
                :keyword,
                :job_category,
                :job_count,
                :extra_payload ::jsonb
            )
            ON CONFLICT (raw_document_id) DO NOTHING
        """),
        {
            "raw_document_id": raw_document_id,
            "stock_id":        stock_id,
            "keyword":         job_title[:100],         # VARCHAR(100) 잘라내기
            "job_category":    sector,
            "job_count":       1,                       # 개별 공고 1건
            "extra_payload":   json.dumps(extra_payload, ensure_ascii=False),
        },
    )


# ═════════════════════════════════════════════════════════════════════════════
# 레코드 단위 처리 (savepoint 컨텍스트 매니저 패턴)
# ═════════════════════════════════════════════════════════════════════════════

def _process_one_job(
    conn,
    job: dict,
    run_id: int,
) -> tuple[str, str]:
    """
    레코드 1건을 savepoint 컨텍스트 매니저로 처리합니다.

    SQLAlchemy 2.0 `with conn.begin_nested():` 동작:
        - 정상 탈출 → RELEASE SAVEPOINT  (변경 보존, 외부 트랜잭션에 반영)
        - 예외 발생 → ROLLBACK TO SAVEPOINT  (이 건만 취소, 외부 트랜잭션 유지)

    수동 savepoint.commit() / savepoint.rollback() 을 쓰지 않는 이유:
        - try 블록 내 수동 rollback 후 continue 시 savepoint 객체가 dangling 상태
        - except 블록에서 이미 롤백된 savepoint를 재사용할 위험
        - 루프 내 conn.commit() 호출로 외부 트랜잭션 경계가 붕괴되는 문제
        → 컨텍스트 매니저가 이 모든 상태 관리를 자동으로 처리

    Returns:
        ("inserted" | "skipped" | "failed", 사람이 읽을 수 있는 메시지)
    """
    company_name = job["company_name"]
    job_title    = job["job_title"]
    job_link     = job["job_link"]
    posting_date = job["posting_date"]
    unique_key   = job["unique_key"]

    try:
        with conn.begin_nested():   # ← savepoint 시작
            # ── Step 0: stock_id 확보 ─────────────────────────────────────
            stock_result = _get_or_create_stock(conn, company_name)
            if stock_result is None:
                # COMPANY_STOCK_MAP 미등록 → skip
                # _SkipRecord 는 begin_nested 블록 밖에서 잡히므로
                # 자동 rollback 후 skipped 카운트로 분류됨
                raise _SkipRecord(f"매핑 없음 '{company_name}'")
            stock_id, sector = stock_result

            # ── Step 1: raw_documents INSERT ──────────────────────────────
            raw_doc_id = _insert_raw_document(
                conn,
                stock_id=stock_id,
                run_id=run_id,
                company_name=company_name,
                job_link=job_link,
                job_title=job_title,
                posting_date=posting_date,
                unique_key=unique_key,
            )
            if raw_doc_id is None:
                # ON CONFLICT DO NOTHING → 이미 존재
                raise _SkipRecord(f"중복 '{job_link}'")

            # ── Step 2: hiring_raw_details INSERT ─────────────────────────
            _insert_hiring_detail(
                conn,
                raw_document_id=raw_doc_id,
                stock_id=stock_id,
                job_title=job_title,
                sector=sector,
                job=job,
            )

        # with 블록 정상 탈출 → RELEASE SAVEPOINT 자동 실행
        return "inserted", f"✓  [{company_name}] {job_title}"

    except _SkipRecord as e:
        # with 블록 예외 탈출 → ROLLBACK TO SAVEPOINT 자동 실행
        label = "⊘  중복 스킵" if "중복" in str(e) else f"⊘  스킵"
        return "skipped", f"{label}: [{company_name}] {job_title}"

    except Exception as exc:
        # with 블록 예외 탈출 → ROLLBACK TO SAVEPOINT 자동 실행
        # 이 시점에서 외부 트랜잭션은 온전히 유지됨
        err = str(exc)
        if "foreign key" in err.lower():
            msg = (
                f"✗  FK 오류: [{company_name}] {job_title}\n"
                f"     → stocks 테이블에 '{company_name}' 없거나 복합 FK 불일치"
            )
        else:
            msg = f"✗  실패: [{company_name}] {job_title}\n     → {exc}"
        return "failed", msg


# ═════════════════════════════════════════════════════════════════════════════
# 메인 적재 함수
# ═════════════════════════════════════════════════════════════════════════════

def insert_mock_alternative_data(database_url: str | None = None) -> int:
    """
    Fixture JSON → collector_runs / raw_documents / hiring_raw_details INSERT.

    트랜잭션 전략:
        ① collector_run 생성 → 즉시 conn.commit()
              (이후 레코드 실패와 분리, 실행 이력은 항상 남김)
        ② 각 레코드: with conn.begin_nested() 컨텍스트 매니저
              성공 → RELEASE SAVEPOINT (외부 트랜잭션에 누적)
              실패 → ROLLBACK TO SAVEPOINT (이 건만 취소, 나머지 유지)
        ③ 모든 레코드 처리 후 → 단일 conn.commit()
              (성공한 모든 레코드를 한 번에 영구 반영)
        ④ 예외 시 → conn.rollback() + collector_run status='failed'

    루프 내에서 conn.commit() / conn.rollback() 을 호출하지 않는 이유:
        - conn.commit() 호출 시 외부 트랜잭션이 종료되어
          이후 begin_nested() 가 새 트랜잭션을 시작하므로
          "성공 레코드를 한 덩어리로 커밋" 하는 원자성 보장이 불가
        - conn.rollback() 호출 시 savepoint 로 보호된 이전 성공 레코드까지
          모두 취소되는 치명적 데이터 손실 발생

    Returns:
        inserted_count  신규 적재 건수
    """
    db_url = database_url or os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)

    print("\n" + "=" * 70)
    print("🚀 Mock Alternative Collector 시작  [SQLAlchemy Core]")
    print("=" * 70)
    safe_url = db_url.split("@")[-1] if "@" in db_url else db_url
    print(f"  DB   : ...@{safe_url}")
    print(f"  파일 : {FIXTURE_PATH}")

    jobs = _load_fixture(FIXTURE_PATH)
    print(f"  레코드: {len(jobs)}개 로드\n")

    engine = create_engine(db_url, echo=False)

    inserted_count = 0
    skipped_count  = 0
    failed_count   = 0
    final_status   = "success"
    run_id: int | None = None

    with engine.connect() as conn:
        try:
            # ── ① collector_run 생성 후 즉시 커밋 ──────────────────────────
            # 이후 레코드 처리와 분리: 실행 이력은 항상 DB에 남는다
            run_id = _create_collector_run(conn)
            conn.commit()

            # ── ② 레코드별 savepoint 처리 ───────────────────────────────────
            for job in jobs:
                outcome, message = _process_one_job(conn, job, run_id)
                print(f"  {message}")
                if outcome == "inserted":
                    inserted_count += 1
                elif outcome == "skipped":
                    skipped_count += 1
                else:
                    failed_count += 1

            # ── ③ 최종 단일 커밋 ────────────────────────────────────────────
            # 성공한 모든 레코드(RELEASE SAVEPOINT 된 것들)를 한꺼번에 영구 반영
            final_status = "success" if failed_count == 0 else "partial"
            _finish_collector_run(
                conn, run_id, final_status,
                inserted_count, skipped_count, failed_count,
            )
            conn.commit()

        except Exception as exc:
            # ── ④ 전체 실패: 외부 트랜잭션 전체 롤백 ───────────────────────
            conn.rollback()
            final_status = "failed"
            if run_id is not None:
                try:
                    # collector_run 만큼은 실패 상태로 기록 (별도 커밋)
                    _finish_collector_run(
                        conn, run_id, "failed",
                        inserted_count, skipped_count, failed_count,
                        str(exc),
                    )
                    conn.commit()
                except Exception:
                    pass
            print(f"\n❌ 전체 실패: {exc}")
            raise

    # ── 결과 출력 ────────────────────────────────────────────────────────────
    total = inserted_count + skipped_count + failed_count
    print("\n" + "=" * 70)
    print("✅ Mock Alternative Collector 완료!")
    print("=" * 70)
    print(f"  총 처리    : {total}개")
    print(f"  ✓ 신규 적재: {inserted_count}개")
    print(f"  ⊘ 중복 스킵: {skipped_count}개")
    print(f"  ✗ 실패     : {failed_count}개")
    print(f"  상태       : {final_status.upper()}")
    print("=" * 70)
    print(
        "\n🔍 검증 SQL:\n"
        "  SELECT id, status, inserted_count\n"
        "    FROM collector_runs WHERE collector_type='HIRING'\n"
        "    ORDER BY id DESC LIMIT 3;\n\n"
        "  SELECT r.title, r.published_at, h.keyword, h.job_category\n"
        "    FROM raw_documents r\n"
        "    JOIN hiring_raw_details h ON h.raw_document_id = r.id\n"
        "   WHERE r.source_type = 'HIRING'\n"
        "   LIMIT 5;\n"
    )
    return inserted_count


# ─────────────────────────────────────────────────────────────────────────────
# 직접 실행
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    insert_mock_alternative_data()
