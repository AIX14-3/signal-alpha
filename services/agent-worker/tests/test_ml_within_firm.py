"""within_firm 분해 단위테스트 — 정적특성 vs timing 을 실제로 가르는지 검증.

합성 데이터로 두 극단을 만든다:
  (A) 순수 정적 : signal·label 이 둘 다 기업 고유 레벨만 따라감(within 무상관)
                  → between_ic 높음, within_ic≈0, permutation 비유의.
  (B) 순수 timing: label 이 기업내 signal 편차만 따라감(기업 레벨과 무관)
                  → within_ic 높음, between_ic≈0, permutation 유의.
분해가 이 둘을 뒤집지 않아야 채용 매출 게이트의 판정을 믿을 수 있다.
"""

from __future__ import annotations

import numpy as np

from app.ml.research.within_firm import (
    between_within_ic,
    firm_demean,
    firm_means,
    within_ic_permutation,
)


def _static_panel(n_firms=40, obs=8, seed=0):
    """정적: 기업레벨 L_f 가 signal·label 을 동시에 끌고, within 편차는 서로 독립."""
    rng = np.random.default_rng(seed)
    levels = rng.normal(0, 3, n_firms)  # 넓게 퍼진 기업 고유 레벨
    sig, lab, sid = [], [], []
    for f in range(n_firms):
        for _ in range(obs):
            sig.append(levels[f] + rng.normal(0, 1))  # within 노이즈
            lab.append(levels[f] + rng.normal(0, 1))  # 독립 within 노이즈
            sid.append(f)
    return np.array(sig), np.array(lab), np.array(sid)


def _timing_panel(n_firms=40, obs=8, seed=0):
    """timing: 라벨은 기업내 signal 편차 d_i 에만 반응, 기업 레벨과는 무관."""
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 3, n_firms)  # signal 의 기업 레벨(라벨과 무관해야 함)
    sig, lab, sid = [], [], []
    for f in range(n_firms):
        devs = rng.normal(0, 1, obs)
        devs -= devs.mean()  # 기업내 zero-mean 편차
        for d in devs:
            sig.append(base[f] + d)
            lab.append(2.0 * d + rng.normal(0, 0.3))  # within 편차만 추종
            sid.append(f)
    return np.array(sig), np.array(lab), np.array(sid)


def test_firm_demean_is_exact():
    sid = np.array([1, 1, 2, 2, 2])
    vals = np.array([10.0, 20.0, 1.0, 2.0, 3.0])
    within, means = firm_demean(vals, sid)
    assert firm_means(vals, sid) == {1: 15.0, 2: 2.0}
    np.testing.assert_allclose(means, [15, 15, 2, 2, 2])
    np.testing.assert_allclose(within, [-5, 5, -1, 0, 1])


def test_static_panel_is_between_not_within():
    sig, lab, sid = _static_panel()
    d = between_within_ic(sig, lab, sid)
    assert d.between_ic > 0.5           # 정적 횡단면 상관 강함
    assert abs(d.within_ic) < 0.15      # timing 성분 거의 없음
    assert "정적특성" in d.verdict


def test_timing_panel_is_within_not_between():
    sig, lab, sid = _timing_panel()
    d = between_within_ic(sig, lab, sid)
    assert d.within_ic > 0.5            # timing 성분 강함
    assert abs(d.between_ic) < 0.35     # 기업 레벨은 라벨과 무관
    assert d.n_firms_within == 40
    assert "timing" in d.verdict


def test_permutation_flags_timing_significant_static_not():
    sig_t, lab_t, sid_t = _timing_panel()
    obs_t, p_t, _ = within_ic_permutation(sig_t, lab_t, sid_t, n_perm=200, seed=1)
    assert obs_t > 0.5
    assert p_t < 0.05                   # timing 은 유의

    sig_s, lab_s, sid_s = _static_panel()
    obs_s, p_s, null_s = within_ic_permutation(sig_s, lab_s, sid_s, n_perm=200, seed=1)
    assert p_s > 0.10                   # 정적특성은 비유의
    assert abs(null_s) < 0.1            # 셔플 귀무 평균은 0 부근


def test_single_obs_firms_excluded_from_within():
    # 관측 1개짜리 기업은 within(demean=0)에서 빠지고 between 에만 남는다.
    sig = np.array([1.0, 2.0, 5.0, 9.0])
    lab = np.array([1.0, 2.0, 5.0, 9.0])
    sid = np.array([1, 1, 2, 3])  # 기업2·3 은 관측 1개
    d = between_within_ic(sig, lab, sid, min_obs_per_firm=2)
    assert d.n_firms == 3
    assert d.n_firms_within == 1
    assert d.n_within_obs == 2


def test_degenerate_returns_nan_not_raise():
    sig = np.array([1.0, 1.0, 1.0])
    lab = np.array([1.0, 2.0, 3.0])
    sid = np.array([1, 1, 1])
    d = between_within_ic(sig, lab, sid)
    assert np.isnan(d.between_ic)  # 기업 1개 → between 무의미
