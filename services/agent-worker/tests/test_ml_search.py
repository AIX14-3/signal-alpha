"""Tests for the source-agnostic feature×label search engine.

Beyond "does it run", these encode the honesty properties that make an automated
sweep legitimate rather than a p-hacking machine:

  (a) a PLANTED signal survives the whole gauntlet (grid-wide BH-FDR + held-out).
  (b) PURE NOISE produces ZERO held-out-confirmed cells (the false-signal guard).
  (c) sweep-wide BH-FDR is STRICTER than a per-run correction (pooling N hypotheses
      raises the bar, so a borderline p that survives alone can fail in the pool).
  (d) a credential-gated source is recorded as a GATE, never crashes the grid.
"""

from __future__ import annotations

import numpy as np

from app.ml.research.adapters import GateNeeded, build_panel_for, select_family
from app.ml.research.search import (
    fdr_over_ledger,
    run_cell,
    run_sweep,
)
from app.ml.research.search_grid import Cell, build_grid


def _synth_grid(noise: float, seed: int = 42):
    return build_grid(
        source="synthetic", size="small", universe="synth", seed=seed,
        extra={"n_stocks": 60, "n_dates": 60, "noise": noise},
    )


def test_planted_signal_survives_gauntlet(tmp_path):
    """(a) A low-noise synthetic panel has real cross-sectional signal → it must
    clear sweep-wide BH-FDR AND confirm on the held-out era."""
    cells = _synth_grid(noise=1.0)
    summary = run_sweep(cells, out_dir=str(tmp_path), n_folds=4, n_perm=100, q=0.10)
    assert summary["ok"] == len(cells)
    assert summary["fdr_survivors"] >= 1
    assert summary["holdout_confirmed"] >= 1  # the planted signal is real


def test_pure_noise_zero_holdout_confirmed(tmp_path):
    """(b) The false-signal guard: with the signal drowned in noise, NO cell may
    survive the held-out confirmation, even if a lucky perm_p slips through BH."""
    cells = _synth_grid(noise=30.0)
    summary = run_sweep(cells, out_dir=str(tmp_path), n_folds=4, n_perm=100, q=0.10)
    assert summary["ok"] == len(cells)
    assert summary["holdout_confirmed"] == 0


def test_sweep_wide_fdr_is_stricter_than_per_run():
    """(c) Pooling more hypotheses into one BH-FDR can only raise the bar.

    A borderline p that survives BH among a handful of tests must NOT survive once
    a pile of null hypotheses are added to the same correction (sweep-wide N)."""
    def _rows(pvals):
        return [
            {"status": "ok", "era": "full", "perm_p": p, "key": str(i)}
            for i, p in enumerate(pvals)
        ]

    borderline = 0.02
    small = _rows([borderline, 0.9, 0.95])
    ok_small, _ = fdr_over_ledger(small, q=0.10)
    survives_small = sum(r["fdr_survive"] for r in ok_small)

    pooled = _rows([borderline] + [0.9] * 40)
    ok_pooled, _ = fdr_over_ledger(pooled, q=0.10)
    survives_pooled = sum(r["fdr_survive"] for r in ok_pooled)

    assert survives_small >= 1
    assert survives_pooled <= survives_small  # sweep-wide is never more lenient
    assert survives_pooled == 0  # borderline p=0.02 fails BH at N=41, q=0.10


def test_gated_source_records_gate_not_crash(tmp_path):
    """(d) A revenue source with no --revenue-csv (and/or no DATABASE_URL) is a GATE:
    the adapter raises GateNeeded and the sweep records status="gate", never crashes."""
    # The adapter itself gates deterministically (no revenue_csv → GateNeeded,
    # regardless of whether a .env supplies DATABASE_URL).
    try:
        build_panel_for("patent-revenue", horizon=1, band=0.0, seed=42, extra={})
        raised = False
    except GateNeeded:
        raised = True
    assert raised

    cell = Cell(
        source="patent-revenue", universe="u", label="rev_nowcast_q", task="revenue",
        horizon=1, band=0.0, feature_family="all", transform="raw", model="logistic",
        seed=42, extra=(),
    )
    card = run_cell(cell, n_perm=10)
    assert card.status == "gate"
    assert card.reason  # explains what to unblock

    summary = run_sweep([cell], out_dir=str(tmp_path), n_perm=10)
    assert summary["gate"] >= 1
    assert summary["ok"] == 0


