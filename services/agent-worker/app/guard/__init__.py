"""지정학 리스크 Kill-Switch 뉴스 감시 에이전트.

GDELT 수집 → LLM 판정(GeminiJsonClient 재사용) → gate 결정(advisory 제안/auto 차단).
guard_* 테이블은 backend DB 소유 — 이 패키지는 BACKEND_DATABASE_URL 풀로만 쓴다.
"""
