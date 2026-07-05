"""within-firm 분해 — 횡단면 신호가 트레이더블 timing이냐 정적 종목특성이냐 판별.

confirmed 채용→차기분기 매출 나우캐스트 rankIC(decision_tree +0.128)가

  (a) **timing**  — 한 기업이 *자기 평소보다* 더 뽑을 때 *자기 평소보다* 매출이 더 크는가,
  (b) **정적특성** — 원래 많이 뽑고 원래 잘 크는 기업들의 횡단면 상관일 뿐인가

를 구분한다. (a)만이 트레이더블 알파다. 특허→실현변동성 매그니튜드 선례가 바로 이 게이트에서
``between_ic≈±0.42 / within_ic≈0`` → **정적특성으로 강등**(타이밍 아님)됐다(``portfolio.py`` 도입부).
채용 매출 신호도 같은 함정 가능성이 있어, within_ic가 0 부근이고 permutation에서 비유의하면
채용도 "정적 특성"으로 강등한다.

분해(라벨 = 매출 YoY 성장률, ``Dataset.excess_returns`` 슬롯):

  between_ic : 기업별 (평균 signal, 평균 label) 쌍의 Spearman rankIC = 정적 종목특성.
  within_ic  : 기업내 demean 한 (signal−ḡ_f, label−L̄_f) 을 풀링한 Spearman rankIC = timing.

within_ic 유의성은 **소표본 analytic-t 금지** 원칙에 따라 permutation(기업 블록 내 라벨 셔플)로만
잰다 — 셔플은 각 기업의 라벨 평균(∴ between 구조)을 보존한 채 within-firm 짝짓기만 파괴하므로
within_ic의 정확한 귀무분포가 된다.

순수 numpy(+scipy.stats.spearmanr). RNG는 seed로 명시 주입 → 결정론적 단위테스트.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import spearmanr


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman rankIC that returns nan (not raise) on degenerate input.

    <3 points or zero variance in either side → 분해가 무의미하므로 nan.
    """
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    try:
        return float(spearmanr(a, b).statistic)
    except Exception:
        return float("nan")


def firm_means(values: np.ndarray, stock_ids: np.ndarray) -> dict[int, float]:
    """{stock_id: 그 기업 관측치들의 평균} — between/within 성분의 공통 재료."""
    out: dict[int, float] = {}
    for fid in np.unique(stock_ids):
        out[int(fid)] = float(values[stock_ids == fid].mean())
    return out


