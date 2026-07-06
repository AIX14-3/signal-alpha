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

## 6. 실 데이터 결과 (2026-07-06 실행 완료 — 🟡 모호, 정적붕괴 아님)

**실행 조건**: MCP-오프라인 경로(`within_firm_hiring_revenue_offline.py`). Supabase MCP 로 뽑은
stocks(208)·HIRING 포스팅(7,842, precise rematch 7,644/7,842) 덤프 + OpenDART revenue CSV
(97종목 요청, 매출계정 있는 **91종목** 확정 — 금융주 6개는 '매출액' 계정 부재로 자연 제외).
`--feature-set volume+duty --n-perm 200`. 표본 = 748 (stock,quarter), 84종목, 28분기.

### 6.1 주 신호(decision_tree OOF score) 분해 — 🟡 모호

| 지표 | 값 | 판정 |
| --- | --- | --- |
| sanity `rank_ic_xs` | **+0.106** | confirmed +0.128 근사 재현 ✅ |
| `between_ic` (정적) | **+0.147** | 특허 앵커(±0.42) 대비 **훨씬 작음** |
| `within_ic` (timing) | **+0.059** | 0 아님(≥0.03)이나… |
| `within` perm-p / BH-q | 0.095 / **0.333** | **비유의** (q≫0.05) |

⇒ **VERDICT: 🟡 모호** — within_ic 가 0 부근이 아니라 **양(+0.059)** 이므로 특허식 정적붕괴
(within≈0)와 **동형이 아니다**. 그러나 이 표본에서 BH 유의(q<0.05)에는 미달 → timing 승격도 불가.

### 6.2 피처별 분해 — `tech_share`(직무믹스)는 유의한 within-firm timing

| feature | between | within | perm-p | BH-q |
| --- | --- | --- | --- | --- |
| **`hiring__tech_share`** | **−0.071** | **+0.074** | **0.000** | **0.000** ✅ |
| `hiring__days_since_latest` | −0.055 | −0.138 | 1.000 | 1.000 |
| 그 외(deseason/tech_share_mom·yoy/yoy_change) | nan | nan | — | 1.000 |

⇒ 지배 피처 **tech_share** 는 between 이 **음(−0.071)** = 정적 종목특성이 아니고, within=+0.074 로
**유의한 within-firm 타이밍**(BH-q=0.000). "자기 평소보다 tech 직군을 더 뽑은 분기 → 자기 평소보다
차기 매출↑". 채용→매출 채널의 timing 성분이 **직무믹스에 실재**함을 시사.

### 6.3 사전등록 스윕(loop) 교차검증 — grid-wide BH 에서 0 생존(정직 NULL), 단 near-miss 전부 양

`run_feature_search --source revenue-offline --grid full`(24셀: label×{all,hr_duty}×{raw,within_firm_z}
×{logistic,ridge,lda,hist_grad_boost,random_forest,decision_tree}, perm=300). **FDR 생존 0**
(최소 perm_p=**0.040** < BH 문턱 ~0.004 미달). 그러나 near-miss 상위셀은 **전부 rank_ic 양
(+0.08~0.15)·within_firm_ic 양(+0.05~0.19)**, 특히 **`within_firm_z` 변환(정적특성 제거) 후에도
양의 신호 잔존** → 6.1/6.2 와 일치(약한 within-firm 타이밍 실재, 다중검정 후 유의 미달).

### 6.4 종합 판정

- **특허 매그니튜드처럼 정적특성으로 붕괴하지 않았다**(between 작음·within 양·within_firm_z 후 잔존).
- 그러나 **트레이더블 timing 으로 승격하기엔 약하다**(모델 within BH-q=0.333, 스윕 grid-wide 0 생존).
- 유일하게 깨끗이 유의한 timing = **tech_share**(피처패밀리 BH q=0.000).
- ⇒ **🟡 모호/유망**. 채널은 살아있고 방향은 timing 쪽이나, 이 표본(quarterly·91종목·28분기)에선
  보정 후 미달. 다음 레버 = **§4 Step2(월별×깨끗한 KOSPI200 확대)** 로 표본을 키워 재판정
  (tech_share 를 앵커 피처로). 매그니튜드/나우캐스트 가치는 유지되나 timing 알파는 **미확정**.
  cf. [[hiring-revenue-nowcast-signal]]·[[patent-volatility-magnitude-signal]](정적붕괴 대조군).

## 6.5 Step 2 — 월별 확대 + 누수-안전 재판정 (2026-07-07) → 🔴 정적특성 확정

quarterly 가 표본 병목(28분기)이었으므로 **월별 신호화**(`--revenue-signal-step-days 30`: 같은 분기
라벨을 as_of {분기말,−30,−60}로 3배 샘플링, 횡단면 28→~84)로 검정력을 키웠다. **누수 2중 방어**:
(a) 스윕 purge embargo=95일(revenue 라벨 horizon 63) — 같은 분기 라벨이 train/test 걸치는 것 차단,
(b) **within-firm 게이트 OOF 폴드도 purge**(`gate_report(embargo_days=)` 신설) — 게이트 자체 누수 차단.

**누수 안전 대조(결정적)**: 매출을 종목 간 derangement(고정점0)로 뒤섞은 **셔플 CSV 월별 스윕 =
FDR 생존 0**. 즉 월별·중복라벨 구조가 **가짜신호를 만들지 않음**을 실증 → 실런 생존은 진짜.

**실런(월별, 24셀, perm=300)**:
- **FDR 생존 5 · 홀드아웃 확정 3** (quarterly 0 → 월별서 신호 견고화; sweep-wide p≤0.00997).
  생존자 전부 `within_firm_z` 변환 tree(random_forest/hist_grad_boost/decision_tree).
- **purge-안전 within-firm 게이트: 확정 3개 전부 🔴 정적특성 강등** —
  **between_ic=+0.252 / within_ic=+0.029≈0** (누수 제거하니 between 이 더 뚜렷·within≈0). 특허 앵커
  (between≈±0.42/within≈0)와 **동형**.

**종합(Step 2 확정)**: 채용→차기분기 매출 **횡단면 나우캐스트 신호는 견고**(월별서 BH+홀드아웃 통과,
셔플-대조로 누수 배제)하나, **within-firm 분해에선 정적 종목특성 = 트레이더블 timing 아님**. 6.2 의
quarterly tech_share within(+0.074) 은 피처 단위 잔존 timing 이나, 모델 종합신호·월별 확대판에서는
static 이 지배. ⇒ **대체데이터 가치 = 매출 레벨 나우캐스팅(횡단면 magnitude), 방향 timing 알파 아님**
— [[altdata-direction-signal-wall]]·[[patent-volatility-magnitude-signal]] 과 일관. 채용→매출 timing
추격은 **종료**(park); 나우캐스트 용도는 유지. ⚠️월별은 pseudo-replication(3×중복라벨)으로 perm p 가
낙관적일 수 있음 — 구조 판정(🔴 static)이 유의성 수치보다 robust 한 결론.

하니스: `--revenue-signal-step-days`(fundamentals_dataset.build_revenue_dataset `signal_step_days`),
게이트 purge=`within_firm_gate.gate_report(embargo_days)`. 브랜치 `research/source-agnostic-search`.

## 7. 주의

- research 도구 = **신호 확정 전 main 머지 금지**(백업 브랜치 `research/hiring-ml-step1` push 만).
  산출 CSV 커밋 금지.
- 하니스는 현 HEAD 부재 → 복구 커밋 `2949f48` 에서 checkout(§0 런북 동일).
