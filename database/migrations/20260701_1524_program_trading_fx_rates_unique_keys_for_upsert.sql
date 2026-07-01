-- 20260701_1524_program_trading_fx_rates_unique_keys_for_upsert.sql
-- target: collection
-- ============================================================================
-- program_trading fx_rates unique keys for upsert
-- ----------------------------------------------------------------------------
-- 배경:
--   하락데이터 수집이 program_trading(프로그램매매)·fx_rates(환율)를 재사용하는데,
--   두 테이블에 (stock_id, trade_date) / (pair, trade_date) 유니크가 없어
--   ON CONFLICT 멱등 업서트가 불가능했다. 재수집 안전성을 위해 유니크 키를 추가한다.
-- 설계:
--   두 테이블 모두 현재 비어 있어 유니크 인덱스 추가는 안전. 명명 규칙 uq_ 접두.
-- ============================================================================

-- 작성 규칙: 적용 여부는 schema_migrations 원장이 관리하므로 IF NOT EXISTS를 쓰지 않는다.
-- 시드 데이터가 필요하면 seeds/에 분리하고 ON CONFLICT로 재실행 가능하게 작성한다.
-- 적용 후에는 이 파일을 수정하지 말 것(checksum 검증). 변경은 새 마이그레이션으로 추가한다.

CREATE UNIQUE INDEX uq_program_trading_stock_date
    ON public.program_trading (stock_id, trade_date);

CREATE UNIQUE INDEX uq_fx_rates_pair_date
    ON public.fx_rates (pair, trade_date);
