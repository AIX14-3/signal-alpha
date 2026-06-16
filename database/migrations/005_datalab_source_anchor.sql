-- 005_datalab_source_anchor.sql
-- ============================================================================
-- DataLab 정규화 앵커 — source_documents에 "범용 외부 앵커" 추가 (이슈 #127)
-- ----------------------------------------------------------------------------
-- 배경
--   hiring/patent/dart/report는 raw 1건이 종목 1개에 1:1로 대응해
--   source_documents.raw_document_id(→ raw_documents)로 앵커한다.
--
--   DataLab은 구조가 다르다:
--     (1) raw가 raw_documents가 아니라 datalab_raw_documents(별도 테이블,
--         stock_id 컬럼 없음 — 카테고리 단위 수집)에 있다.
--     (2) 관측 1건(키워드·날짜·기간)이 datalab_category_stocks 매핑으로
--         여러 종목으로 fan-out 한다 (1:N).
--   따라서 raw_document_id(NOT NULL UNIQUE, raw_documents FK)로는 앵커할 수 없다.
--
-- 설계 — "범용 외부 앵커"(D-soft)
--   source_documents에 소스별 컬럼을 늘리는 대신, raw_documents 밖에 사는
--   모든 raw 소스를 위한 범용 앵커 한 쌍을 둔다:
--     external_ref_type  -- 논리 소스 태그(= raw 테이블명, 예: 'datalab_raw_documents')
--     external_ref_id     -- 그 테이블의 PK 값
--   - raw_documents 기반 소스(hiring/patent/dart/report)는 raw_document_id를
--     그대로 사용 → 기존 코드·복합 FK 무결성 무변경.
--   - 새 비-raw_documents 소스가 생겨도 source_documents DDL 없이
--     external_ref_type에 새 태그를 쓰고 Normalize 핸들러만 추가하면 된다.
--
-- 멱등성/무결성 동작 원리
--   - raw_document_id를 nullable로 완화한다. 기존 복합 FK
--     (raw_document_id, stock_id)→raw_documents는 Postgres 기본 MATCH SIMPLE이라
--     raw_document_id가 NULL이면 검사를 건너뛴다 → DataLab 행에는 영향 없음.
--   - 기존 UNIQUE(raw_document_id)는 NULL 다중을 허용하므로 DataLab 행
--     (raw_document_id=NULL)들은 서로 충돌하지 않는다(제약 제거 불필요).
--   - chk_source_doc_anchor: raw_document_id 앵커 / external_ref_* 앵커 중
--     정확히 하나만 채워지도록 강제.
--   - uq_source_doc_external: (external_ref_type, external_ref_id, stock_id)
--     부분 유니크로 "관측 1건 → 종목 N개" fan-out과 run-batch 재실행 멱등을 보장.
--
-- 무결성 수준 — soft 참조 (D-soft)
--   external_ref_*는 선언적 FK가 아니다(한 컬럼이 두 테이블을 동시에 FK 참조 불가).
--   현재 mock 단계에선 데이터 소스가 자주 추가/삭제/수정되므로 의도적으로
--   "soft 참조"로 둔다: external_ref_id의 존재 보장은 Normalize 핸들러 책임이며,
--   datalab_raw_documents를 자유롭게 drop/recreate 해도 source_documents FK가
--   막지 않는다.
--
--   ▶ 운영 승격 시 업그레이드 경로 (D-trigger) — DB에서 직접 강제하려면:
--     소스 셋이 안정화된 뒤, 다음 마이그레이션에서 BEFORE INSERT OR UPDATE 트리거를
--     붙여 external_ref_type별로 존재를 검증한다. 예:
--
--       CREATE FUNCTION check_source_doc_external_ref() RETURNS TRIGGER AS $$
--       BEGIN
--         IF NEW.external_ref_type IS NULL THEN RETURN NEW; END IF;
--         CASE NEW.external_ref_type
--           WHEN 'datalab_raw_documents' THEN
--             PERFORM 1 FROM datalab_raw_documents WHERE id = NEW.external_ref_id;
--           -- WHEN '<새 소스 raw 테이블>' THEN ...  (소스 추가 시 분기 한 줄)
--           ELSE
--             RAISE EXCEPTION 'unknown external_ref_type: %', NEW.external_ref_type;
--         END CASE;
--         IF NOT FOUND THEN
--           RAISE EXCEPTION 'external_ref % % not found',
--             NEW.external_ref_type, NEW.external_ref_id;
--         END IF;
--         RETURN NEW;
--       END; $$ LANGUAGE plpgsql;
--
--       CREATE TRIGGER trg_source_doc_external_ref
--       BEFORE INSERT OR UPDATE ON source_documents
--       FOR EACH ROW EXECUTE FUNCTION check_source_doc_external_ref();
--
--     새 소스 추가 = CASE 분기 한 줄(컬럼/제약 변경 불필요). ON DELETE 연쇄가
--     필요하면 datalab_raw_documents 삭제 트리거에서 대응 source_documents를
--     정리하는 보조 트리거를 함께 둔다.
-- ============================================================================

ALTER TABLE source_documents
    ALTER COLUMN raw_document_id DROP NOT NULL;

ALTER TABLE source_documents
    ADD COLUMN external_ref_type VARCHAR(40),  -- 논리 소스 태그(= raw 테이블명)
    ADD COLUMN external_ref_id   BIGINT;        -- 해당 테이블 PK

-- raw_documents 앵커 / external_ref_* 앵커 중 정확히 하나만.
ALTER TABLE source_documents
    ADD CONSTRAINT chk_source_doc_anchor CHECK (
        (raw_document_id IS NOT NULL
            AND external_ref_type IS NULL
            AND external_ref_id IS NULL)
        OR
        (raw_document_id IS NULL
            AND external_ref_type IS NOT NULL
            AND external_ref_id IS NOT NULL)
    );

-- 관측 1건 → 종목 N개 fan-out + run-batch 재실행 멱등.
CREATE UNIQUE INDEX uq_source_doc_external
    ON source_documents (external_ref_type, external_ref_id, stock_id)
    WHERE external_ref_type IS NOT NULL;
