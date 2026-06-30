-- 0007_backend_grants.sql
-- target: backend
-- ============================================================================
-- 백엔드 DB 권한: signal_backend (서비스 소유 — backend 테이블 DML + api.* SELECT)
-- ----------------------------------------------------------------------------
-- 배경: main-server 가 signal_backend 롤로 백엔드 DB 에 접속한다. backend 소유 테이블에
--   DML, 발행 산출물은 api.* 읽기 view 로만 SELECT. PUBLISHED base 테이블 직접 권한은
--   부여하지 않는다(읽기 계약). 롤 생성은 0001_infra_roles(target all).
-- 설계: 존재하는 테이블/시퀀스만 동적으로 grant(멱등). api 스키마는 0005_api_read_contract.
--   롤이 존재할 때만 grant(0006/20260629_0900 과 동일 패턴) — 관리형 DB 롤 선생성/부분 적용 안전.
-- ============================================================================

-- api 읽기 계약 (worker 산출물은 view 로만). 롤 존재 시에만.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_backend') THEN
        GRANT USAGE ON SCHEMA api TO signal_backend;
        GRANT SELECT ON ALL TABLES IN SCHEMA api TO signal_backend;
        ALTER DEFAULT PRIVILEGES IN SCHEMA api
            GRANT SELECT ON TABLES TO signal_backend;
    END IF;
END $$;

-- backend 소유 테이블만 DML (+ 해당 시퀀스). PUBLISHED base 직접 권한 없음.
DO $$
DECLARE
    backend_tables TEXT[] := ARRAY[
        'users',
        'user_sessions',
        'social_accounts',
        'terms_agreements',
        'portone_verifications',
        'signal_subscriptions',
        'subscription_plans',
        'payments',
        'watchlists',
        'signal_journals',
        'user_signal_reads',
        'report_issuances',
        'admin_accounts',
        'admin_sessions',
        'admin_audit_log'
    ];
    t   TEXT;
    col TEXT;
    seq TEXT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signal_backend') THEN
        RETURN;  -- 롤 미존재(예: 롤 콘솔 선생성 전) — grant 건너뜀(멱등)
    END IF;
    GRANT USAGE ON SCHEMA public TO signal_backend;
    FOREACH t IN ARRAY backend_tables LOOP
        IF to_regclass('public.' || t) IS NULL THEN
            CONTINUE;  -- 아직 없는 테이블에는 건너뜀(멱등)
        END IF;
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO signal_backend', t
        );
        FOR col IN
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = t
        LOOP
            seq := pg_get_serial_sequence('public.' || t, col);
            IF seq IS NOT NULL THEN
                EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO signal_backend', seq);
            END IF;
        END LOOP;
    END LOOP;
END $$;
