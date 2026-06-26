-- 0001_infra_roles.sql
-- target: all
-- ============================================================================
-- 인프라: 서비스 롤 (2-인스턴스 분리 재베이스라인)
-- ----------------------------------------------------------------------------
-- 배경: 수집 DB / 백엔드 DB 양쪽에 동일한 서비스 롤이 존재해야 각 서비스의
--   DATABASE_URL(signal_worker / signal_backend)이 접속·권한 격리된다. 롤은 양쪽에
--   생성하되(미사용 롤은 무해), grant 는 DB 별로 0006(collection)/0007(backend)에서 부여한다.
-- 설계:
--   - 비밀번호는 이 파일에 두지 않는다(커밋·checksum 대상). LOGIN 으로 만들고 비번 없이 두며,
--     컷오버 시 out-of-band 로 `ALTER ROLE ... PASSWORD '...'` 부여 후 DSN 을 교체한다.
--   - 관리형 DB(Neon/Cloud SQL)에서 실행 롤이 CREATEROLE 권한이 없으면 콘솔에서 롤을 먼저
--     만든 뒤 적용한다(아래 블록은 IF NOT EXISTS 가드).
--   - 확장(CREATE EXTENSION)은 현재 스키마에서 불필요(pgvector 제거됨, gen_random_uuid 는 코어).
--     향후 필요한 확장이 생기면 rebaseline 생성기가 이 파일에 함께 적재한다.
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'signal_worker') THEN
        CREATE ROLE signal_worker LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'signal_backend') THEN
        CREATE ROLE signal_backend LOGIN;
    END IF;
END $$;
