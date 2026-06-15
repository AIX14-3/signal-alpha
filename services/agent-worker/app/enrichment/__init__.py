"""LLM enrichment tools: read raw records once, cache structured features.

NOT collectors (they never call an LLM) and NOT analyzers (they only read the
cached features). Enrichment sits between them, like the keyword generator.
"""
