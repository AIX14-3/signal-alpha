# Prediction Harness (검증 파이프라인)

예측 점수가 실제 주가와 얼마나 일치하는지 측정하는 하네스입니다.
**노선: 신뢰도 우선 재설계** — KOSPI200 × 10년 횡단면 상대 순위 + 보정표 + 포워드 섀도.
계획: `docs/superpowers/plans/2026-06-11-claude-reliability-first-design.md`
(구 30종목·가중치 튜닝 노선은 폐기 — `2026-06-11-prediction-harness-plan.md`는 역사 기록)

## 구성

| 모듈 | 역할 |
| --- | --- |
| `universe.py` | KOSPI200 스냅샷 로더 (`data/universe_kospi200_*.csv`, 버전 커밋) |
| `snapshot_universe.py` | KOSPI200 구성종목 스냅샷 생성 (pykrx, 1회 실행 후 커밋) |
| `collect_panel.py` | pykrx 일봉+수급 10년 수집 → 종목 샤드 + `data/panel_kospi200.parquet` |
| `collect_fundamentals.py` | DART 정형 재무 (2015~), **available_date(공시일)** 포함 point-in-time |
| `panel.py` | 패널 로드 + 미래수익률(fwd_ret_N) 부착 — lookahead 차단 지점 |
| `splits.py` | 학습60/검증20/최종20 시간 분할 (최종 구간은 `--unlock-final` 없이 접근 불가) + 워크포워드 |
| `metrics.py` | 지표 3종(방향 적중률 · 일별 Spearman IC · 분위 스프레드) + 순열 검정(날짜 내 셔플) |
| `baseline_score.py` | Phase 0 배관 검증용 PRICE-lite 점수 (실분석기 연결 전 임시) |
| `backtest.py` | 러너 CLI — 실행마다 `experiments.jsonl`에 1줄 기록 |

## 사용법 (`harness/` 디렉터리에서 — 다른 서비스와 동일한 실행 규칙)

```powershell
cd harness

# 0) 유니버스 스냅샷 (최초 1회, 결과 CSV 커밋)
uv run python -m signal_alpha_harness.snapshot_universe

# 1) 패널 수집 (KOSPI200 × 10년, 30~40분 — 중단해도 샤드부터 재개)
uv run python -m signal_alpha_harness.collect_panel --years 10

# 1-b) DART 재무 수집 (2015~, 1~2시간, DART_API_KEY 필요)
uv run python -m signal_alpha_harness.collect_fundamentals

# 2) 학습 구간 백테스트 (순열 검정 500회 포함)
uv run python -m signal_alpha_harness.backtest --segment train --note "baseline"

# 3) 채택 판정은 검증 구간으로
uv run python -m signal_alpha_harness.backtest --segment valid --note "w_flow 0.15→0.2"

# 워크포워드 국면표 (train+valid 내부, 반년 단위)
uv run python -m signal_alpha_harness.backtest --walk-forward

# 최종 구간 — 루프 종료 후 단 1회만!
uv run python -m signal_alpha_harness.backtest --segment final --unlock-final
```

## 루프 규칙 (요약)

- 회차당 파라미터 1개만 변경, `--note`에 무엇을 바꿨는지 기록
- 채택 = 3지표 동시 개선(또는 2개선+1유지) **AND** perm_p < 0.05
- `experiments.jsonl`은 append-only — 지우지 말 것
- 자세한 규칙·종료 조건은 계획 문서 5장 참조

## experiments.jsonl 포맷

```json
{"ts": "...", "scorer": "...", "segment": "train|valid|final|walk_forward",
 "note": "바꾼 것 1개", "panel": "...", "permutations": 500,
 "result": {"n_days": 444, "h5": {"hit_rate": ..., "mean_ic": ..., "ic_positive_share": ...,
            "quantile_spread": ..., "permutation_p": ...}, "h20": {...}}}
```
