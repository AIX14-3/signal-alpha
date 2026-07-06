"""[채용 Step1] within-firm 게이트 로직 — OOF 신호 생성 + between/within 판정.

``within_firm.py`` 의 순수 분해에 하니스(모델·walk-forward·permutation·BH-FDR)를 붙인 계층.
CLI(``scripts/within_firm_hiring_revenue.py``)와 단위테스트가 공유한다.

판정 대상 신호:
  (주) 최적모델(decision_tree)의 **out-of-fold 횡단면 score** — confirmed rankIC(+0.128)를 낸
       바로 그 신호. purge/embargo 는 walk_forward_folds 가 날짜 경계 분할로 보장.
  (보조) 지배적 rich 피처들을 각각 원시 신호로 분해 → 어느 피처가 timing/static 인지.

within_ic 유의성은 permutation(기업 블록 내 라벨 셔플)으로만 재고(소표본 analytic-t 금지),
모델+피처 셀 전체에 **BH-FDR** 를 걸어 다중검정을 보정한다. 특허→변동성 선례
(between≈±0.42 / within≈0 → 정적특성 강등)를 앵커로 대조한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.base import clone

from .datalab_dataset import Dataset
from .evaluation import (
    _bullish_score,
    evaluate_model,
    purged_walk_forward_folds,
    walk_forward_folds,
)
from .models import build_classifier_registry
from .stats import benjamini_hochberg
from .within_firm import Decomposition, between_within_ic, within_ic_permutation

# 특허→실현변동성 매그니튜드 게이트의 확정 결과(portfolio.py 도입부) — 강등 앵커.
PATENT_ANCHOR = "특허 선례: between_ic≈±0.42 / within_ic≈0 → 정적특성 강등(타이밍 아님)"


def oof_bullish_scores(
    model,
    X: np.ndarray,
    y: np.ndarray,
    folds,
) -> np.ndarray:
    """walk-forward out-of-fold bullish score(행 정렬). 미검정 행(첫 청크)은 nan.

    각 폴드에서 train 으로 적합 → test 행에만 score 기록. evaluate_model 과 동일한
    degenerate-fold(단일 클래스 train) 스킵 규약 → 그 test 행은 nan 으로 남아 분해에서 빠진다.
    """
    oof = np.full(len(y), np.nan, dtype=float)
    for fold in folds:
        Xtr, ytr = X[fold.train_idx], y[fold.train_idx]
        if len(np.unique(ytr)) < 2 or len(fold.test_idx) == 0:
            continue
        est = clone(model)
        est.fit(Xtr, ytr)
        oof[fold.test_idx] = _bullish_score(est, X[fold.test_idx])
    return oof


@dataclass
class FeatureCell:
    name: str
    between_ic: float
    within_ic: float
    within_p: float
    within_q: float = float("nan")  # BH 보정 후 채움


@dataclass
class GateReport:
    model_name: str
    n_obs: int                 # OOF 신호가 있는 관측치 수
    n_firms: int
    model_rank_ic_xs: float    # sanity: confirmed +0.128 재현 여부
    model_decomp: Decomposition
    model_within_p: float
    model_within_null_mean: float
    model_within_q: float
    features: list[FeatureCell] = field(default_factory=list)
    verdict: str = ""


def _make_verdict(decomp: Decomposition, within_q: float) -> str:
    if np.isnan(decomp.within_ic):
        return "판정 보류 — within 관측 부족(기업당 관측 <min_obs_per_firm)"
    significant = not np.isnan(within_q) and within_q < 0.05
    if significant and abs(decomp.within_ic) >= 0.03:
        return (
            f"🟢 timing 승격 후보 — within_ic={decomp.within_ic:+.3f} (BH q={within_q:.3f} 유의). "
            "다음: DSR/t≥3 경제가치 검정."
        )
    if abs(decomp.within_ic) < 0.03 and not significant:
        return (
            f"🔴 정적특성 강등 — within_ic={decomp.within_ic:+.3f}≈0, between_ic="
            f"{decomp.between_ic:+.3f}. 특허 선례와 동형(타이밍 알파 아님)."
        )
    return (
        f"🟡 모호 — within_ic={decomp.within_ic:+.3f} (BH q={within_q:.3f}). "
        "표본/피처셋 확대 후 재판정."
    )


def gate_report(
    ds: Dataset,
    *,
    model_name: str = "decision_tree",
    n_folds: int = 5,
    seed: int = 42,
    n_perm: int = 200,
    min_obs_per_firm: int = 2,
    embargo_days: int = 0,
) -> GateReport:
    """매출 나우캐스트 Dataset 을 within-firm 게이트에 통과시켜 판정 리포트를 만든다.

    ``embargo_days>0``: OOF 신호 생성 폴드를 **purge(embargo)** 한다. 월별 신호화처럼 같은 분기
    라벨이 여러 as_of 에 중복되면 non-purged walk-forward 는 train/test 에 같은 outcome 을 걸쳐
    OOF 가 누수된다 → 분기폭(~95일) embargo 로 막는다. quarterly(중복 없음)는 0 이면 충분.
    """
    folds = (
        purged_walk_forward_folds(ds.dates, n_folds=n_folds, embargo_days=embargo_days)
        if embargo_days > 0 else walk_forward_folds(ds.dates, n_folds=n_folds)
    )
    model = build_classifier_registry(seed=seed)[model_name]

    # sanity: confirmed 모델 rankIC(+0.128) 재현?
    model_rank_ic_xs = evaluate_model(
        model_name, model, ds.X, ds.y, ds.excess_returns, folds, ds.dates
    )._agg("rank_ic_xs")[0]

    # (주) OOF 횡단면 score 를 신호로 between/within 분해.
    oof = oof_bullish_scores(model, ds.X, ds.y, folds)
    mask = ~np.isnan(oof)
    sig, lab, sid = oof[mask], ds.excess_returns[mask], ds.stock_ids[mask]
    model_decomp = between_within_ic(sig, lab, sid, min_obs_per_firm=min_obs_per_firm)
    m_obs, m_p, m_null = within_ic_permutation(
        sig, lab, sid, n_perm=n_perm, seed=seed, min_obs_per_firm=min_obs_per_firm
    )

    # (보조) 피처별 원시신호 분해(CV 불필요 — 고정 신호).
    features: list[FeatureCell] = []
    for j, name in enumerate(ds.feature_names):
        fsig = ds.X[:, j]
        d = between_within_ic(
            fsig, ds.excess_returns, ds.stock_ids, min_obs_per_firm=min_obs_per_firm
        )
        _, fp, _ = within_ic_permutation(
            fsig, ds.excess_returns, ds.stock_ids,
            n_perm=n_perm, seed=seed, min_obs_per_firm=min_obs_per_firm,
        )
        features.append(FeatureCell(name, d.between_ic, d.within_ic, fp))

    # BH-FDR: {모델 OOF within} ∪ {피처별 within} 전체 패밀리에 보정.
    pvals = [m_p] + [f.within_p for f in features]
    bh = benjamini_hochberg(pvals, alpha=0.05)
    model_within_q = bh.qvalues[0]
    for f, q in zip(features, bh.qvalues[1:]):
        f.within_q = q

    return GateReport(
        model_name=model_name,
        n_obs=int(mask.sum()),
        n_firms=model_decomp.n_firms,
        model_rank_ic_xs=model_rank_ic_xs,
        model_decomp=model_decomp,
        model_within_p=m_p,
        model_within_null_mean=m_null,
        model_within_q=model_within_q,
        features=features,
        verdict=_make_verdict(model_decomp, model_within_q),
    )


def render_gate(r: GateReport) -> str:
    """게이트 리포트를 콘솔/리포트용 텍스트로."""
    L = []
    L.append(f"[within-firm gate] model={r.model_name}  n_obs={r.n_obs}  "
             f"n_firms={r.n_firms}  min_obs/firm 필터 적용")
    L.append(f"  sanity: model rank_ic_xs = {r.model_rank_ic_xs:+.3f} "
             f"(confirmed 기준 ~+0.128)")
    d = r.model_decomp
    L.append("")
    L.append("  ── 주 신호(모델 OOF score) 분해 ──")
    L.append(f"    between_ic(정적) = {d.between_ic:+.3f}")
    L.append(f"    within_ic (timing) = {d.within_ic:+.3f}  "
             f"perm-p={r.model_within_p:.3f}  BH-q={r.model_within_q:.3f}  "
             f"(null_mean={r.model_within_null_mean:+.3f}, "
             f"n_within_obs={d.n_within_obs}, n_firms_within={d.n_firms_within})")
    L.append(f"    앵커 — {PATENT_ANCHOR}")
    L.append("")
    L.append("  ── 보조: 피처별 분해(within_q 오름차순) ──")
    L.append(f"    {'feature':>22}  {'between':>8}  {'within':>8}  {'perm-p':>7}  {'BH-q':>6}")
    for f in sorted(r.features, key=lambda x: (x.within_q if x.within_q == x.within_q else 1.0)):
        L.append(f"    {f.name:>22}  {f.between_ic:>+8.3f}  {f.within_ic:>+8.3f}  "
                 f"{f.within_p:>7.3f}  {f.within_q:>6.3f}")
    L.append("")
    L.append(f"  VERDICT: {r.verdict}")
    return "\n".join(L)


__all__ = [
    "oof_bullish_scores",
    "gate_report",
    "render_gate",
    "GateReport",
    "FeatureCell",
    "PATENT_ANCHOR",
]
