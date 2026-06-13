# 퀀트 점수 엔진 — 검증 결과 시각화 리포트

Signal α 퀀트 점수 엔진(KOSPI200 횡단면 상대 순위 백분위)의 Phase 0~6 검증 결과를 시각화한 자료 모음입니다. 신뢰도 우선 설계(가중치 튜닝 루프 없이 검증된 팩터를 고정 등가중)에 따른 백테스트·OOS 검증 산출물을 담습니다.

> 계획 전문: `plans/stateless-gliding-rain.md` · 점수 엔진 코드: `packages/signal-core/signal_core/quant/`

## 차트 인덱스

| 파일 | 제목 | 설명 |
|------|------|------|
| `0_pipeline.svg` | Phase 0~6 검증 파이프라인 | 데이터 수집 → 팩터 선별 → 결합·보정 → OOS 검증 → 서비스 통합 전체 흐름 (수작업 다이어그램) |
| `1_factor_ic.svg` | 단일 팩터 정보계수 (IC) | 팩터 6종의 단독 IC와 채택/탈락 게이트 결과 |
| `2_calibration.svg` | 보정표 (Calibration) | 점수 10구간별 20일 초과수익 분포(중앙값·사분위 밴드) |
| `3_walkforward.svg` | 워크포워드 검증 | 14개 6개월 윈도의 OOS IC 추이 (11/14 양수) |
| `4_regime_ic.svg` | 국면별 정보계수 | 상승/횡보/하락 국면별 IC 분해 |

## 핵심 수치 요약

| 지표 | 값 | 비고 |
|------|----|----|
| 채택 팩터 | reversal_1m · lowvol_60 · quality_margin_yoy | 단독 IC 게이트 통과 3종 (등가중 고정) |
| 결합 IC (h20, train+valid) | **+0.0466** | 순열검정 p = 0.002, 관측치 342,820 |
| 보정표 단조성 (Spearman) | **+0.915** | top &gt; bottom, 단조성 게이트 PASS |
| valid IC20 (20% 홀드아웃) | **+0.0353** | p = 0.004 |
| 워크포워드 | **11 / 14 윈도 양수** | 음수 윈도 = 코로나·2차전지 광풍 국면 |
| 국면별 IC (bull/flat/bear) | +0.054 / +0.045 / +0.031 | **하락장 포함 전 국면 양수** |
| 유니버스 | KOSPI200 199종목 × 10년 | Point-in-Time (DART `available_date` 기준) |

## 한계 (보고서 명시 사항)

- **보정표 전 버킷 median 음수**: 시장 기준이 등가중 평균(우상향 왜도)이라 절대값이 음수로 나타남 — 점수 구간 간 *상대* 단조성이 검증 대상. Phase 6 표시 시 시장 중앙값 기준 재검토 항목.
- **분위 스프레드 음수**: lowvol 팩터의 비선형성에서 기인. IC는 양수이나 극단 분위 단순 차이는 음수일 수 있음.
- **신뢰도 미확정**: 포워드 섀도 4~8주 누적 전까지는 최종 신뢰도 증거 미완. final 20% 홀드아웃은 모든 수정 종료 후 단 1회만 개봉.
- **서빙 확신도 상한 B**: `fundamentals` 테이블에 공시일 컬럼 부재 → 서빙은 가격계 2팩터만 사용, 확신도 상한 B로 제한 (후속 스키마 보강 과제).

## 재생성 방법

차트 4종(matplotlib)은 harness 환경에서 재생성합니다.

```bash
# harness/ 디렉터리에서
python -m uv run --with matplotlib <스크립트>
```

- 폰트: `Malgun Gothic` + `matplotlib.rcParams['axes.unicode_minus'] = False` (한글·음수 부호 처리)
- 데이터 원천: `harness/experiments.jsonl` (IC·게이트 기록) + `harness/data/calibration.parquet` (보정표)
- `0_pipeline.svg`는 수작업 SVG로, 코드 생성물이 아닙니다. 모듈명·수치 변경 시 직접 편집하세요.
