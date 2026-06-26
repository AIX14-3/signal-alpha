"""소스별 base 모델 오프라인 학습 harness (#525 Phase 3).

전 종목 패널 풀링(D1) + L6 forward-return 라벨로 소스별 base 모델을 학습한다. 학습 규율
(``docs/archive/design/meta-learner-training.md``):
- **walk-forward 시간분할 OOF** 만(랜덤 k-fold 금지) — train 은 valid 이전 시점만(D3 누설 차단).
- 강한 정규화(L1/L2·shallow tree)·조기종료로 과적합 억제.

순수 부분(``walk_forward_splits``·``build_panel``)은 백엔드 의존 없이 단위테스트 가능하고,
학습 자체(``train_booster``)는 ``lightgbm`` 가용 시에만 동작한다(가용성 게이트).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

from app.ml.source_models import HAVE_LGBM, feature_order, vectorize

if HAVE_LGBM:  # pragma: no cover - 백엔드 있을 때만
    import lightgbm as lgb


@dataclass(frozen=True)
class Panel:
    """벡터화된 학습 패널. ``X`` 행 i = ``asofs[i]`` 시점 한 (종목,asof) 표본."""

    X: list[list[float]]
    y: list[float]
    asofs: list[date]
    feature_names: list[str]


def build_panel(
    samples: Sequence[Mapping[str, Any]],
    *,
    source: str,
) -> Panel:
    """``{asof, features, label}`` 표본열 → 고정 컬럼 순서의 벡터 패널.

    ``features`` 는 해당 소스의 피처 dict(``assemble_features(...)[source]`` 형태).
    라벨이 None/비유한인 표본은 제외(미래수익 미관측 구간).
    """
    order = feature_order(source)
    X: list[list[float]] = []
    y: list[float] = []
    asofs: list[date] = []
    for sample in samples:
        label = sample.get("label")
        if label is None or not math.isfinite(float(label)):
            continue
        X.append(vectorize(sample["features"], order))
        y.append(float(label))
        asofs.append(_as_date(sample["asof"]))
    return Panel(X=X, y=y, asofs=asofs, feature_names=order)


def walk_forward_splits(
    asofs: Sequence[date],
    *,
    n_splits: int,
) -> list[tuple[list[int], list[int]]]:
    """행별 asof 열 → walk-forward OOF 폴드 (train 인덱스, valid 인덱스) 목록.

    distinct 날짜를 시간순 ``n_splits+1`` 블록으로 나눠, 폴드 k 의 valid=블록 k,
    train=블록 0..k-1(모두 valid 이전 시점). train 이 valid 보다 미래를 보는 누설 0(D3).
    """
    if n_splits < 1:
        raise ValueError("n_splits must be >= 1")
    distinct = sorted(set(asofs))
    if len(distinct) < n_splits + 1:
        raise ValueError(
            f"walk-forward 분할에는 최소 {n_splits + 1} 개의 distinct asof 가 필요합니다 "
            f"(현재 {len(distinct)})."
        )

    blocks = _contiguous_blocks(distinct, n_splits + 1)
    by_date: dict[date, list[int]] = {}
    for idx, d in enumerate(asofs):
        by_date.setdefault(d, []).append(idx)

    folds: list[tuple[list[int], list[int]]] = []
    for k in range(1, n_splits + 1):
        train_dates = {d for block in blocks[:k] for d in block}
        valid_dates = set(blocks[k])
        train_idx = sorted(i for d in train_dates for i in by_date.get(d, []))
        valid_idx = sorted(i for d in valid_dates for i in by_date.get(d, []))
        folds.append((train_idx, valid_idx))
    return folds


# 강한 정규화·shallow tree 기본값(과적합 억제, D1).
DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "regression",
    "metric": "l2",
    "num_leaves": 15,
    "max_depth": 4,
    "learning_rate": 0.03,
    "lambda_l1": 1.0,
    "lambda_l2": 1.0,
    "min_child_samples": 50,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbosity": -1,
}


def train_booster(
    panel: Panel,
    *,
    n_splits: int = 4,
    num_boost_round: int = 500,
    early_stopping_rounds: int = 30,
    params: Mapping[str, Any] | None = None,
    seed: int = 42,
) -> Any:
    """walk-forward 마지막 폴드를 valid 로 조기종료 학습한 LightGBM Booster 반환.

    ``lightgbm`` 미설치면 ``RuntimeError`` (가용 호스트에서만 학습). 추론 측은 아티팩트
    부재를 결측으로 흡수하므로, 학습은 백엔드가 있는 곳에서만 수행한다.
    """
    if not HAVE_LGBM:
        raise RuntimeError("train_booster 는 lightgbm 백엔드가 필요합니다.")
    if not panel.X:
        raise ValueError("빈 패널로는 학습할 수 없습니다.")

    folds = walk_forward_splits(panel.asofs, n_splits=n_splits)
    train_idx, valid_idx = folds[-1]
    merged_params = {**DEFAULT_PARAMS, **(params or {}), "seed": seed}

    train_set = lgb.Dataset(
        [panel.X[i] for i in train_idx],
        label=[panel.y[i] for i in train_idx],
        feature_name=panel.feature_names,
    )
    valid_set = lgb.Dataset(
        [panel.X[i] for i in valid_idx],
        label=[panel.y[i] for i in valid_idx],
        reference=train_set,
    )
    return lgb.train(
        merged_params,
        train_set,
        num_boost_round=num_boost_round,
        valid_sets=[valid_set],
        callbacks=[lgb.early_stopping(early_stopping_rounds, verbose=False)],
    )


def _contiguous_blocks(items: list[date], n_blocks: int) -> list[list[date]]:
    """시간순 리스트를 ``n_blocks`` 개의 (거의) 균등 연속 블록으로 분할."""
    size, rem = divmod(len(items), n_blocks)
    blocks: list[list[date]] = []
    start = 0
    for b in range(n_blocks):
        length = size + (1 if b < rem else 0)
        blocks.append(items[start : start + length])
        start += length
    return blocks


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
