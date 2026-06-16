COLLECT_DART = "collect_dart"
NORMALIZE_DART = "normalize_dart"
ANALYZE_DART = "analyze_dart"

# Alternative sources (hiring/patent/datalab). The values MUST match the strings
# the collectors enqueue verbatim: BaseCollector / DataLabCollector / PatentCollector
# all enqueue f"NORMALIZE_{SOURCE_TYPE}" with an UPPERCASE source type, so these
# constants are uppercase (unlike the lowercase DART constants above). The handler
# dict key, the constant value, and the DB processing_queue.task_type are all the
# same string. task_type is VARCHAR(50); the longest value here is 19 chars.
NORMALIZE_HIRING = "NORMALIZE_HIRING"
NORMALIZE_PATENT = "NORMALIZE_PATENT"
NORMALIZE_DATALAB = "NORMALIZE_DATALAB"
# Cross-source, per-stock analysis (not per-event): one task analyzes all
# registered Alternative sources for a stock on a given as_of date.
ANALYZE_ALTERNATIVE = "ANALYZE_ALTERNATIVE"
