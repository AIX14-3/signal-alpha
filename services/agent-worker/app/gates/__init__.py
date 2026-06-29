"""Pipeline gates (결정론) for the gated worker architecture.

architecture.mermaid의 결정론 게이트들: 데이터 검증(게이트1, 기존 수집기 단계에 분산),
신호·모델 품질(게이트2 = 기존 AGGREGATE_SIGNAL). 발행 차단 게이트(리스크 veto)는 폐기됐다 —
7예측률을 무조건 발행하며, 유일 가드는 끝단 SYNTHESIZE 의 법적 금지단어 필터뿐이다.
"""
