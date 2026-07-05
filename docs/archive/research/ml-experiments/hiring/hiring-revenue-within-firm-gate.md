# 채용→매출 나우캐스트 — within-firm 게이트 (timing vs 정적특성) (2026-07-06)

> confirmed 채용→차기분기 매출 YoY 나우캐스트(clean 94종목·분기·purge·월별 3검증 모두
> BH-FDR 생존, decision_tree rankIC **+0.128**)에 **within-firm 분해가 미적용**이었다. 이 rankIC 가
> **(a) 트레이더블 timing**(기업이 *자기 평소보다* 더 뽑을 때 *자기 평소보다* 매출이 더 크는가)인지
> **(b) 정적 종목특성**(원래 많이 뽑고 원래 잘 크는 기업들의 횡단면 상관)인지 이 게이트가 가른다.
>
> **선례 경보:** 특허→실현변동성 매그니튜드 신호는 바로 이 게이트에서
> `between_ic≈±0.42 / within_ic≈0` → **정적특성으로 강등**(타이밍 알파 아님)됐다
> (`app/ml/research/portfolio.py` 도입부). 채용 매출 신호도 동일 함정 가능성이 있어,
> 융합·포트폴리오 등 후속 전 **선결 게이트**다.
>
> **상태(2026-07-06): 게이트 코드·테스트 완비·합성검증 통과. 실 94종목 라이브 판정은
> 데이터/크리덴셜(.env·revenue CSV) 있는 실행 머신에서 대기 중** — 아래 §5 명령 1줄.

---

## 1. 판별 원리

라벨 = 매출 YoY 성장률(`Dataset.excess_returns` 슬롯; **주가 방향 아님** — 방향/매그니튜드는
기각·재시도 금지). 각 행 = (종목, 분기). 신호를 두 성분으로 분해해 각각 Spearman rankIC:

- **between_ic (정적)**: 기업별 (평균 signal, 평균 label) 한 점씩 → 기업 간 rankIC.
  "원래 많이 뽑는 기업이 원래 매출 잘 크나?"
- **within_ic (timing)**: 기업평균을 뺀 `(signal−ḡ_f, label−L̄_f)` 를 풀링 → rankIC.
  "한 기업이 자기 평소보다 더 뽑은 분기에 자기 평소보다 매출이 더 컸나?" — **이것만이 트레이더블.**

관측 1개 기업은 demean 하면 항상 (0,0) 이라 within 을 인위적으로 부풀리므로 `min_obs_per_firm=2`
로 제외한다.

## 2. 방법론 (handoff §7 준수)

- **신호 정의**: (주) 최적모델(decision_tree)의 **walk-forward OOF 횡단면 score** — confirmed
  +0.128 을 낸 바로 그 신호. purge/embargo 는 `walk_forward_folds` 의 날짜경계 분할이 보장.
  (보조) 지배적 rich 피처를 각각 원시신호로 분해 → 어느 피처가 timing/static 인지.
- **유의성**: 소표본 analytic-t 금지 → **permutation**. 기업 블록 **내에서만** 라벨을 셔플하면
  각 기업 라벨평균(=between 구조)은 보존한 채 within 짝짓기만 파괴 → within_ic 의 정확한 귀무분포.
  one-sided(null ≥ obs) p.
- **다중검정**: {모델 OOF within} ∪ {피처별 within} 패밀리 전체에 **BH-FDR**(Bonferroni 아님).
- **판정 규칙**:
  - `within_ic` 유의(BH q<0.05) & `|within_ic|≥0.03` → 🟢 **timing 승격 후보** → 다음 DSR/t≥3.
  - `|within_ic|≈0` & 비유의 → 🔴 **정적특성 강등**(특허 선례와 동형, 타이밍 알파 아님).
  - 그 외 → 🟡 모호(표본/피처셋 확대 후 재판정).

## 3. 구현 (신규)

| 파일 | 역할 |
| --- | --- |
| `app/ml/research/within_firm.py` | 순수 numpy 분해 — `between_within_ic`, `within_ic_permutation`, `firm_demean` |
| `app/ml/research/within_firm_gate.py` | 하니스 접합 — OOF score 생성 + `gate_report` + `render_gate` |
| `scripts/within_firm_hiring_revenue.py` | CLI(`load_from_env` → 게이트) |
| `tests/test_ml_within_firm.py` · `tests/test_ml_within_firm_gate.py` | 합성 static/timing 분리 검증(8케이스) |

## 4. 합성 검증 (코드가 실제로 timing/static 을 가르는가)

