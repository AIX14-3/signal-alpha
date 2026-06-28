"""소스별 base 모델 오프라인 학습 실행 스크립트 (#525 Phase 3 harness).

L6 event_study_panel 의 forward-return 라벨 + 소스별 PIT 피처(전 종목 패널 풀링, D1)로
DataLab/Hiring base 모델을 walk-forward OOF 학습해 아티팩트로 저장한다. 런타임(SRC_INFER)은
이 아티팩트를 로드해 추론한다(없으면 결측, D2).

학습 규율(docs/archive/design/meta-learner-training.md): walk-forward 시간분할 OOF(랜덤 금지),
L1/L2·shallow tree·early-stopping(source_training.DEFAULT_PARAMS).

사용법:
    uv run python -m app.ml.train_source_models \
        --source all --target fwd_return_20d \
        --asof-from 2025-01-01 --asof-to 2026-06-01 \
        --database-url postgresql://user:pw@host:5432/collection_db

순수 부분(``train_and_save``·``evaluate_oof``)은 DB 없이 표본 리스트로 단위테스트 가능하고,
DB 표본수집(``collect_samples``)·실제 학습은 각각 asyncpg·lightgbm 가용 시에만 동작한다.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

from app.ml.source_features import DEFAULT_LOOKBACK_DAYS, assemble_features
from app.ml.source_inference import DEFAULT_ARTIFACT_DIR, DEFAULT_LOADER_LOOKBACK_DAYS
from app.ml.source_models import SOURCE_MODELS
from app.ml.source_training import HAVE_LGBM, build_panel, train_booster, walk_forward_splits

# source 키 → ml_inferences model_name (SOURCE_MODELS 역방향).
MODEL_NAME_BY_SOURCE = {source: name for name, source in SOURCE_MODELS.items()}
# 허용 라벨 컬럼(event_study_panel) — 공통 타깃 forward return.
LABEL_COLUMNS = ("fwd_return_1d", "fwd_return_5d", "fwd_return_20d", "abnormal_return_20d")
DEFAULT_TARGET = "fwd_return_20d"
DEFAULT_UNIVERSE = "kospi20_seed"


async def collect_samples(
    connection: Any,
    *,
    source: str,
    asof_from: Any,
    asof_to: Any,
    target: str = DEFAULT_TARGET,
    universe: str | None = DEFAULT_UNIVERSE,
    loader_lookback_days: int = DEFAULT_LOADER_LOOKBACK_DAYS,
    feature_lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[dict]:
    """L6 라벨 × 소스 PIT 피처를 (asof, features, label) 표본열로 모은다(패널 풀링).

    각 라벨 (stock_id, asof) 에 대해 해당 소스 행을 evidence 로더로 읽어 ``assemble_features``
    로 PIT 어셈블한다. 라벨 결측 행은 제외(미래수익 미관측).
    """
    from signal_alpha_data_access.repositories import EventStudyRepository, RawDetailRepository

    if source not in MODEL_NAME_BY_SOURCE:
        raise ValueError(f"unknown source: {source} (expected one of {list(MODEL_NAME_BY_SOURCE)})")

    repo = RawDetailRepository(connection)
    loader = _build_loader(source, repo, loader_lookback_days, connection=connection)
    labels = await EventStudyRepository(connection).list_for_training(
        asof_from=asof_from, asof_to=asof_to, universe_snapshot=universe
    )

    samples: list[dict] = []
    for row in labels:
        label = row[target]
        if label is None or not math.isfinite(float(label)):
            continue
        stock_id = int(row["stock_id"])
        asof = row["asof_date"]
        evidence = await loader.load(stock_id=stock_id, stock_code="", as_of=asof)
        rows = list(evidence[0].metadata.get("rows") or []) if evidence else []
        sector = (
            evidence[0].metadata.get("sector_demand")
            if (source == "hiring" and evidence)
            else None
        )
        features = assemble_features(
            asof,
            lookback_days=feature_lookback_days,
            sector_demand=sector,
            **{f"{source}_rows": rows},
        )[source]
        samples.append({"asof": asof, "features": features, "label": float(label)})
    return samples


def train_and_save(
    samples: Sequence[dict],
    *,
    source: str,
    out_dir: str | Path = DEFAULT_ARTIFACT_DIR,
    n_splits: int = 4,
    **train_kwargs: Any,
) -> dict[str, Any]:
    """표본 → 패널 → walk-forward OOF 학습 → 아티팩트 저장 + OOF 지표 반환.

    아티팩트 경로: ``<out_dir>/<model_name>.txt`` (런타임 ``load_source_models`` 와 동일 규약).
    라벨 표본이 없으면 학습을 건너뛴다. 표본이 있는데 lightgbm 미설치면 RuntimeError.
    """
    model_name = MODEL_NAME_BY_SOURCE[source]
    panel = build_panel(samples, source=source)
    if not panel.X:
        return {"source": source, "model_name": model_name, "samples": 0, "skipped": "no_labeled_samples"}
    if not HAVE_LGBM:
        raise RuntimeError("train_and_save 는 lightgbm 백엔드가 필요합니다.")

    metrics = evaluate_oof(samples, source=source, n_splits=n_splits, **train_kwargs)
    booster = train_booster(panel, n_splits=n_splits, **train_kwargs)

    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    artifact_path = directory / f"{model_name}.txt"
    booster.save_model(str(artifact_path))

    return {
        "source": source,
        "model_name": model_name,
        "samples": len(panel.X),
        "artifact": str(artifact_path),
        **metrics,
    }


def evaluate_oof(
    samples: Sequence[dict],
    *,
    source: str,
    n_splits: int = 4,
    **train_kwargs: Any,
) -> dict[str, float | int]:
    """walk-forward OOF 예측으로 IC(Pearson)·hit(부호 일치율)·표본수 산출.

    각 폴드의 train 으로 학습해 그 valid 를 예측(누설 0). 전 폴드 valid 예측을 모아 평가한다.
    """
    if not HAVE_LGBM:
        raise RuntimeError("evaluate_oof 는 lightgbm 백엔드가 필요합니다.")
    panel = build_panel(samples, source=source)
    folds = walk_forward_splits(panel.asofs, n_splits=n_splits)

    oof_pred: list[float] = []
    oof_true: list[float] = []
    for train_idx, valid_idx in folds:
        fold_samples = [samples[i] for i in train_idx]
        fold_panel = build_panel(fold_samples, source=source)
        if not fold_panel.X:
            continue
        booster = train_booster(fold_panel, n_splits=min(n_splits, max(1, _distinct_asofs(fold_panel) - 1)), **train_kwargs)
        for i in valid_idx:
            pred = float(booster.predict([panel.X[i]])[0])
            if math.isfinite(pred):
                oof_pred.append(pred)
                oof_true.append(panel.y[i])

    return {
        "oof_n": len(oof_pred),
        "oof_ic": round(_pearson(oof_pred, oof_true), 4),
        "oof_hit": round(_hit_rate(oof_pred, oof_true), 4),
    }


# 주가 학습 윈도우(달력일) — sma60 등 ~60 거래세션 + 피처 lookback 을 덮도록 넉넉히.
DEFAULT_PRICE_TRAIN_WINDOW_DAYS = 160


class _PriceTrainingLoader:
    """학습용 주가 로더 — evidence 로더와 동일한 ``load()`` 계약으로 OHLCV 행을 돌려준다.

    런타임(SrcInfer)은 최근 세션을 읽지만, 학습은 라벨의 as_of 시점 PIT 윈도우가 필요하므로
    ``list_ohlcv_between([as_of - window, as_of])`` 로 조회한다(look-ahead 0).
    """

    def __init__(self, connection: Any, *, window_days: int = DEFAULT_PRICE_TRAIN_WINDOW_DAYS) -> None:
        from signal_alpha_data_access.repositories import MarketDataRepository

        self._market = MarketDataRepository(connection)
        self._window = window_days

    async def load(self, *, stock_id: int, stock_code: str, as_of: Any) -> list[Any]:
        end = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)[:10])
        start = end - timedelta(days=self._window)
        rows = await self._market.list_ohlcv_between(
            stock_id=stock_id, start_date=start, end_date=end
        )
        return [SimpleNamespace(metadata={"rows": [dict(row) for row in rows]})]


def _build_loader(source: str, repo: Any, lookback_days: int, *, connection: Any = None) -> Any:
    if source == "price":
        return _PriceTrainingLoader(connection)
    if source == "datalab":
        from app.evidence_loaders.datalab_loader import DataLabEvidenceLoader

        return DataLabEvidenceLoader(repo, lookback_days=lookback_days)
    if source == "dart":
        from app.evidence_loaders.dart_loader import DartEvidenceLoader

        return DartEvidenceLoader(repo, lookback_days=lookback_days)
    if source == "patent":
        from app.evidence_loaders.patent_loader import PatentEvidenceLoader

        return PatentEvidenceLoader(repo, lookback_days=lookback_days)
    from app.evidence_loaders.hiring_loader import HiringEvidenceLoader

    return HiringEvidenceLoader(repo, lookback_days=lookback_days)


def _distinct_asofs(panel: Any) -> int:
    return len(set(panel.asofs))


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    if n < 2:
        return 0.0
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    denom = math.sqrt(var_a * var_b)
    return cov / denom if denom > 0 else 0.0


def _hit_rate(pred: Sequence[float], true: Sequence[float]) -> float:
    if not pred:
        return 0.0
    hits = sum(1 for p, t in zip(pred, true) if (p > 0) == (t > 0))
    return hits / len(pred)


async def _run(args: argparse.Namespace) -> int:
    import asyncpg

    dsn = args.database_url or os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL 이 필요합니다 (--database-url 또는 환경변수).")

    sources = list(MODEL_NAME_BY_SOURCE) if args.source == "all" else [args.source]
    connection = await asyncpg.connect(dsn)
    try:
        for source in sources:
            samples = await collect_samples(
                connection,
                source=source,
                asof_from=args.asof_from,
                asof_to=args.asof_to,
                target=args.target,
                universe=args.universe,
            )
            report = train_and_save(samples, source=source, out_dir=args.out_dir, n_splits=args.n_splits)
            print(f"[{source}] {report}")
    finally:
        await connection.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="소스별 base 모델 학습 harness (#525 Phase 3)")
    parser.add_argument("--source", choices=[*MODEL_NAME_BY_SOURCE, "all"], default="all")
    parser.add_argument("--target", choices=LABEL_COLUMNS, default=DEFAULT_TARGET)
    parser.add_argument("--asof-from", required=True, help="라벨 기간 시작 (YYYY-MM-DD)")
    parser.add_argument("--asof-to", required=True, help="라벨 기간 끝 (YYYY-MM-DD)")
    parser.add_argument("--universe", default=DEFAULT_UNIVERSE, help="유니버스 스냅샷(생존편향 차단)")
    parser.add_argument("--out-dir", default=str(DEFAULT_ARTIFACT_DIR))
    parser.add_argument("--n-splits", type=int, default=4)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
