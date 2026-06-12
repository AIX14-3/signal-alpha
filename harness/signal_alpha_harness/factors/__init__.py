"""검증된 팩터 6종 — 순수 함수, LLM 없음, 결정적.

각 팩터는 ``(panel, fundamentals) -> pd.Series`` (panel 인덱스 정렬) 시그니처.
값은 "클수록 유리" 방향으로 통일한다 (저변동성·반전은 부호 반전 적용).
z-score·결합은 Phase 3 combine.py의 몫 — 여기서는 원값만 낸다.

데이터 가용성 (2026-06-12 기준):
- momentum_12_1 / reversal_1m / lowvol_60 : 가격 패널만 필요 — 가용
- quality_margin / quality_margin_yoy     : DART PIT 재무 — 가용
- flow_20  : 수급 100% 결측 (pykrx 제약, Phase 6 키움 백필 후 활성화)
- value_bpr: 시가총액 필요 — pykrx 시총·PER/PBR 엔드포인트 익명 차단 확인,
             DART 주식총수(stockTotqySttus) + 비수정 종가로 후속 수집 예정
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from signal_alpha_harness.factors.flow import flow_20
from signal_alpha_harness.factors.price import lowvol_60, momentum_12_1, reversal_1m
from signal_alpha_harness.factors.quality import quality_margin, quality_margin_yoy
from signal_alpha_harness.factors.value import value_bpr

FactorFn = Callable[[pd.DataFrame, pd.DataFrame | None], pd.Series]

FACTORS: dict[str, FactorFn] = {
    "momentum_12_1": momentum_12_1,
    "reversal_1m": reversal_1m,
    "lowvol_60": lowvol_60,
    "flow_20": flow_20,
    "value_bpr": value_bpr,
    "quality_margin": quality_margin,
    "quality_margin_yoy": quality_margin_yoy,
}

# 데이터가 아직 없어 단독 IC 평가에서 제외하는 팩터 (게이트 분모에서도 제외)
DATA_PENDING: frozenset[str] = frozenset({"flow_20", "value_bpr"})