def test_sector_neutralize_removes_sector_time_effect():
    """``sector_neutralize_label`` 은 (섹터×시점) 공통성분을 라벨에서 제거한다: 순수 섹터-시점
    효과로 만든 라벨은 중립화 후 각 (섹터,date) 그룹 평균이 0 이 된다(섹터 파도 제거 실증)."""
    from collections import Counter

    from app.ml.research.datalab_dataset import Dataset
    from app.ml.research.fundamentals_dataset import sector_neutralize_label

    rng = np.random.default_rng(0)
    stock, dates, growth, sector = [], [], [], {}
    sid = 0
    for sec_i in range(2):
        for _ in range(6):  # 섹터당 6종목
            sid += 1
            sector[sid] = f"S{sec_i}"
            for dt in (1, 2, 3):
                stock.append(sid)
                dates.append(dt)
                growth.append(10.0 * sec_i + 5.0 * dt + 0.01 * rng.standard_normal())
    n = len(stock)
    ds = Dataset(X=np.zeros((n, 1)), y=np.zeros(n, dtype=int),
                 excess_returns=np.array(growth, dtype=float), dates=np.array(dates),
                 stock_ids=np.array(stock), feature_names=["f"], dropped=Counter())

    out = sector_neutralize_label(ds, sector, min_cross_section=2)
    assert len(out) > 0
    sec_arr = np.array([sector[int(s)] for s in out.stock_ids])
    for d in np.unique(out.dates):
        for sc in np.unique(sec_arr):
            m = (out.dates == d) & (sec_arr == sc)
            if m.sum() >= 2:  # 섹터-시점 평균이 제거됨
                assert abs(float(out.excess_returns[m].mean())) < 1e-9


def test_monthly_signal_step_multiplies_cross_sections_pit_safe():
    """``signal_step_days>0`` 은 같은 분기 라벨을 여러 as_of 로 월별 샘플링해 횡단면을 늘리되,
    라벨은 항상 known_at(공시일) 이후 as_of 만(PIT). quarterly 대비 행수·유니크 날짜가 는다."""
    from app.ml.research.fundamentals_dataset import build_revenue_dataset

    def _known(y, q):  # 분기말 +45일쯤 공시 (as_of 분기내부보다 항상 이후)
        m_end = {1: 3, 2: 6, 3: 9, 4: 12}[q]
        ny, nm = (y + 1, 2) if q == 4 else (y, m_end + 1)
        return f"{ny:04d}-{nm:02d}-14"

    hiring, revenue = {}, {}
    for sid in (101, 202, 303):
        posts = []
        for y in (2021, 2022):
            for mo in range(1, 13):
                posts.append({"observed_date": f"{y:04d}-{mo:02d}-05",
                              "duty_groups": ["개발"] if (sid + mo) % 2 else ["영업"]})
        hiring[sid] = posts
        revenue[sid] = {
            (y, q): (float(1000 + sid * (y - 2020) * 4 + q * 10), _known(y, q))
            for y in (2021, 2022) for q in (1, 2, 3, 4)
        }

    common = dict(hiring_rows_by_stock=hiring, revenue_by_stock=revenue,
                  feature_set="volume+duty", min_observations=1, min_cross_section=1)
    q = build_revenue_dataset(**common, signal_step_days=0)
    m = build_revenue_dataset(**common, signal_step_days=30, n_signal_steps=3)

    assert len(q) > 0 and len(m) > len(q)          # 월별이 표본을 늘림
    assert len(np.unique(m.dates)) > len(np.unique(q.dates))  # 월별 횡단면 증가
    # 같은 분기 라벨이 여러 as_of 에 공유 → 한 종목의 동일 성장률이 2개 이상 날짜에 등장
    import collections
    per_firm_label_dates = collections.defaultdict(set)
    for sid, dt, g in zip(m.stock_ids, m.dates, m.excess_returns):
        per_firm_label_dates[(int(sid), round(float(g), 6))].add(int(dt))
    assert any(len(v) >= 2 for v in per_firm_label_dates.values())


