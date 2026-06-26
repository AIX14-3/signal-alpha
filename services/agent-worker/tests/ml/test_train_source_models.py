"""소스 base 모델 학습 harness (#525 Phase 3) — 순수부(지표/매핑/스킵) + e2e(gated).

DB 표본수집(collect_samples)·실제 학습은 asyncpg/lightgbm 가용 시에만 동작하므로,
DB 없이 검증 가능한 순수 함수와 아티팩트 라운드트립을 테스트한다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.ml.source_models import SOURCE_MODELS, SourceModel
from app.ml.train_source_models import (
    MODEL_NAME_BY_SOURCE,
    _hit_rate,
    _pearson,
    train_and_save,
)


def test_model_name_mapping_inverts_source_models():
    assert MODEL_NAME_BY_SOURCE == {source: name for name, source in SOURCE_MODELS.items()}
    assert MODEL_NAME_BY_SOURCE["datalab"] == "src_datalab"
    assert MODEL_NAME_BY_SOURCE["hiring"] == "src_hiring"


def test_pearson_and_hit_rate():
    # 완전 상관 → IC 1.0, hit 1.0.
    assert _pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)
    assert _hit_rate([0.1, -0.2, 0.3], [0.5, -0.1, 0.4]) == 1.0
    # 부호 절반 일치.
    assert _hit_rate([0.1, 0.2], [0.1, -0.2]) == 0.5
    # 표본 부족 → 0.
    assert _pearson([1.0], [2.0]) == 0.0


def test_train_and_save_skips_without_labels():
    # 라벨이 모두 결측이면 학습 스킵(빈 패널) — lightgbm 없이도 동작.
    samples = [
        {"asof": date(2026, 1, 1), "features": {"momentum_pct": 0.1}, "label": None},
    ]
    report = train_and_save(samples, source="datalab")
    assert report["skipped"] == "no_labeled_samples"
    assert report["model_name"] == "src_datalab"


def test_train_and_save_writes_loadable_artifact(tmp_path):
    pytest.importorskip("lightgbm")
    base = date(2026, 1, 1)
    samples = []
    for d in range(60):
        asof = base + timedelta(days=d)
        for k in range(4):  # 전 종목 패널 풀링
            momentum = (d % 7 - 3) / 10.0 + k * 0.01
            samples.append(
                {
                    "asof": asof,
                    "features": {"momentum_pct": momentum, "spike_ratio": 0.0},
                    "label": 0.5 * momentum,
                }
            )

    report = train_and_save(samples, source="datalab", out_dir=tmp_path, n_splits=3)

    assert report["model_name"] == "src_datalab"
    assert report["samples"] == len(samples)
    assert "oof_ic" in report and "oof_hit" in report
    # 저장된 아티팩트를 런타임 로더 규약대로 다시 로드해 추론 가능.
    artifact = tmp_path / "src_datalab.txt"
    assert artifact.exists()
    model = SourceModel("src_datalab").load(str(artifact))
    assert model.is_loaded
    assert model.predict({"momentum_pct": 0.2, "spike_ratio": 0.0}) is not None
