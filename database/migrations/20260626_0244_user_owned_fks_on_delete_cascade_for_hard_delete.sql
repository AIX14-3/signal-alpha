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
--   - analysis_requests: collection DB 소유(다른 서버)라 이 FK 는 2-DB 분리에선 만들 수 없어 제외.
--     회원 detach 가 필요하면 DB FK 가 아니라 앱 레벨에서 처리(파일 하단 주석 참고).
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

-- analysis_requests 는 의도적으로 제외한다 (2-DB 분리에서 cross-DB FK 불가).
--   analysis_requests = collection DB (0003_collection_baseline, target: collection),
--   users            = backend DB. 둘은 서로 다른 서버라 이 FK 를 어느 DB 에서도 만들 수 없다
--   (backend 엔 analysis_requests 부재, collection 엔 users 부재 → 단일 DB 시절 잔재).
-- 회원 하드삭제 시 analysis_requests.user_id 를 NULL 로 분리(detach)해야 한다면, DB FK 가 아니라
-- 앱 레벨(collection·backend 두 DB 를 잇는 로직)에서 처리한다.
