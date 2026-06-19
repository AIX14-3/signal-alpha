COLLECT_DART = "collect_dart"
NORMALIZE_DART = "normalize_dart"
ANALYZE_DART = "analyze_dart"
AGGREGATE_SIGNAL = "aggregate_signal"

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
