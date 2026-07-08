-- 20260707_1000_patent_publication_date.sql
-- target: collection
-- ============================================================================
-- patent_raw_details.publication_date (특허 공개일) 컬럼 추가
-- ----------------------------------------------------------------------------
-- 배경:
--   특허는 출원(application_date) 후 ~18개월 뒤에야 공개(publication)된다. 지금까지
--   수집·분석·표시가 모두 application_date 만 기준으로 삼아, 방금 공개되어 처음
--   보이는 특허도 출원일이 ~1.5년 전이라 최근성 창 밖으로 떨어져 신호가 나오지
--   않았다. 공개일을 1급 컬럼으로 승격해 "최근 *공개된* 특허" 기준 창을 가능케 한다.
--   기존에는 공개일이 extra_payload JSON 안에만 있었다
--   (KIPRIS: OpeningDate, BigQuery(Google Patents): publication_date).
-- 설계:
--   nullable date. 백필 스크립트(services/agent-worker/scripts/
--   backfill_patent_publication_date.py)가 extra_payload 에서 기존 행을 채우고,
--   미상이면 NULL 로 둔다(분석기는 COALESCE(publication_date, application_date) 로
--   폴백). 공개일 기준 최근성/페이지네이션을 위한 (stock_id, publication_date DESC)
--   인덱스를 함께 만든다.
-- ============================================================================

-- 작성 규칙: 적용 여부는 schema_migrations 원장이 관리하나, 증분 변경의 멱등 가드는
-- 허용·권장(README §3) — 재적용·2-DB 부분적용 안전성을 위해 IF [NOT] EXISTS 가드를 쓴다.
-- 적용 후에는 이 파일을 수정하지 말 것(checksum 검증). 변경은 새 마이그레이션으로 추가한다.

ALTER TABLE public.patent_raw_details
    ADD COLUMN IF NOT EXISTS publication_date date;

COMMENT ON COLUMN public.patent_raw_details.publication_date IS
    '특허 공개일(출원 후 ~18개월). 시장에 정보가 노출되는 이벤트 시점. NULL 가능(미상). 출처: KIPRIS OpeningDate / Google Patents publication_date';

CREATE INDEX IF NOT EXISTS idx_patent_stock_pubdate
    ON public.patent_raw_details (stock_id, publication_date DESC);
