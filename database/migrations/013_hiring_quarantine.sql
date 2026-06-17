-- 012_hiring_quarantine.sql
-- ============================================================================
-- Phase 4 — hiring 크롤 레코드 레벨 격리 + replay (이슈 #161)
-- ----------------------------------------------------------------------------
-- 배경
--   3c 검증 게이트(validation_failure_reason)가 빈/깨진 레코드를 failed로 거부하지만
--   그대로 버린다. 사이트가 '부분 손상'되면(예: 회사명만 빈 채 제목은 긁힘) 그 레코드가
--   영구 유실된다. Phase 2 DLQ(dead_letter)는 processing_queue_id 기반이라 크롤 레코드엔
--   부적합 → 크롤 레코드 전용 격리 테이블을 둔다.
--
-- 설계 (하이브리드 정공법, 인프라 단계)
--   hiring_quarantine은 수집 중 failed로 거부/오류난 레코드의 "격리 보관 + replay 원장"이다.
--     - record_payload(JSONB): parse된 표준 dict. 항상 존재 → replay-data(재적재)용.
--     - raw_payload(TEXT, NULL 허용): 크롤 원본 HTML/JSON. 크롤러가 CollectorResult로
--       원본을 실어 보낼 때만 채워진다. legacy(dict 반환) 크롤러는 NULL.
--       → selector 수정 후 raw_payload 재파싱(replay-reparse, 후속 PR)으로 유실 제로 backfill.
--     - source_label: 'SAMSUNG_BIO_CAREERS' 등 — replay-reparse 시 어느 파서로 재파싱할지 매핑.
--     - replayed_at / replayed_run_id: 재적재 이력.
--   멱등: append-only(매 run 스냅샷). UNIQUE 제약 없음 — 깨진 레코드는 job_link도 빈값일 수
--   있어 자연키가 불안정하다. 중복 격리는 collector_run_id로 시계열 구분.
--
-- 범위 경계
--   레코드 단위 격리는 '부분 손상'만 잡는다. 사이트 전면 개편은 crawl()이 0건을 반환해
--   격리할 레코드가 없다 → 그건 Phase 5 알림(침묵실패/거부율)의 소관.
-- ============================================================================

CREATE TABLE hiring_quarantine (
    id BIGSERIAL PRIMARY KEY,
    collector_run_id BIGINT REFERENCES collector_runs(id),
    source_type VARCHAR(20) NOT NULL,           -- 'HIRING'
    source_label VARCHAR(100),                  -- 논리 라벨 (replay-reparse 파서 매핑)
    company_name TEXT,
    violation_reason VARCHAR(200) NOT NULL,     -- 거부/오류 사유
    record_payload JSONB NOT NULL,              -- parse된 표준 dict (replay-data용)
    raw_payload TEXT,                           -- 원본 HTML/JSON (legacy 크롤러는 NULL)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    replayed_at TIMESTAMPTZ,
    replayed_run_id BIGINT REFERENCES collector_runs(id)
);

-- 아직 재적재 안 한 격리만 빠르게 — replay 대상/현황 조회용 부분 인덱스.
CREATE INDEX idx_hiring_quarantine_unreplayed
    ON hiring_quarantine (created_at DESC)
    WHERE replayed_at IS NULL;

-- replay-reparse 시 사이트(source_label)별 격리 레코드 조회용.
CREATE INDEX idx_hiring_quarantine_label
    ON hiring_quarantine (source_label, created_at DESC);
