"""Alignment tests for the daily publication-lag event study (Stage 7).

The non-negotiable property (Stage 6 had an offset-by-one alignment bug): in the
search profile, **offset 0 must be the move day**, and a search spike placed
exactly on the move day must surface at offset 0 — not -1 or +1. The lag sign in
the cross-corr must also be honest: lag>0 means search LEADS the return.
"""

from __future__ import annotations

from datetime import date, timedelta

from scripts.event_study_daily import daily_crosscorr, search_profile


def _event(move_day: str):
    return {"ticker": "005930", "week_date": "2020-01-10", "keyword": "kw", "move_day": move_day}


def test_profile_peak_lands_on_move_day():
    move = date(2020, 1, 8)
    # Flat search except a clear spike exactly on the move day.
    series = {move + timedelta(days=o): (100.0 if o == 0 else 10.0) for o in range(-10, 11)}
    daily_by_event = {"005930|2020-01-10": series}
    prof, used = search_profile([_event(move.isoformat())], daily_by_event, k=10)
    assert used == 1
    means = {o: prof[o][0] for o in prof}
    peak = max((o for o in means if means[o] is not None), key=lambda o: means[o])
    assert peak == 0  # spike on move day -> peak at offset 0, NOT -1/+1


def test_crosscorr_lag_sign_search_leads_return():
    move = date(2020, 1, 8)
    days = [move + timedelta(days=o) for o in range(-10, 11)]
    # A VARIED (non-degenerate) search series so consecutive momenta aren't
    # artificially correlated — otherwise lag 0 would also hit |corr|=1.
    series = {d: 10.0 + (i * 7 % 13) for i, d in enumerate(days)}
    daily_by_event = {"005930|2020-01-10": series}
    # Return mirrors the SAME day's search momentum, placed one day LATER:
    # so search momentum at d "predicts" the return at d+1 -> peak at lag +1.
    mom_by_day = {}
    prev = None
    for d in days:
        if prev is not None and series[prev]:
            mom_by_day[d] = series[d] / series[prev] - 1.0
        prev = d
    excess = {d + timedelta(days=1): m * 100.0 for d, m in mom_by_day.items()}
    cc, used = daily_crosscorr([_event(move.isoformat())], daily_by_event, {"005930": excess}, k=5)
    assert used == 1
    c1 = cc[1][0]
    assert c1 is not None and c1 > 0.95  # the engineered lead is exactly at lag +1
    # lag +1 (search leads) is the strongest lag among all.
    best_lag = max((lag for lag in cc if cc[lag][0] is not None), key=lambda lag: cc[lag][0])
    assert best_lag == 1