라벨 = `기업레벨(static) + 1.2·within편차(timing) + 노이즈`, 피처 = {정적 `x_static`, timing
`x_timing`} 패널에서 게이트 실행 결과:

```
── 주 신호(모델 OOF score) ──   within_ic=+0.473  BH-q=0.000  → 🟢 timing
── 보조: 피처별 ──
  duty_ai_share(=timing)  between −0.152  within +0.978  BH-q 0.000   (timing)
  vol_zscore(=static)     between +0.999  within −0.041  BH-q 0.690   (static)
```

⇒ 분해가 timing 피처(within↑·유의)와 static 피처(between↑·within≈0·비유의)를 정확히 반대로
가른다. (전체 ML 스위트 111 테스트 GREEN.)

## 5. 실 데이터 라이브 판정 (실행 머신에서 1줄)

작업 디렉터리 `services/agent-worker`, `.env` 에 `DATABASE_URL`(Supabase prod)·매출 CSV 필요:

```bash
python scripts/within_firm_hiring_revenue.py \
  --tickers <clean-KOSPI200> --revenue-csv revenue_dart.csv \
  --signal-freq quarterly --feature-set volume+duty --precise-rematch \
  --min-obs 2 --min-cross-section 6 --n-perm 200
```

판정 결과(between_ic·within_ic·BH-q·verdict)를 이 문서 §6 에 채우고
[[hiring-revenue-nowcast-signal]] 메모리를 갱신할 것. 특허 앵커(`between≈±0.42/within≈0`)와 대조.

## 5b. MCP-오프라인 실행 경로 (이 머신, DB·uv 없이)

이 머신엔 `DATABASE_URL`·`uv`·산출 CSV 가 없어 §5(load_from_env)를 못 쓴다. 대신 **Supabase MCP**
로 prod 를 읽고(채용은 DB에 온전: `hiring_raw_details` 7,842건, `stocks` 208), 매출만 OpenDART
에서 새로 인출한다. 매출은 prod DB에 없음(`fundamentals`·`dart_raw_details` 0행 — handoff §2의
"의도적 우회"). **필요한 외부 시크릿은 `DART_API_KEY` 하나뿐.** 전 과정 anaconda python 로 동작.

신규 코드: `app/ml/research/hiring_mcp_offline.py`(precise rematch 재현 — 기존 `unique_norm_map`/
`_norm_name` 재사용) + `scripts/within_firm_hiring_revenue_offline.py`(덤프→게이트).

유니버스(2026-07-06 도출): distinct source_name(175) rematch → **7,644/7,842 매칭**, `≥15` 포스팅
= **97 티커**(confirmed clean-94 와 근접; 잔차는 아카이브 `universe_kospi200.csv` 큐레이션 차이).
드롭 198건은 비-exact 변형(예: "포스코"/"실리콘웍스"/"에코프로"/"현대중공업")로 precise 규약대로 제외.

실행(작업 디렉터리 services/agent-worker):
```bash
# 1) 매출 CSV 인출(DART 키 필요, ~수십분 rate-limited)
python -m app.ml.research.fundamentals_dart --tickers <clean-97> \
  --start-year 2016 --end-year 2024 --out <scratch>/revenue_dart.csv
# 2) 채용 포스팅 덤프(Supabase MCP → hiring_postings.jsonl)  ※ 에이전트가 수행
# 3) 게이트
python scripts/within_firm_hiring_revenue_offline.py \
  --stocks-json <scratch>/stocks.json --postings-jsonl <scratch>/hiring_postings.jsonl \
  --revenue-csv <scratch>/revenue_dart.csv --universe-json <scratch>/universe_clean15.json \
  --feature-set volume+duty --n-perm 200
```

## 6. 실 데이터 결과 (대기 중 — `DART_API_KEY` 필요)

> 준비 완료: 하니스·게이트·오프라인 로더·유니버스(97) 확정, 채용 데이터 MCP 접근 확인.
> **남은 것: DART 키로 매출 CSV 생성 → 위 명령 실행.** 출력의 between_ic/within_ic/BH-q·피처표·
> verdict 를 여기 기입하고 [[hiring-revenue-nowcast-signal]] 갱신. 특허 앵커(between≈±0.42/within≈0) 대조.

## 7. 주의

- research 도구 = **신호 확정 전 main 머지 금지**(백업 브랜치 `research/hiring-ml-step1` push 만).
  산출 CSV 커밋 금지.
- 하니스는 현 HEAD 부재 → 복구 커밋 `2949f48` 에서 checkout(§0 런북 동일).