def test_revenue_sue_is_pit_and_standardized():
    """``revenue_sue`` = within-firm 놀라움: 각 분기 SUE 는 **직전 K분기 YoY** 만으로
    (YoY−mean)/(std+eps). trailing 이 K개 미만이면 그 분기는 라벨 없음(PIT·엄격히 과거만)."""
    from app.ml.research.fundamentals_dataset import revenue_sue, yoy_growth

    # 8분기 매출(2020Q1..2021Q4) → YoY 는 2021Q1..Q4 4개만 정의.
    rev = {}
    base = {1: 100.0, 2: 110.0, 3: 120.0, 4: 130.0}
    for q in (1, 2, 3, 4):
        rev[(2020, q)] = base[q]
        rev[(2021, q)] = base[q] * (1.0 + 0.10 * q)  # 분기마다 다른 YoY

    yoy = yoy_growth(rev)
    assert len(yoy) == 4  # 2021 4개만
    # K=4 → 앞선 4개 trailing 이 필요. YoY 는 4개뿐이라 어느 분기도 trailing 4개 없음 → 전부 제외.
    assert revenue_sue(rev, k=4) == {}

    # K=2 로 낮추면 2021Q3(앞 Q1,Q2), 2021Q4(앞 Q2,Q3) 두 분기 SUE 정의.
    sue = revenue_sue(rev, k=2)
    assert set(sue.keys()) == {(2021, 3), (2021, 4)}
    ordered = sorted(yoy)
    for i in (2, 3):
        key = ordered[i]
        prior = [yoy[ordered[i - 2]], yoy[ordered[i - 1]]]
        expect = (yoy[key] - np.mean(prior)) / (np.std(prior) + 1e-9)
        assert abs(sue[key] - expect) < 1e-6
    # 미래 분기를 baseline 에 절대 쓰지 않음(PIT): trailing 은 항상 key 보다 앞.
    assert all(all(p < key for p in ordered[:ordered.index(key)]) for key in sue)


def test_label_mode_surprise_changes_excess_returns():
    """``label_mode='surprise'`` 는 excess_returns(연속 타깃)를 yoy 와 다르게 채운다:
    같은 (종목,분기) 라벨셋에서 surprise 는 SUE, yoy 는 성장률 → 값·랭킹이 달라진다."""
    from app.ml.research.fundamentals_dataset import build_revenue_dataset

    def _known(y, q):
        m_end = {1: 3, 2: 6, 3: 9, 4: 12}[q]
        ny, nm = (y + 1, 2) if q == 4 else (y, m_end + 1)
        return f"{ny:04d}-{nm:02d}-14"

    hiring, revenue = {}, {}
    for sid in (101, 202, 303, 404, 505, 606):
        posts = [{"observed_date": f"{y:04d}-{mo:02d}-05"}
                 for y in (2019, 2020, 2021, 2022) for mo in range(1, 13)]
        hiring[sid] = posts
        revenue[sid] = {
            (y, q): (float(1000 + sid * (y - 2018) + q * 7 * (sid % 3 + 1)), _known(y, q))
            for y in (2019, 2020, 2021, 2022) for q in (1, 2, 3, 4)
        }

    common = dict(hiring_rows_by_stock=hiring, revenue_by_stock=revenue,
                  feature_set="volume", min_observations=1, min_cross_section=2)
    ds_yoy = build_revenue_dataset(**common, label_mode="yoy")
    ds_sue = build_revenue_dataset(**common, label_mode="surprise")

    assert len(ds_sue) > 0
    # surprise 는 K=4 trailing 요구 → yoy 보다 표본이 적거나 같다(초기 분기 제외).
    assert len(ds_sue) <= len(ds_yoy)
    # 같은 (종목,분기)에서 두 라벨의 연속 타깃이 다르다.
    yoy_map = {(int(s), int(d)): float(v)
               for s, d, v in zip(ds_yoy.stock_ids, ds_yoy.dates, ds_yoy.excess_returns)}
    diffs = [abs(yoy_map[(int(s), int(d))] - float(v))
             for s, d, v in zip(ds_sue.stock_ids, ds_sue.dates, ds_sue.excess_returns)
             if (int(s), int(d)) in yoy_map]
    assert diffs and max(diffs) > 1e-6


