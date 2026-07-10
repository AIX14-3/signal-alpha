-- 20260710_1100_stock_logo_published.sql
-- target: backend
-- ============================================================================
-- 종목 회사 로고 (워커→백엔드 발행 사본) — 공개 홈/리포트 UI 용
-- ----------------------------------------------------------------------------
-- 배경: 20260710_1000_stock_logos.sql 이 수집 DB 에 stock_logos(원본 PNG)를 두었다.
--   홈/관심종목/리포트에서 종목 옆 로고를 보여주려면 백엔드가 읽을 수 있어야 하는데,
--   Model A(백엔드는 수집 DB 에 접속하지 않음)라 stock_price_daily 처럼 발행 사본을 백엔드
--   DB 로 둔다. 발행 러너(sync_stock_logos)가 수집 DB stock_logos 에서 이 테이블로 멱등 upsert.
-- 설계: 종목당 1행(PK stock_id). image=원본 PNG bytea. 수집 원본과 컬럼 동일하되 이름만
--   다르다(stock_logos 는 수집 소유, stock_logo_published 는 백엔드 소유 — db_partition 버킷
--   충돌 방지). FK 는 backend 의 stocks 발행 사본을 참조. main-server 가 여기서 직접 SELECT.
-- ============================================================================

CREATE TABLE public.stock_logo_published (
    stock_id   bigint NOT NULL REFERENCES public.stocks(id),
    image      bytea NOT NULL,
    mime_type  character varying(30) NOT NULL DEFAULT 'image/png',
    source     character varying(40),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (stock_id)
);

-- Grants (멱등, 롤 존재 시에만):
--   signal_backend : 공개 로고 API 읽기 전용(FROM stock_logo_published 직접 SELECT).
--   signal_worker  : 발행 러너가 로고 동기화(INSERT + upsert UPDATE).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_backend') THEN
        GRANT SELECT ON public.stock_logo_published TO signal_backend;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_worker') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON public.stock_logo_published TO signal_worker;
    END IF;
END $$;
