-- 20260626_1205_event_study_panel_forward_return_labels.sql
-- ============================================================================
-- event_study_panel — L6 forward-return 라벨 패널
-- ----------------------------------------------------------------------------
-- 배경: 소스별 base ML/DL + 학습형 메타러너(stacking)의 공통 학습 타깃은 forward return 이다.
--       그 라벨의 단일 진실원으로, signal_events(각 소스 원자 신호)를 앵커로 삼아 이벤트
--       기준일 다음 영업일(asof+1)부터의 누적수익과 시장초과수익을 적재한다. lift 채택
--       게이트(소스/그룹별 forward-return 우위 검정)도 이 패널을 읽는다.
-- 설계: signal_event_id+asof_date 당 1행 멱등(upsert). look-ahead 차단 — fwd_return_* 는 모두
--       asof+1 부터 계산(당일 종가 미사용; asof_date = event_date 다음 영업일). abnormal_return_20d
--       = 종목 20d 수익 − kospi20 벤치 20d 수익(시장초과). universe_snapshot 은 생존편향 차단용
--       유니버스 식별 태그(예: 'kospi20_seed'). 데이터 부족/휴장으로 윈도우 미충족 시 해당
--       fwd_return_* 는 NULL(라벨 결측 → 학습에서 제외). 워커 산출 테이블이라 signal_worker DML 은
--       db_roles_and_grants 의 ALTER DEFAULT PRIVILEGES 로 자동 적용된다(별도 grant 불필요).
-- ============================================================================

-- 멱등(ON CONFLICT / IF NOT EXISTS)하게 작성. 적용 후에는 이 파일을 수정하지 말 것
-- (checksum 검증). 변경은 새 마이그레이션으로 추가한다.

CREATE TABLE IF NOT EXISTS event_study_panel (
    id BIGSERIAL PRIMARY KEY,
    signal_event_id BIGINT NOT NULL REFERENCES signal_events(id) ON DELETE CASCADE,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    asof_date DATE NOT NULL,
    fwd_return_1d DOUBLE PRECISION,
    fwd_return_5d DOUBLE PRECISION,
    fwd_return_20d DOUBLE PRECISION,
    abnormal_return_20d DOUBLE PRECISION,
    universe_snapshot VARCHAR(40) NOT NULL DEFAULT 'kospi20_seed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_event_study_panel UNIQUE (signal_event_id, asof_date)
);

CREATE INDEX IF NOT EXISTS idx_event_study_panel_stock_asof
    ON event_study_panel (stock_id, asof_date DESC);

CREATE INDEX IF NOT EXISTS idx_event_study_panel_asof
    ON event_study_panel (asof_date, universe_snapshot);
