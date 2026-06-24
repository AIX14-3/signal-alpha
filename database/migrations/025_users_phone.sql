-- 019_users_phone.sql
-- 신규 기획: 포트원 본인인증 단일 인증. 본인인증으로 확보한 핸드폰 번호를 사용자 고유 식별값으로 저장한다.
-- - phone 은 NULL 허용(레거시/관리자 행 대비) 후, 활성 사용자(미탈퇴) 범위에서만 UNIQUE 를 강제한다.
-- - 탈퇴(soft delete, deleted_at IS NOT NULL) 후 동일 번호 재가입을 허용하기 위해 partial unique index 를 쓴다.
-- - member_code(영문4+숫자4)는 baseline 의 users.member_code VARCHAR(20) UNIQUE 를 그대로 사용한다(컬럼 추가 없음).

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS phone VARCHAR(20);

-- 활성 사용자 기준 핸드폰 유니크. 탈퇴 사용자는 제외하여 동일 번호 재가입 가능.
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_phone_active
    ON users (phone)
    WHERE phone IS NOT NULL AND deleted_at IS NULL;
