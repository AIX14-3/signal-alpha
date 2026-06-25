-- 20260625_1024_drop_embeddings_pgvector.sql
-- ============================================================================
-- drop embeddings pgvector
-- ----------------------------------------------------------------------------
-- 배경: 임베딩/벡터화 기능을 제거하기로 결정(증권사 리포트 BGE-M3 벡터화 + RAG).
--       따라서 pgvector 저장이 더 이상 필요 없다. 임베딩 전용 테이블과 vector
--       확장을 forward-only 로 제거한다(기존 001/021/022 는 보존, 체크섬 유지).
-- 설계: dart_chunks / dart_document_features / report_chunks 는 모두 임베딩
--       파이프라인 전용(청크 텍스트 + vector(1024)). RAG 분석 경로 제거 결정에
--       따라 통째로 드롭. 이후 vector 타입 사용처가 없으므로 확장도 드롭.
--       GCP Cloud SQL 은 pgvector 를 지원하므로 001 의 CREATE EXTENSION 은
--       배포 시 통과하고, 이 마이그레이션이 마지막에 확장을 제거 → 최종 스키마는
--       pgvector-free.
-- ============================================================================

-- 멱등(ON CONFLICT / IF NOT EXISTS)하게 작성. 적용 후에는 이 파일을 수정하지 말 것
-- (checksum 검증). 변경은 새 마이그레이션으로 추가한다.

-- 임베딩 전용 테이블 제거(인덱스는 테이블과 함께 드롭됨). 참조 객체가 없어 CASCADE 불필요.
DROP TABLE IF EXISTS public.dart_document_features;
DROP TABLE IF EXISTS public.dart_chunks;
DROP TABLE IF EXISTS public.report_chunks;

-- 남은 vector 타입 사용처가 없으므로 확장 제거(Cloud SQL/pgvector 이미지 모두에서 멱등).
DROP EXTENSION IF EXISTS vector;
