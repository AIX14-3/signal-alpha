"""within_firm_gate 배선 테스트 — 게이트가 매출 Dataset을 end-to-end로 판정하는지.

순수 분해 정확성은 test_ml_within_firm.py 가 증명. 여기선 (a) OOF 생성·마스킹·BH·렌더가
실제 Dataset 위에서 도는지, (b) 보조 피처표가 timing 피처와 static 피처를 가르는지 확인한다.
"""

from __future__ import annotations

from collections import Counter

import numpy as np

from app.ml.research.datalab_dataset import Dataset
from app.ml.research.within_firm_gate import gate_report, render_gate


def _revenue_panel(n_firms=30, n_quarters=8, seed=0) -> Dataset:
    """(종목,분기) 패널: growth = 기업레벨(static) + 1.2·within편차(timing) + 노이즈.

    피처 2개 — 'x_static'=기업레벨, 'x_timing'=within 편차(+미세노이즈). 라벨(연속 growth)은
    excess_returns 슬롯, y 는 분기별 횡단면 중앙값 이진. build_revenue_dataset 산출 규약과 동형.
    """
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 3, n_firms)  # 기업 고유 레벨
    x_static, x_timing, growth, dates, sid = [], [], [], [], []
    for f in range(n_firms):
        dev = rng.normal(0, 1, n_quarters)
        dev -= dev.mean()  # 기업내 zero-mean 편차
        for q in range(n_quarters):
            x_static.append(base[f] + rng.normal(0, 0.05))
            x_timing.append(dev[q] + rng.normal(0, 0.05))  # 미세노이즈 → 기업평균 분산 확보
            growth.append(base[f] + 1.2 * dev[q] + rng.normal(0, 0.2))
            dates.append(q)  # 분기 ordinal
            sid.append(f)
    growth = np.array(growth)
    dates = np.array(dates)
    # y = 분기별 횡단면 중앙값 이진.
    y = np.zeros(len(growth), dtype=int)
    for q in np.unique(dates):
        m = dates == q
        y[m] = (growth[m] > np.median(growth[m])).astype(int)
    X = np.column_stack([x_static, x_timing])
    return Dataset(
        X=X, y=y, excess_returns=growth, dates=dates,
        stock_ids=np.array(sid), feature_names=["x_static", "x_timing"],
        dropped=Counter(),
    )


def test_gate_runs_and_reports_all_fields():
    ds = _revenue_panel()
    r = gate_report(ds, model_name="logistic", n_folds=5, n_perm=100, seed=1)
    assert r.n_obs > 0
    assert r.n_firms == 30
    assert len(r.features) == 2
    assert 0.0 <= r.model_within_q <= 1.0
    assert np.isfinite(r.model_rank_ic_xs)
    txt = render_gate(r)
    assert "VERDICT" in txt and "between_ic" in txt and "앵커" in txt


def test_feature_table_separates_timing_from_static():
    ds = _revenue_panel()
    r = gate_report(ds, model_name="logistic", n_folds=5, n_perm=200, seed=1)
    cells = {c.name: c for c in r.features}

    timing = cells["x_timing"]
    assert timing.within_ic > 0.4          # within 편차가 growth within 편차를 추종
    assert timing.within_q < 0.05          # BH 보정 후에도 유의

    static = cells["x_static"]
    assert static.between_ic > 0.5         # 기업레벨은 평균 growth 와 강한 정적 상관
    assert abs(static.within_ic) < 0.2     # timing 성분 거의 없음
