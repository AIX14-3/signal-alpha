-- 20260708_1100_stock_news_digest.sql
-- target: backend
-- ============================================================================
-- 종목별 뉴스 다이제스트(stock_news_digest) — "이 종목 뉴스 흐름" 한 줄 요약
-- ----------------------------------------------------------------------------
-- 배경: stock_news 는 기사별 원문(제목·링크·시각) 목록(display-only). 메인 우 pane
--   종목 뉴스 영역에 "최근 뉴스 흐름"을 LLM(Claude Sonnet) 한 줄로 얹는다. 관련도
--   규칙으로 1차 추린 후보를 LLM 1콜에 태워 영향도 상위 기사를 고르고(내부용) 그
--   근거로 중립 서술 1문장을 생성한다(방향/감성 라벨·투자권유 없음).
-- 설계: stock_news / guard_news_events 선례를 그대로 미러링한다 — 워커 뉴스 데몬이
--   BACKEND_DATABASE_URL 풀로 직접 적재하고, main-server 는 api.stock_news_digest 뷰로
--   읽는다(2-DB 물리분리 #531). 점수·시그널 파이프라인과 무관한 display-only 레이어라
--   signal_events/scoring 은 건드리지 않는다. cross-DB FK 불가 원칙에 따라 stocks 로의
--   하드 FK 없이 stock_id + ticker 를 비정규화 저장한다.
--   종목 1:1(stock_id PK). source_hash = 요약에 넣은 후보 기사집합 해시 → 동일 집합
--   재요약 멱등 skip(비용 통제). 기사별 원문발췌인 stock_news.summary 는 재사용 불가라
--   별도 테이블로 둔다.
-- ============================================================================

CREATE TABLE public.stock_news_digest (
    stock_id            bigint PRIMARY KEY,
    ticker              varchar(10) NOT NULL,
    digest_text         text NOT NULL,
    model               varchar(60) NOT NULL,
    prompt_version      varchar(40) NOT NULL,
    article_count       integer NOT NULL,
    source_hash         varchar(64) NOT NULL,
    source_window_start timestamptz,
    source_window_end   timestamptz,
    generated_at        timestamptz NOT NULL DEFAULT now()
);

-- main-server 는 stock_code(=ticker) 로 조회한다(1 종목 1 행).
CREATE INDEX idx_stock_news_digest_ticker ON public.stock_news_digest (ticker);

-- main-server 읽기 계약 — base 권한 없이 뷰로만 조회. 내부 선별 근거(source_hash 등)는
-- 노출하지 않고 화면에 필요한 컬럼만 준다.
CREATE OR REPLACE VIEW api.stock_news_digest AS
SELECT
    ticker AS stock_code,
    digest_text,
    model,
    article_count,
    generated_at
FROM public.stock_news_digest;

-- Grants (멱등, 롤 존재 시에만):
--   signal_worker  : 뉴스 데몬이 digest 생성·재생성(UPSERT).
--   signal_backend : main-server 가 api.stock_news_digest 뷰로 조회.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_worker') THEN
        GRANT SELECT, INSERT, UPDATE ON public.stock_news_digest TO signal_worker;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_backend') THEN
        GRANT SELECT ON api.stock_news_digest TO signal_backend;
    END IF;
END $$;
