"""DART 공시 임베딩에서 뽑는 결정론 피처 헬퍼(생성형 LLM 미사용).

임베딩(BGE-M3, 고정 가중치)은 결정론적이므로 여기서 파생하는 피처도 같은 입력 →
같은 출력이다. 1024차원 raw를 모델에 직접 넣지 않고(과적합 방지), 소수의 스칼라
피처로 환원한다(예: 신규성 = 같은 종목 과거 공시와의 거리). 거리 집계는 pgvector
(`<=>`)로 DB에서 수행하므로, 여기서는 현재 문서의 중심벡터만 만든다.
"""
from __future__ import annotations

from collections.abc import Sequence


def mean_vector(vectors: Sequence[Sequence[float]]) -> list[float]:
    """임베딩들의 성분별 평균(중심벡터)을 돌려준다. 빈 입력 → 빈 리스트.

    BGE-M3는 정규화 단위벡터를 내므로 평균은 문서 대표 방향의 근사다(추후 코사인
    거리 비교에 사용). 정확히 같은 입력 → 같은 출력(결정론).
    """
    if not vectors:
        return []
    dim = len(vectors[0])
    acc = [0.0] * dim
    for vec in vectors:
        if len(vec) != dim:
            raise ValueError(f"임베딩 차원 불일치: {len(vec)} != {dim}")
        for i, value in enumerate(vec):
            acc[i] += float(value)
    count = len(vectors)
    return [component / count for component in acc]
