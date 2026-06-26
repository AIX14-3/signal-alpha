-- 004_queue_stats_index.sql
-- ============================================================================
-- 파이프라인 가시성(Phase 1) — processing_queue 집계 인덱스
-- ----------------------------------------------------------------------------
-- /internal/stats/queue 의 `GROUP BY task_type, status` 집계를 index-only 스캔으로
-- 처리하기 위한 복합 인덱스. 기존 idx_queue_pending 은 (task_type, priority,
-- scheduled_at) 부분 인덱스(pending/retrying 한정)라 status별 전체 집계를 커버하지
-- 못한다. 대기열이 수만~수십만 행으로 커져도 stats 엔드포인트가 풀스캔하지 않도록 한다.
--
-- 주의: migrate.py 는 파일당 단일 트랜잭션이라 CREATE INDEX CONCURRENTLY 를 쓸 수 없다.
-- 대용량 운영 DB 에 사후 적용할 때는 이 파일 대신 트랜잭션 밖에서 수동으로
--   CREATE INDEX CONCURRENTLY idx_queue_type_status ON processing_queue (task_type, status);
-- 를 적용하는 것을 권장한다(테이블 쓰기 잠금 회피).
-- ============================================================================

CREATE INDEX idx_queue_type_status
    ON processing_queue (task_type, status);
