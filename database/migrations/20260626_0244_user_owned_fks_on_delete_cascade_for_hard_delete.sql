-- 20260626_0244_user_owned_fks_on_delete_cascade_for_hard_delete.sql
-- target: backend
-- ============================================================================
-- user owned fks on delete cascade for hard delete
-- ----------------------------------------------------------------------------
-- 배경: 관리자 회원 삭제·회원탈퇴를 hard-delete 로 전환하면서, users 를 참조하는
--   자식 FK 의 삭제 동작을 DB 레벨에서 정리한다. 그동안 앱이 자식 테이블 목록을
--   하드코딩해 순서대로 지웠으나(스키마 드리프트에 취약), FK 의 ON DELETE 규칙으로
--   책임을 내려 'DELETE FROM users' 한 줄로 회원 데이터가 정리되도록 일반화한다.
-- 설계:
--   - 회원 소유 자식: ON DELETE CASCADE 로 전환(회원과 함께 삭제).
--     portone_verifications / signal_journals / signal_subscriptions /
--     terms_agreements / user_signal_reads / watchlists / payments(NOT NULL).
--     (report_issuances / social_accounts / user_sessions 는 이미 CASCADE 라 제외.)
--   - analysis_requests: 공용 시그널 파이프라인(analysis_results→final_signals→…)의
--     뿌리이므로 회원과 함께 삭제하지 않는다(공용 데이터 보존).
--     단, analysis_requests 는 COLLECTION DB(sa-pg) 전용이고 users 는 BACKEND DB(sa-be)
--     전용이라 2-인스턴스 분리(#531)에서는 둘 사이 **물리 FK 가 불가능**하다(cross-DB FK).
--     따라서 여기서 FK 를 추가하지 않는다 — user_id 는 FK 없는 nullable 컬럼으로 유지하고,
--     회원 삭제 시 분리(detach)는 앱레벨 publisher 책임으로 둔다
--     (rebaseline.py 의 cross-DB FK 제거 정책과 동일선상). 이 마이그는 target: backend 라
--     sa-be 에만 적용되는데 거기엔 analysis_requests 가 없으므로 관련 구문을 두면 적용이 실패한다.
-- ============================================================================

-- 회원 소유 자식 → ON DELETE CASCADE
ALTER TABLE public.portone_verifications
    DROP CONSTRAINT IF EXISTS portone_verifications_user_id_fkey,
    ADD  CONSTRAINT portone_verifications_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE public.signal_journals
    DROP CONSTRAINT IF EXISTS signal_journals_user_id_fkey,
    ADD  CONSTRAINT signal_journals_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE public.signal_subscriptions
    DROP CONSTRAINT IF EXISTS signal_subscriptions_user_id_fkey,
    ADD  CONSTRAINT signal_subscriptions_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE public.terms_agreements
    DROP CONSTRAINT IF EXISTS terms_agreements_user_id_fkey,
    ADD  CONSTRAINT terms_agreements_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE public.user_signal_reads
    DROP CONSTRAINT IF EXISTS user_signal_reads_user_id_fkey,
    ADD  CONSTRAINT user_signal_reads_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

ALTER TABLE public.watchlists
    DROP CONSTRAINT IF EXISTS watchlists_user_id_fkey,
    ADD  CONSTRAINT watchlists_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

-- payments: user_id 가 NOT NULL 이라 SET NULL 불가 → CASCADE(결제 이력도 회원과 함께 삭제).
-- 주의: 결제/환불 이력 삭제이므로 법정 보관의무(전자상거래법 등)가 있으면 별도 보관 정책 필요.
ALTER TABLE public.payments
    DROP CONSTRAINT IF EXISTS payments_user_id_fkey,
    ADD  CONSTRAINT payments_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

-- analysis_requests(COLLECTION) → users(BACKEND) 는 cross-DB FK 라 추가하지 않는다(위 설계 주석 참조).
-- COLLECTION DB 의 0003_collection_baseline 은 analysis_requests.user_id 를 FK 없는 nullable 컬럼으로
-- 두므로 그린필드에선 정리할 FK 가 없다(구 단일 DB 에서 생긴 FK 는 dev 재생성으로 사라진다).
