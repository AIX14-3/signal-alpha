"""Registry gates: backtest whitelist (gate_passed) + per-host availability."""

from __future__ import annotations

from app.ml import model_registry as reg


def test_default_gate_is_ml3_plus_tcn_and_excludes_gpu_and_lightgbm() -> None:
    names = reg.gate_passed_names()
    # ML 3종(ewma/har_rv/garch) + DL 1종(tcn). LightGBM 은 candidate 이지만 기본 제외.
    assert names == ["ewma", "har_rv", "garch", "tcn"]
    assert "lightgbm" not in names
    assert "kronos" not in names
    assert "chronos2" not in names


def test_lightgbm_still_a_candidate_but_off_by_default() -> None:
    # vendored 파일/candidate 는 남아 env override 로 재활성화 가능(기본 게이트에만 빠짐).
    assert "lightgbm" in {s.name for s in reg.all_specs()}


def test_env_override_and_unknown_names_filtered(monkeypatch) -> None:
    monkeypatch.setenv("ML_GATE_PASSED_MODELS", "ewma, har_rv , bogus_model")
    # unknown 'bogus_model' is dropped; known names kept in order
    assert reg.gate_passed_names() == ["ewma", "har_rv"]


def test_pure_numpy_models_always_available() -> None:
    by_name = {s.name: s for s in reg.all_specs()}
    assert by_name["ewma"].is_available() is True
    assert by_name["har_rv"].is_available() is True


def test_gpu_models_unavailable_without_backend() -> None:
    # torch may be installed, but the Kronos repo / chronos-forecasting are not,
    # so HAVE_LIB is False and the availability gate excludes them.
    by_name = {s.name: s for s in reg.all_specs()}
    assert by_name["chronos2"].is_available() is False
    assert by_name["kronos"].is_available() is False


def test_resolve_models_applies_both_gates() -> None:
    # explicit subset still subject to gates; pure-numpy pair is always resolvable
    resolved = reg.resolve_models(enabled=["ewma", "har_rv"])
    assert [s.name for s in resolved] == ["ewma", "har_rv"]

    # a GPU model can never resolve on a CPU-only host even if asked for
    assert reg.resolve_models(enabled=["chronos2"]) == []
