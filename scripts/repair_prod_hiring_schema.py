"""prod hiring 스키마 드리프트 교정 — 빠진 hiring 객체만 baseline 정의대로 생성.

배경
----
prod DB 는 "마이그레이션 하나가 빠진" 상태가 아니라 **옛 베이스라인에서 만들어져 드리프트된**
상태다(`schema_migrations` 원장이 비어 있음). 그 결과 hiring 테이블 9개 중 3개
(`hiring_baseline` / `hiring_quarantine` / `hiring_raw_details`)만 존재하고, 나머지가 없다.

이 결손이 런타임에 미치는 영향(둘 다 except 가 삼켜 경고만 남고 조용히 스킵된다):
  - `hiring_sources` 부재 → `_load_source_specs()` 예외 → **공식 채용사이트 크롤러 15개 전부 스킵**
  - `hiring_portal_company_ids` 부재 → `_load_jobkorea_company_ids()` 예외 →
    잡코리아 회원번호 직접수집(노이즈 0·Selenium 불요) 경로가 죽고 전 종목 키워드 폴백

왜 마이그레이션 파일이 아니라 스크립트인가
----------------------------------------
이 테이블들은 **이미 `migrations/0003_collection_baseline.sql` 에 정의돼 있다.** 새 타임스탬프
마이그레이션에 `CREATE TABLE` 을 또 넣으면 깨끗한 DB 에서 중복 정의로 깨진다(database/README.md §3
가 경고하는 report_chunks 이중 정의 사고와 동일 패턴). 원장 전체 재정합은 hiring 밖 테이블까지
건드리는 팀 결정 사항이다.

→ 그래서 이 스크립트는 마이그레이션이 아니라 **prod 를 migrations/ 정의에 맞추는 일회성 교정 도구**다.
   스키마의 소스 오브 트루스는 여전히 `database/migrations/` 이고, 아래 DDL 은 거기서 그대로 발췌했다.

안전장치
--------
  - 기본은 **dry-run**. 실제 적용은 `--yes` 필요.
  - 객체별로 존재 여부를 먼저 확인하고 **없는 것만** 생성(있으면 skip 로그) → 멱등.
    깨끗한 DB(마이그 정상 적용)에 돌리면 전부 skip 되고 아무것도 안 바뀐다.
  - 전 과정 **단일 트랜잭션**. 하나라도 실패하면 전체 롤백.
  - `schema_migrations` 원장은 **건드리지 않는다**. 기존 hiring 테이블 3개도 변경하지 않는다.

사용
----
    # 무엇이 생성될지 확인 (아무것도 안 바꿈)
    uv run python scripts/repair_prod_hiring_schema.py

    # 실제 적용
    uv run python scripts/repair_prod_hiring_schema.py --yes

    # DATABASE_URL 직접 지정
    uv run python scripts/repair_prod_hiring_schema.py --database-url postgresql://... --yes

DDL 출처
--------
  ENUM  hiring_crawler_type            : database/migrations/0002_published_baseline.sql:11
  TABLE hiring_job_function_stocks     : database/migrations/0003_collection_baseline.sql:800
  TABLE hiring_job_functions           : 0003:813
  TABLE hiring_portal_company_ids      : 0003:849
  TABLE hiring_search_trend            : 0003:947
  TABLE hiring_signals                 : 0003:984
  TABLE hiring_sources                 : 0003:1023
  (+ 각 시퀀스·PK·uq_*·idx_*·FK·updated_at 트리거는 같은 파일의 대응 섹션에서 발췌)

시드
----
적용 후 `database/seeds/` 의 hiring 시드 3종을 실행한다(전부 ON CONFLICT 기반 재실행 안전):
  005_seed_hiring_sources.sql        공식 채용사이트 15종목
  006_seed_hiring_job_functions.sql  직무 분류 + 종목 매핑(sector_demand 신호)
  007_seed_jobkorea_company_ids.sql  잡코리아 회원번호 매핑
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
SEEDS_DIR = REPO_ROOT / "database" / "seeds"

load_dotenv(REPO_ROOT / ".env")

# 적용 후 실행할 hiring 시드(순서 유의: 테이블 생성 → 시드).
SEED_FILES = (
    "005_seed_hiring_sources.sql",
    "006_seed_hiring_job_functions.sql",
    "007_seed_jobkorea_company_ids.sql",
)

# ── DDL 블록 ─────────────────────────────────────────────────────────────────
# 각 항목: (종류, 이름, DDL). DDL 은 baseline 에서 그대로 발췌했다(손으로 쓰지 않음).
# 종류는 존재 확인 방법을 고른다: 'type' → pg_type, 'table' → information_schema.tables.

_ENUM_HIRING_CRAWLER_TYPE = """
CREATE TYPE public.hiring_crawler_type AS ENUM (
    'portal_saramin',
    'portal_jobkorea',
    'official_api',
    'official_selenium',
    'recruiter_kr',
    'simple_site'
);
"""

_TBL_HIRING_JOB_FUNCTIONS = """
CREATE TABLE public.hiring_job_functions (
    id bigint NOT NULL,
    function_key character varying(40) NOT NULL,
    label character varying(100) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.hiring_job_functions_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.hiring_job_functions_id_seq OWNED BY public.hiring_job_functions.id;
ALTER TABLE ONLY public.hiring_job_functions
    ALTER COLUMN id SET DEFAULT nextval('public.hiring_job_functions_id_seq'::regclass);

ALTER TABLE ONLY public.hiring_job_functions
    ADD CONSTRAINT hiring_job_functions_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.hiring_job_functions
    ADD CONSTRAINT hiring_job_functions_function_key_key UNIQUE (function_key);

CREATE TRIGGER trg_hiring_job_functions_updated_at BEFORE UPDATE ON public.hiring_job_functions
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();
"""

# hiring_job_functions 에 의존(FK) → 반드시 그 뒤에 생성.
_TBL_HIRING_JOB_FUNCTION_STOCKS = """
CREATE TABLE public.hiring_job_function_stocks (
    job_function_id bigint NOT NULL,
    stock_id bigint NOT NULL,
    weight numeric(4,2) DEFAULT 1.0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.hiring_job_function_stocks
    ADD CONSTRAINT hiring_job_function_stocks_pkey PRIMARY KEY (job_function_id, stock_id);
ALTER TABLE ONLY public.hiring_job_function_stocks
    ADD CONSTRAINT hiring_job_function_stocks_job_function_id_fkey
    FOREIGN KEY (job_function_id) REFERENCES public.hiring_job_functions(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.hiring_job_function_stocks
    ADD CONSTRAINT hiring_job_function_stocks_stock_id_fkey
    FOREIGN KEY (stock_id) REFERENCES public.stocks(id);

CREATE INDEX idx_hiring_function_stocks_stock
    ON public.hiring_job_function_stocks USING btree (stock_id);
"""

_TBL_HIRING_PORTAL_COMPANY_IDS = """
CREATE TABLE public.hiring_portal_company_ids (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    portal character varying(20) NOT NULL,
    company_id character varying(40) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.hiring_portal_company_ids_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.hiring_portal_company_ids_id_seq
    OWNED BY public.hiring_portal_company_ids.id;
ALTER TABLE ONLY public.hiring_portal_company_ids
    ALTER COLUMN id SET DEFAULT nextval('public.hiring_portal_company_ids_id_seq'::regclass);

ALTER TABLE ONLY public.hiring_portal_company_ids
    ADD CONSTRAINT hiring_portal_company_ids_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.hiring_portal_company_ids
    ADD CONSTRAINT uq_hiring_portal_company_ids UNIQUE (stock_id, portal);
ALTER TABLE ONLY public.hiring_portal_company_ids
    ADD CONSTRAINT hiring_portal_company_ids_stock_id_fkey
    FOREIGN KEY (stock_id) REFERENCES public.stocks(id);
"""

_TBL_HIRING_SEARCH_TREND = """
CREATE TABLE public.hiring_search_trend (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    keyword_group character varying(100) NOT NULL,
    period_date date NOT NULL,
    search_index numeric(10,4) NOT NULL,
    period_type character varying(10) DEFAULT 'weekly'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.hiring_search_trend_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.hiring_search_trend_id_seq OWNED BY public.hiring_search_trend.id;
ALTER TABLE ONLY public.hiring_search_trend
    ALTER COLUMN id SET DEFAULT nextval('public.hiring_search_trend_id_seq'::regclass);

ALTER TABLE ONLY public.hiring_search_trend
    ADD CONSTRAINT hiring_search_trend_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.hiring_search_trend
    ADD CONSTRAINT uq_hiring_search_trend_stock_date UNIQUE (stock_id, period_date);
ALTER TABLE ONLY public.hiring_search_trend
    ADD CONSTRAINT hiring_search_trend_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);
"""

_TBL_HIRING_SIGNALS = """
CREATE TABLE public.hiring_signals (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    observed_date date NOT NULL,
    job_count integer DEFAULT 0 NOT NULL,
    baseline numeric(10,2),
    relative_strength numeric(8,4),
    is_spike boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    calculation_phase character varying(1)
);

CREATE SEQUENCE public.hiring_signals_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.hiring_signals_id_seq OWNED BY public.hiring_signals.id;
ALTER TABLE ONLY public.hiring_signals
    ALTER COLUMN id SET DEFAULT nextval('public.hiring_signals_id_seq'::regclass);

ALTER TABLE ONLY public.hiring_signals
    ADD CONSTRAINT hiring_signals_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.hiring_signals
    ADD CONSTRAINT hiring_signals_stock_id_observed_date_key UNIQUE (stock_id, observed_date);
ALTER TABLE ONLY public.hiring_signals
    ADD CONSTRAINT hiring_signals_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);

CREATE INDEX idx_hiring_signals_stock_date
    ON public.hiring_signals USING btree (stock_id, observed_date DESC);
CREATE INDEX idx_hiring_signals_spike
    ON public.hiring_signals USING btree (observed_date DESC) WHERE (is_spike = true);
"""

# hiring_crawler_type ENUM 에 의존 → 반드시 그 뒤에 생성.
_TBL_HIRING_SOURCES = """
CREATE TABLE public.hiring_sources (
    id bigint NOT NULL,
    stock_id bigint NOT NULL,
    crawler_type public.hiring_crawler_type NOT NULL,
    crawler_class character varying(100),
    base_url character varying(500),
    extra_config jsonb,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.hiring_sources_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.hiring_sources_id_seq OWNED BY public.hiring_sources.id;
ALTER TABLE ONLY public.hiring_sources
    ALTER COLUMN id SET DEFAULT nextval('public.hiring_sources_id_seq'::regclass);

ALTER TABLE ONLY public.hiring_sources
    ADD CONSTRAINT hiring_sources_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.hiring_sources
    ADD CONSTRAINT hiring_sources_stock_id_crawler_type_key UNIQUE (stock_id, crawler_type);
ALTER TABLE ONLY public.hiring_sources
    ADD CONSTRAINT hiring_sources_stock_id_fkey FOREIGN KEY (stock_id) REFERENCES public.stocks(id);

CREATE INDEX idx_hiring_sources_active
    ON public.hiring_sources USING btree (stock_id) WHERE (is_active = true);
"""

# 의존 순서대로. (kind, name, ddl)
OBJECTS: tuple[tuple[str, str, str], ...] = (
    ("type", "hiring_crawler_type", _ENUM_HIRING_CRAWLER_TYPE),
    ("table", "hiring_job_functions", _TBL_HIRING_JOB_FUNCTIONS),
    ("table", "hiring_job_function_stocks", _TBL_HIRING_JOB_FUNCTION_STOCKS),
    ("table", "hiring_portal_company_ids", _TBL_HIRING_PORTAL_COMPANY_IDS),
    ("table", "hiring_search_trend", _TBL_HIRING_SEARCH_TREND),
    ("table", "hiring_signals", _TBL_HIRING_SIGNALS),
    ("table", "hiring_sources", _TBL_HIRING_SOURCES),
)


def _exists(cur, kind: str, name: str) -> bool:
    if kind == "type":
        cur.execute(
            "SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = 'public' AND t.typname = %s",
            (name,),
        )
    else:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (name,),
        )
    return cur.fetchone() is not None


def _preflight(cur) -> None:
    """생성 DDL 이 의존하는 선행 객체 확인. 없으면 즉시 중단(부분 적용 방지)."""
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'stocks'"
    )
    if cur.fetchone() is None:
        raise SystemExit("✗ stocks 테이블이 없습니다 — hiring FK 를 걸 수 없습니다. 중단.")

    cur.execute(
        "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname = 'update_updated_at'"
    )
    if cur.fetchone() is None:
        raise SystemExit(
            "✗ update_updated_at() 함수가 없습니다 — "
            "hiring_job_functions 의 updated_at 트리거를 붙일 수 없습니다. 중단."
        )


def _resolve_dsn(cli_url: str | None) -> str:
    dsn = cli_url or os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("✗ DATABASE_URL 이 없습니다 (--database-url 또는 .env).")
    return dsn


def _redact(dsn: str) -> str:
    """로그용: 자격증명 마스킹."""
    if "://" not in dsn or "@" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", 1)
    return f"{scheme}://***:***@{rest.split('@', 1)[1]}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--database-url", help="대상 DSN (기본: env DATABASE_URL)")
    parser.add_argument(
        "--yes", action="store_true",
        help="실제로 적용한다. 없으면 dry-run(무엇이 생성될지만 출력).",
    )
    parser.add_argument(
        "--skip-seeds", action="store_true", help="테이블만 만들고 시드는 건너뛴다.",
    )
    args = parser.parse_args()

    dsn = _resolve_dsn(args.database_url)
    mode = "APPLY" if args.yes else "DRY-RUN"
    print(f"[{mode}] target = {_redact(dsn)}\n")

    conn = psycopg2.connect(dsn)
    conn.autocommit = False           # 전 과정 단일 트랜잭션
    created: list[str] = []
    try:
        with conn.cursor() as cur:
            _preflight(cur)

            for kind, name, ddl in OBJECTS:
                if _exists(cur, kind, name):
                    print(f"  skip   {kind:5s} {name}  (이미 존재)")
                    continue
                print(f"  CREATE {kind:5s} {name}")
                created.append(name)
                if args.yes:
                    cur.execute(ddl)

            if not created:
                print("\n✓ 결손 없음 — 스키마가 이미 baseline 정의와 일치합니다.")

            if not args.skip_seeds:
                print()
                for fname in SEED_FILES:
                    path = SEEDS_DIR / fname
                    if not path.exists():
                        raise SystemExit(f"✗ 시드 파일 없음: {path}")
                    print(f"  {'SEED  ' if args.yes else 'seed? '} {fname}")
                    if args.yes:
                        cur.execute(path.read_text(encoding="utf-8"))

        if args.yes:
            conn.commit()
            print("\n✓ 커밋 완료.")
        else:
            conn.rollback()
            print("\n(dry-run — 아무것도 바뀌지 않았습니다. 적용하려면 --yes)")
    except Exception:
        conn.rollback()
        print("\n✗ 실패 — 전체 롤백했습니다.", file=sys.stderr)
        raise
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
