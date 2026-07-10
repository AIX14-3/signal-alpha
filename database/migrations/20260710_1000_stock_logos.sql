-- 20260710_1000_stock_logos.sql
-- target: collection
-- ============================================================================
-- 종목 회사 로고 (코스피 시총 상위 200, ETF 제외)
-- ----------------------------------------------------------------------------
-- 배경: 홈/리포트/관심종목 UI 에서 종목 옆 회사 로고를 표시한다. 로고 PNG 를 종목당
--   1행으로 보관하고 stocks(id) 로 조인한다(파일명=ticker → stock_id 매칭).
--   소스=토스 CDN 브랜드 로고. 우선주는 모기업 로고 공유, 미보유 신규상장(예: LG CNS)은
--   파비콘 대체 — 감사/교체를 위해 source 컬럼에 출처를 태깅한다.
-- 설계: 종목당 1행(PK stock_id). image=원본 PNG bytea(평균 ~15KB, 200건 ~3MB).
--   서빙 계약: 워커(수집 DB) 소유. 웹 노출이 필요해지면 stock_price_daily 처럼
--   백엔드로 발행 사본을 두는 별도 마이그레이션을 추가한다(지금은 워커 DB 단독).
-- ============================================================================

CREATE TABLE public.stock_logos (
    stock_id   bigint NOT NULL REFERENCES public.stocks(id),
    image      bytea NOT NULL,
    mime_type  character varying(30) NOT NULL DEFAULT 'image/png',
    source     character varying(40),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (stock_id)
);

-- Grants (멱등, 롤 존재 시에만):
--   signal_worker  : 로더가 로고 upsert(INSERT + ON CONFLICT UPDATE).
--   signal_backend : (향후 발행 사본 대비) 읽기 전용.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_worker') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON public.stock_logos TO signal_worker;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_backend') THEN
        GRANT SELECT ON public.stock_logos TO signal_backend;
    END IF;
END $$;
