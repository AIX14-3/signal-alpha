COLLECT_DART = "collect_dart"
COLLECT_DART_OWNERSHIP = "collect_dart_ownership"
NORMALIZE_DART = "normalize_dart"
NORMALIZE_DART_OWNERSHIP = "normalize_dart_ownership"
BACKFILL_DART_LABELS = "backfill_dart_labels"
ANALYZE_DART = "analyze_dart"
AGGREGATE_SIGNAL = "aggregate_signal"

# 오케스트레이터 조건부 되묻기(re-query) — 집계기가 소스 불일치/커버리지 얇음/mixed 를 감지하면
# 문제 소스에게만 집중 질문으로 다시 분석을 요청한다(에이전트화 Wave 3, 양방향 피드백). 이미 수집된
# 원자료를 재해석할 뿐 재수집이 아니다. bounded: task_context.requery_round 상한(MAX)이 종료 보장
# (dedupe 는 동일 라운드 중복만 collapse). ⚠️ 라이브 데몬은 plan 기반 → DEFAULT_CYCLE_PLAN 편입 필수.
# 되묻기 완료 후 AGGREGATE_SIGNAL 을 재인큐해 갱신된 소스 결과로 재종합한다. 13 chars (VARCHAR(50)).
REQUERY_SOURCE = "requery_source"

# PRICE 소스 분석 — ohlcv_data 를 PriceAnalyzer 로 분석해 analysis_result+agent_result 적재.
# DART/ALTERNATIVE 와 달리 별도 NORMALIZE 단계 없이(가격은 signal_events 없음) 바로 분석한다.
# fan-in AGGREGATE 가 (stock,date)로 집어가는 PRICE 피어를 만든다. 13 chars (VARCHAR(50)).
ANALYZE_PRICE = "analyze_price"

# 소스별 base 모델 추론 (#525 Phase 3) — DataLab/Hiring 정형 피처를 forward-return base
# 모델로 추론해 ml_inferences(model_name=src_*, run_key=SRC) 적재. 타깃이 return 이라
# 기존 vol 결합(run_key=ML)과 run_key 로 분리(D4). return 채널 결합은 WS-C 가 인큐.
# 9 chars (task_type VARCHAR(50)).
SRC_INFER = "src_infer"

# 메타러너 return 채널 결합 (#525 WS-C) — ml_inferences(run_key=SRC, src_*) + Report 피처를
# combine_return 으로 결합해 meta_signals(run_key=SRC) return 컬럼(final_score/direction/
# confidence) 적재. SRC_INFER 가 성공 예측이 있을 때 enqueue. vol 채널 불변(D4).
# 14 chars (task_type VARCHAR(50)).
RETURN_COMBINE = "return_combine"

# 발행 — 종목 발행 산출물(PUBLISHED 테이블)을 백엔드 DB 로 복사 (#11 물리 2-DB).
# AGGREGATE 가 final_signal 발행 + BACKEND_DATABASE_URL 설정 시 인큐. 미설정이면 핸들러 no-op.
# 15 chars (task_type VARCHAR(50)).
PUBLISH_SIGNALS = "publish_signals"

# 끝단 LLM 종합·설명 + 리스크 리포트(JSON). 발행 직전 단계(수치 불변, 설명만). 종합 결과를
# 곧장 PUBLISH_SIGNALS 로 인큐한다 — 유일 가드는 법적 금지단어 필터뿐(발행 차단 게이트 폐기).
# 10 chars (task_type VARCHAR(50)).
SYNTHESIZE = "synthesize"

# 에피소드 아웃컴 리코더(에이전트화 Wave 3 후속②) — 종목 무관 주기 유지태스크. 발행 후 만기된
# 에피소드의 실현 forward return/direction hit 을 signal_episodes.outcome 에 progressive 로 기록해
# recall 근거 품질을 높인다(숫자 불변, recall 참고용). 드레인 데몬이 일 1회 재시드(dedupe by 열린
# 태스크·최근 완료 여부). stock_id=NULL(배치 전체 스윕). 23 chars (task_type VARCHAR(50)).
RECORD_EPISODE_OUTCOMES = "record_episode_outcomes"

COLLECT_REPORT = "collect_report"
PROCESS_REPORT = "process_report"
NORMALIZE_REPORT = "normalize_report"
ANALYZE_REPORT = "analyze_report"

# Report RAG 임베딩 적재 — 파싱된 리포트 PDF 본문을 청킹→Gemini 임베딩→report_chunks(#709)
# 적재. 결정론 점수 경로(process→normalize→analyze)와 분리된 사이드 태스크로, PROCESS 뒤에서
# REPORT_USE_LLM=true 일 때만 인큐된다(NORMALIZE 아님 — 정규화는 임베딩을 기다리지 않는다).
# 하류 enqueue 없음. 12 chars (task_type VARCHAR(50)).
EMBED_REPORT = "embed_report"

# Alternative sources (hiring/patent/datalab). The values MUST match the strings
# the collectors enqueue verbatim: BaseCollector / DataLabCollector / PatentCollector
# all enqueue f"NORMALIZE_{SOURCE_TYPE}" with an UPPERCASE source type, so these
# constants are uppercase (unlike the lowercase DART constants above). The handler
# dict key, the constant value, and the DB processing_queue.task_type are all the
# same string. task_type is VARCHAR(50); the longest value here is 19 chars.
NORMALIZE_HIRING = "NORMALIZE_HIRING"
NORMALIZE_PATENT = "NORMALIZE_PATENT"
NORMALIZE_DATALAB = "NORMALIZE_DATALAB"
# LLM significance enrichment, slotted between NORMALIZE_PATENT and
# ANALYZE_PATENT: a patent's title+abstract -> Gemini -> cached llm_features
# the patent analyzer reads to weight filings by importance. Enqueued per stock
# carrying the just-normalized raw_document_ids; enriches only those, then
# enqueues the per-stock ANALYZE_PATENT. 14 chars (task_type is VARCHAR(50)).
ENRICH_PATENT = "ENRICH_PATENT"
# OCR skill enrichment, slotted between NORMALIZE_HIRING and ANALYZE_HIRING:
# a hiring poster image (extra_payload.image_urls) -> Tesseract OCR -> tech-skill
# set -> cached hiring_raw_details.ocr_skills the hiring analyzer reads to weight
# postings by concrete tech demand. Enqueued per stock carrying the just-normalized
# raw_document_ids; enriches only those, then enqueues the per-stock
# ANALYZE_HIRING. 13 chars (task_type is VARCHAR(50)).
ENRICH_HIRING = "ENRICH_HIRING"
# Per-source, per-stock analysis (not per-event): one task analyzes ONE
# Alternative source for a stock on a given as_of date. Split from the former
# single ANALYZE_ALTERNATIVE so each source is its own pipeline stage (C안 Phase 3)
# — the score is already separated per source upstream, this aligns the stages/
# diagram with that. Uppercase to match the alternative family; the longest value
# (ANALYZE_DATALAB) is 15 chars (task_type is VARCHAR(50)).
ANALYZE_DATALAB = "ANALYZE_DATALAB"
ANALYZE_HIRING = "ANALYZE_HIRING"
ANALYZE_PATENT = "ANALYZE_PATENT"
