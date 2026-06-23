COLLECT_DART = "collect_dart"
NORMALIZE_DART = "normalize_dart"
ANALYZE_DART = "analyze_dart"

# DART 공시 본문 임베딩 (결정론 전처리) — 본문을 BGE-M3(1024d)로 임베딩해 dart_chunks 적재.
# NORMALIZE_DART가 문서별로 enqueue. 생성형 LLM 미사용. 10 chars (task_type VARCHAR(50)).
EMBED_DART = "embed_dart"
AGGREGATE_SIGNAL = "aggregate_signal"

# ML/DL 추론 (게이트 통과 모델만) — 종목 OHLCV를 vol-benchmark 모델로 추론해 ml_inferences 적재.
# architecture.mermaid의 ML/DL 단계. 메타러너 결합 입력. 9 chars (task_type VARCHAR(50)).
ML_INFER = "ml_infer"

# 메타러너 결합 (stacking) — ml_inferences를 학습 가중으로 결합해 meta_signals 적재.
# ML_INFER가 성공 추론이 있을 때 enqueue. 12 chars (task_type VARCHAR(50)).
META_COMBINE = "meta_combine"

# 리스크 veto — 치명 키워드(상장폐지/감사의견거절 등) 탐지 시 final_signal 발행 보류.
# AGGREGATE_SIGNAL이 발행 신호에 대해 enqueue. 9 chars (task_type VARCHAR(50)).
RISK_VETO = "risk_veto"

# 끝단 LLM 종합·설명 + 리스크 리포트(JSON). RISK_VETO 다음 단계(수치 불변, 설명만).
# 10 chars (task_type VARCHAR(50)).
SYNTHESIZE = "synthesize"

COLLECT_REPORT = "collect_report"
PROCESS_REPORT = "process_report"
EMBED_REPORT = "embed_report"
ANALYZE_REPORT = "analyze_report"

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
# ANALYZE_ALTERNATIVE: a patent's title+abstract -> Gemini -> cached llm_features
# the patent analyzer reads to weight filings by importance. Enqueued per stock
# carrying the just-normalized raw_document_ids; enriches only those, then
# enqueues the per-stock ANALYZE_ALTERNATIVE. 14 chars (task_type is VARCHAR(50)).
ENRICH_PATENT = "ENRICH_PATENT"
# Cross-source, per-stock analysis (not per-event): one task analyzes all
# registered Alternative sources for a stock on a given as_of date.
ANALYZE_ALTERNATIVE = "ANALYZE_ALTERNATIVE"