def test_revenue_offline_gates_without_dumps():
    """revenue-offline 어댑터는 덤프 경로(--stocks-json/--postings-jsonl/--revenue-csv)가
    없으면 GateNeeded 로 안내한다(로컬 DATABASE_URL 없이도 크래시 없음)."""
    try:
        build_panel_for("revenue-offline", horizon=1, band=0.0, seed=42, extra={})
        raised = False
    except GateNeeded:
        raised = True
    assert raised

    cell = Cell(
        source="revenue-offline", universe="u", label="rev_nowcast_q", task="revenue",
        horizon=1, band=0.0, feature_family="all", transform="raw", model="logistic",
        seed=42, extra=(),
    )
    card = run_cell(cell, n_perm=10)
    assert card.status == "gate"
    assert card.reason


def test_resume_ledger_skips_completed(tmp_path):
    """The ledger is resumable: a second run over the same grid adds no new rows."""
    cells = _synth_grid(noise=1.0)
    first = run_sweep(cells, out_dir=str(tmp_path), n_perm=30)
    assert first["ran_this_call"] == len(cells)
    second = run_sweep(cells, out_dir=str(tmp_path), n_perm=30)
    assert second["ran_this_call"] == 0  # all keys already in the ledger


def test_no_features_for_family_skips_honestly(tmp_path):
    """A family whose tokens don't appear in the panel yields no columns → skip,
    not a crash or a train-on-nothing result."""
    panel = build_panel_for("synthetic", horizon=5, band=0.3, seed=42,
                            extra={"n_stocks": 40, "n_dates": 40, "noise": 1.0})
    # synthetic feature names carry none of the datalab tokens.
    assert select_family(panel.feature_names, "dl_momentum") == []

    cell = Cell(
        source="synthetic", universe="u", label="dir_h5_b0.3", task="direction",
        horizon=5, band=0.3, feature_family="dl_momentum", transform="raw",
        model="logistic", seed=42, extra=(("n_stocks", 40), ("n_dates", 40), ("noise", 1.0)),
    )
    card = run_cell(cell, panel=panel, n_perm=10)
    assert card.status == "skip"
    assert card.reason == "no_features_for_family"


def test_fusion_revenue_join_inner_and_rebinarizes():
    """The fusion-revenue join (DB-gated in production) is exercised offline here:
    it must inner-join two revenue sources on (stock, quarter), prefix + concatenate
    their features, keep the shared continuous growth, and RE-binarize on the joined
    cross-section."""
    from collections import Counter

    from app.ml.research.adapters import _join_revenue_panels
    from app.ml.research.datalab_dataset import Dataset

    def _mk(name, rows):  # rows: (sid, date, growth, feat)
        return Dataset(
            X=np.array([[r[3]] for r in rows], dtype=float),
            y=np.zeros(len(rows), dtype=int),
            excess_returns=np.array([r[2] for r in rows], dtype=float),
            dates=np.array([r[1] for r in rows], dtype=int),
            stock_ids=np.array([r[0] for r in rows], dtype=int),
            feature_names=[name], dropped=Counter(),
        )

    hi = _mk("hv", [(s, 100, 0.1 * s, 1.0 * s) for s in range(1, 9)])
    pt = _mk("pv", [(s, 100, 0.1 * s, 2.0 * s) for s in range(1, 9)] + [(9, 100, 5.0, 9.0)])
    panel = _join_revenue_panels(hi, pt, min_cross_section=6)

    assert panel.feature_names == ["h::hv", "p::pv"]
    assert len(panel.y) == 8  # the unshared patent key (9,100) is dropped
    assert panel.task == "revenue"
    # median split of growths 0.1..0.8 → lower half 0, upper half 1
    assert panel.y.tolist() == [0, 0, 0, 0, 1, 1, 1, 1]
    assert np.allclose(panel.excess_returns, [0.1 * s for s in range(1, 9)])
    assert np.allclose(panel.X[:, 1], [2.0 * s for s in range(1, 9)])


def test_scorecard_json_roundtrip(tmp_path):
    """Ledger rows serialize with the new within_firm_verdict field intact."""
    cells = _synth_grid(noise=1.0)[:1]
    run_sweep(cells, out_dir=str(tmp_path), n_perm=20)
    import json
    import os
    with open(os.path.join(str(tmp_path), "search_results.jsonl"), encoding="utf-8") as fh:
        row = json.loads(fh.readline())
    assert "within_firm_verdict" in row
    assert row["source"] == "synthetic"
    assert np.isfinite(row["perm_p"])
