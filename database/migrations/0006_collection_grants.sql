-- 0006_collection_grants.sql
-- target: collection
-- ============================================================================
-- 수집 DB 권한: signal_worker (워커 소유 — public 전체 DML)
-- ----------------------------------------------------------------------------
-- 배경: agent-worker/analyzer 가 signal_worker 롤로 수집 DB 에 접속해 수집·분석·시그널
--   적재를 수행한다. 수집 DB 의 public 테이블 전체에 DML + 시퀀스 + 향후 테이블 기본권한.
-- 주: 롤 생성은 0001_infra_roles(target all). 백엔드 DB 권한은 0007_backend_grants(target backend).
-- ============================================================================

GRANT USAGE ON SCHEMA public TO signal_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO signal_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO signal_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO signal_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO signal_worker;
