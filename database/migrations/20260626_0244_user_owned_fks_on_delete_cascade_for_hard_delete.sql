-- 20260626_0244_user_owned_fks_on_delete_cascade_for_hard_delete.sql
-- ============================================================================
-- user owned fks on delete cascade for hard delete
-- ----------------------------------------------------------------------------
-- 배경: 관리자 회원 삭제·회원탈퇴를 hard-delete 로 전환하면서, users 를 참조하는
--   자식 FK 의 삭제 동작을 DB 레벨에서 정리한다. 그동안 앱이 자식 테이블 목록을
--   하드코딩해 순서대로 지웠으나(스키마 드리프트에 취약), FK 의 ON DELETE 규칙으로
--   책임을 내려 'DELETE FROM users' 한 줄로 회원 데이터가 정리되도록 일반화한다.
-- 설계:
--   - 회원 소유(NOT NULL) 자식 6종: ON DELETE CASCADE 로 전환(회원과 함께 삭제).
--     (report_issuances/social_accounts/user_sessions 는 이미 CASCADE 라 제외.)
--   - analysis_requests: 공용 시그널 파이프라인(analysis_results→final_signals→…)의
--     뿌리이므로 삭제하지 않고 ON DELETE SET NULL 로 분리(detach)해 공용 데이터 보존.
--     (user_id 는 nullable.)
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

-- 공용 시그널 보존: analysis_requests → ON DELETE SET NULL (행 보존, 회원만 분리)
ALTER TABLE public.analysis_requests
    DROP CONSTRAINT IF EXISTS analysis_requests_user_id_fkey,
    ADD  CONSTRAINT analysis_requests_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;
