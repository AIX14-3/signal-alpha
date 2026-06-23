"""Pipeline gates (결정론) for the gated worker architecture.

architecture.mermaid의 결정론 게이트들: 데이터 검증(게이트1, 기존 수집기 단계에 분산),
신호·모델 품질(게이트2 = 기존 AGGREGATE_SIGNAL), 그리고 발행 직전 **리스크 veto**(risk_veto).
"""
