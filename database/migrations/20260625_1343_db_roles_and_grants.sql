-- 20260625_1343_db_roles_and_grants.sql
-- ============================================================================
-- db roles and grants
-- ----------------------------------------------------------------------------
-- 배경: worker/backend 의존성 분리(Model A: 단일 Postgres + 소유권 경계). 하나의
--   DB를 공유하되 서비스별 롤로 권한을 격리한다. backend는 worker 내부 테이블에
--   직접 권한 없이 api.* 읽기 view로만 worker 산출물을 조회한다.
-- 설계:
--   - signal_worker  : public 워커 테이블 DML(수집/분석/시그널 적재). api 불필요.
--   - signal_backend : backend 소유 13개 테이블만 DML + api.* SELECT(읽기 계약).
--                      worker 내부 테이블 직접 권한 없음.
--   - 마이그레이션 실행 롤(DB owner, 예: signal_alpha)은 그대로 DDL/소유.
--   - **비밀번호는 이 파일에 두지 않는다**(커밋·checksum 대상). 롤은 LOGIN 으로
--     생성하되 비밀번호 없이 두고, 컷오버 시 운영 콘솔/out-of-band 로
--     `ALTER ROLE signal_worker  PASSWORD '...';`
--     `ALTER ROLE signal_backend PASSWORD '...';` 로 설정한 뒤 각 서비스의
--     DATABASE_URL 을 해당 롤로 교체한다(Phase 1d). 그 전까지 서비스는 owner URL 사용.
--   - 관리형 DB(예: Supabase)에서 실행 롤이 CREATEROLE 권한이 없으면, 롤을 콘솔에서
--     먼저 만든 뒤 이 마이그레이션을 적용한다(아래 생성 블록은 IF NOT EXISTS 가드라
--     이미 있으면 건너뛰고 grant 만 적용된다).
--   - Phase 1 한정 트레이드오프: signal_worker 는 단순화를 위해 public 전체 DML 을
--     받는다(= 기술적으로 backend 테이블에도 쓸 수 있음). 사용자가 요구한
--     "backend → worker 쓰기 차단"은 backend 롤 권한으로 완전히 강제된다.
--     반대 방향(worker → backend 차단)까지 원하면 별도 마이그레이션에서 worker grant 를
--     worker 테이블로 한정한다(Phase 2).
-- ============================================================================

-- 멱등(ON CONFLICT / IF NOT EXISTS)하게 작성. 적용 후에는 이 파일을 수정하지 말 것
-- (checksum 검증). 변경은 새 마이그레이션으로 추가한다.

-- ----------------------------------------------------------------------------
-- 1) 롤 생성 (멱등). 비밀번호 없음 — 컷오버 시 out-of-band 로 설정.
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'signal_worker') THEN
        CREATE ROLE signal_worker LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'signal_backend') THEN
        CREATE ROLE signal_backend LOGIN;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 2) signal_worker: public 전체 DML + 시퀀스 + 향후 테이블 기본권한
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO signal_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO signal_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO signal_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO signal_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO signal_worker;

-- ----------------------------------------------------------------------------
-- 3) signal_backend: api.* 읽기 계약 (worker 산출물은 view 로만)
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA api TO signal_backend;
GRANT SELECT ON ALL TABLES IN SCHEMA api TO signal_backend;
ALTER DEFAULT PRIVILEGES IN SCHEMA api
    GRANT SELECT ON TABLES TO signal_backend;

-- ----------------------------------------------------------------------------
-- 4) signal_backend: backend 소유 테이블만 DML (+ 해당 시퀀스). worker 내부 테이블
--    직접 권한은 부여하지 않는다. 존재하는 테이블/시퀀스만 동적으로 grant(멱등).
-- ----------------------------------------------------------------------------
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
        'watchlists',
        'signal_journals',
        'user_signal_reads',
        'report_issuances',
        'admin_accounts',
        'admin_sessions'
    ];
    t   TEXT;
    col TEXT;
    seq TEXT;
BEGIN
    GRANT USAGE ON SCHEMA public TO signal_backend;
    FOREACH t IN ARRAY backend_tables LOOP
        IF to_regclass('public.' || t) IS NULL THEN
            CONTINUE;  -- 아직 없는 테이블(마이그레이션 순서)에는 건너뜀
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
