"""Point-in-time, period-trending keyword extraction from raw text sources.

Turns dated text (patent titles now; DART disclosures / hiring titles later, via
the same ``SourceAdapter`` interface) into the keywords that SURGED in each time
period — "what was trending then" — to drive period-appropriate DataLab search
collection. Frequency-based, no LLM (so no hindsight), deterministic.
"""
