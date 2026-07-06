# Analyzers

Analyzers read collected evidence and produce structured analysis results.

This is where LLM-assisted analysis, scoring, JSON validation, and rule-based fallback logic belong.

Package boundaries:

- `dart/`: DART disclosure classification rules and future DART source analysis.
- `price/`: deterministic OHLCV/investor-flow scoring (`indicators.py` math, `rules.py`
  direction/score/risk-flag mapping, `analyzer.py` the `PriceAnalyzer`). Scores are signed
  values in [-1, +1]; mapping to 0–100 belongs to the aggregation layer.
- `base.py`: shared analyzer interface.
