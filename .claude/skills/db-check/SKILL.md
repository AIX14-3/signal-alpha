---
name: db-check
description: signal-alpha 2DB(수집/백엔드) 정합 점검. id 드리프트·발행표 충돌·마이그 상태·큐 상태를 진단(읽기 위주). "DB 점검", "id 드리프트", "발행 충돌", "스키마 드리프트" 시 사용.
allowed-tools: Bash, Read, Grep, Glob
---

# 2DB 정합 점검 (진단 전용)

DB 구조: `DATABASE_URL`=수집(worker 소유), `BACKEND_DATABASE_URL`=발행/서비스.
PUBLISHED_TABLES = {stocks, final_signals, analysis_results, agent_results, signal_events, source_documents} — 양쪽에 존재(백엔드는 복사본). 정의: `database/db_partition.py`.

## 점검 순서
1. 마이그 상태:
   - `uv run python database/migrate.py status --target collection`
   - `uv run python database/migrate.py status --target backend`
2. 스키마 드리프트(임시 DB로 비교, exit 1 = 불일치):
   - `uv run --with psycopg2-binary python database/tools/check_schema.py --database-url "$DATABASE_URL"`
   - `... --database-url "$BACKEND_DATABASE_URL"`
3. target 태그 무결성: `uv run python database/tools/check_targets.py`
4. id 드리프트 / 발행 건수 대조 (읽기 쿼리):
   - `SELECT COUNT(*) FROM final_signals;` 를 수집·백엔드 양쪽에서 비교
   - `SELECT stock_id FROM final_signals WHERE id=$1;` 양쪽 일치 확인
5. 큐 적체:
   - `SELECT status, COUNT(*) FROM processing_queue GROUP BY status;`
   - processing 고착 행 확인: `SELECT * FROM processing_queue WHERE status='processing' LIMIT 10;`

## 보고
- 드리프트/충돌 원인(구조 드리프트 vs 데이터 부족)과 권장 조치만 제시.
- 실제 수정(마이그 적용/재발행)은 `/publish-report` 또는 수동 승인 후 진행 — 이 스킬은 진단까지.