def firm_demean(
    values: np.ndarray, stock_ids: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """행별 (기업평균 뺀 within 성분, 그 행이 속한 기업평균) 을 반환.

    ``within = values − mean_{f(row)}`` 이 timing 성분, 두 번째 배열이 정적 성분(between)이다.
    """
    means = firm_means(values, stock_ids)
    firm_mean_per_row = np.array([means[int(f)] for f in stock_ids], dtype=float)
    return values - firm_mean_per_row, firm_mean_per_row


@dataclass(frozen=True)
class Decomposition:
    between_ic: float  # 기업별 평균끼리의 rankIC — 정적 종목특성
    within_ic: float   # 기업내 demean rankIC — 트레이더블 timing
    n_firms: int             # between 에 쓰인 기업 수(관측치 ≥1)
    n_firms_within: int      # within 에 기여한 기업 수(관측치 ≥ min_obs_per_firm)
    n_within_obs: int        # within 에 쓰인 총 관측치 수

    @property
    def verdict(self) -> str:
        """within_ic 크기 기준 1차 판정(유의성은 permutation p로 별도 확정)."""
        if np.isnan(self.within_ic):
            return "indeterminate (within 관측 부족)"
        if abs(self.within_ic) < 0.03:
            return "정적특성 강등 후보 (within_ic≈0)"
        return "timing 후보 (within_ic≠0 — permutation 유의성 확인 필요)"


def _within_arrays(
    signal: np.ndarray,
    label: np.ndarray,
    stock_ids: np.ndarray,
    min_obs_per_firm: int,
) -> tuple[np.ndarray, np.ndarray, int]:
    """min_obs_per_firm 이상인 기업만 골라 within(=demean) signal·label 을 풀링해 반환.

    관측치 1개 기업은 demean 하면 항상 (0,0) 이라 within 상관을 인위적으로 부풀리므로 제외.
    반환: (within_signal, within_label, 기여 기업 수).
    """
    keep_firms = [
        int(f)
        for f in np.unique(stock_ids)
        if int((stock_ids == f).sum()) >= min_obs_per_firm
    ]
    mask = np.isin(stock_ids, keep_firms)
    if not mask.any():
        return np.empty(0), np.empty(0), 0
    s, l, sid = signal[mask], label[mask], stock_ids[mask]
    ws, _ = firm_demean(s, sid)
    wl, _ = firm_demean(l, sid)
    return ws, wl, len(keep_firms)


def between_within_ic(
    signal: np.ndarray,
    label: np.ndarray,
    stock_ids: np.ndarray,
    *,
    min_obs_per_firm: int = 2,
) -> Decomposition:
    """신호를 between(정적) / within(timing) 성분으로 분해해 각 rankIC 를 잰다.

    between : 기업마다 (평균 signal, 평균 label) 한 점씩 → 기업 간 Spearman.
    within  : min_obs_per_firm 이상 기업에서 기업평균을 뺀 값들을 풀링 → Spearman.
    """
    signal = np.asarray(signal, dtype=float)
    label = np.asarray(label, dtype=float)
    stock_ids = np.asarray(stock_ids)

    sig_means = firm_means(signal, stock_ids)
    lab_means = firm_means(label, stock_ids)
    firms = sorted(sig_means)
    between = _spearman(
        np.array([sig_means[f] for f in firms]),
        np.array([lab_means[f] for f in firms]),
    )

    ws, wl, n_firms_within = _within_arrays(
        signal, label, stock_ids, min_obs_per_firm
    )
    within = _spearman(ws, wl)

    return Decomposition(
        between_ic=between,
        within_ic=within,
        n_firms=len(firms),
        n_firms_within=n_firms_within,
        n_within_obs=len(ws),
    )


def within_ic_permutation(
    signal: np.ndarray,
    label: np.ndarray,
    stock_ids: np.ndarray,
    *,
    n_perm: int = 200,
    seed: int = 42,
    min_obs_per_firm: int = 2,
) -> tuple[float, float, float]:
    """within_ic 의 permutation 유의성 — 기업 블록 내에서만 라벨을 셔플.

    각 기업의 라벨 값을 그 기업 행들 사이에서만 재배열하면 기업 라벨평균(=between)은
    그대로 두고 within-firm 짝짓기만 파괴 → within_ic 의 정확한 귀무분포. 관측된 within_ic
    가 양(+)의 timing 가설이므로 **one-sided**(null ≥ obs) p 값을 낸다.

    반환: (observed_within_ic, p_value, null_mean). obs 가 nan(관측 부족)이면 (nan,nan,nan).
    """
    signal = np.asarray(signal, dtype=float)
    label = np.asarray(label, dtype=float)
    stock_ids = np.asarray(stock_ids)

    obs = between_within_ic(
        signal, label, stock_ids, min_obs_per_firm=min_obs_per_firm
    ).within_ic
    if np.isnan(obs):
        return float("nan"), float("nan"), float("nan")

    firm_rows = {int(f): np.where(stock_ids == f)[0] for f in np.unique(stock_ids)}
    rng = np.random.default_rng(seed)
    null: list[float] = []
    for _ in range(n_perm):
        permuted = label.copy()
        for rows in firm_rows.values():
            if len(rows) > 1:
                permuted[rows] = label[rng.permutation(rows)]
        r = between_within_ic(
            signal, permuted, stock_ids, min_obs_per_firm=min_obs_per_firm
        ).within_ic
        if not np.isnan(r):
            null.append(r)
    arr = np.array(null)
    pval = float((arr >= obs).mean()) if len(arr) else float("nan")
    null_mean = float(arr.mean()) if len(arr) else float("nan")
    return obs, pval, null_mean


__all__ = [
    "Decomposition",
    "firm_means",
    "firm_demean",
    "between_within_ic",
    "within_ic_permutation",
]
