# 특허 ML Stage 2~4 — enrich(블록)·이벤트스터디·나우캐스팅·다중소스 융합 (2026-06-26)

> count 기반 특허 방향성 무신호(엄밀 재검증) 후, 사용자 요청으로 Stage 2~4 진행: ②LLM enrich 어블레이션, ③이벤트 스터디+나우캐스팅 재프레이밍, ④다중소스(특허·채용·DataLab) 융합.
> 
> **한 줄 결론: ②enrich는 BigQuery 무료 quota 소진으로 블록(미검정). ③특허 공개는 방향(CAR ±0.3%·|t|<2)·크기(vol_post/pre≈0.98) 모두 비-이벤트. ④융합도 무신호(patent+hiring 27종목 비겹침 rankIC_xs 최고 +0.032=잡음; 3소스는 9종목으로 표본부족). 특허는 단독·융합·재프레이밍 어디서도 알파 없음.**

---

## Stage 2 — LLM enrich 어블레이션 (블록)

- **목표**: 누락 26종목 enrich 후 significance/novelty 피처로 LLM 가설 검정(사용자 질문 #4).
- **블로커**: `enrich_patents_llm.py`는 BigQuery에서 특허 abstract를 인출 → **BQ 무료 quota 소진**(`403 Quota exceeded: free query bytes scanned`, project patent-bq-reader=unbilled). 그동안 backfill+enrich로 월 1TB 스캔 한도 초과.
- **조치**: enrich 스크립트를 **500건마다 커밋**하도록 개선(기존=끝에 1회→중단 시 전손) → quota 리셋/billing 후 청크 재개 가능. pending=77,037건(24종목).
- **상태**: 미검정. quota 리셋(월간) 또는 billing 활성화 시 재개. count artifact 확인 후 우선순위 낮음.
## Stage 3 — 이벤트 스터디 + 나우캐스팅 (무신호)

신규 `scripts/patent_event_study.py`: 공개일(publication_date) ±20거래일 CAR(초과수익=종목−KS11). prices CSV+DB만(BQ 불필요).

| 버킷 | n | CAR[0..+20] | post t | vol_post/pre |
| --- | --- | --- | --- | --- |
| ALL | 8041 | −0.14% | −1.63 | 0.976 |
| single | 4835 | −0.06% | −0.76 | 0.984 |
| burst(≥5건/일) | 3206 | −0.25% | −1.84 | 0.965 |
| sig_hi(enriched) | 1197 | +0.22% | +1.27 | 0.982 |
| sig_lo | 507 | +0.24% | +0.76 | 0.976 |

- **방향**: 공개일 0 부근 CAR 점프 없음, 전 버킷 |t|<2. 고significance만 미약한 양(+0.22%, t=1.27)이나 비유의.
- **크기(나우캐스팅)**: vol_post/pre 전부 ~0.97~0.98(≈1, 오히려 약간↓) → 공개가 변동성 증가도 선행 안 함.
- **해석**: 특허 공개는 ~18개월 지연·저salience(뉴스 아님) → 시장 무반응. DataLab 어텐션이 거래량을 나우캐스팅한 것(S9)과 대조 — 특허엔 실시간 salience가 없어 나우캐스팅도 불가.
## Stage 4 — 다중소스 융합 (무신호)

신규 `app/ml/research/fusion_db.py` + `bakeoff --source fusion`: 세 소스를 **동일 신호일 그리드**(같은 prices·signal_step)로 로드해 (종목,날짜) inner-join, 피처 concat, **날짜내 전체 rank 정규화**. 비겹침(signal-step=horizon).

| 융합 | 종목 | 표본 | 최고 rankIC_xs | 판정 |
| --- | --- | --- | --- | --- |
| patent+hiring | 27 | 396 | extra_trees +0.032(sd 0.050) | 무신호(산포·대부분 음·Dbase≤0) |
| patent+hiring+datalab | 9 | 138 | stacking +0.369(단일 outlier) | 표본부족(날짜당 ~4종목)·타 모델 ~0 = 잡음 |

- 교집합: 세 소스 공통 21종목·특허∩채용 32종목. 단 3소스 inner-join은 datalab 제약+희소 hiring으로 9종목까지 축소 → 횡단면 무의미.
- patent+hiring(27종목)은 표본 충분하나 무신호(최고 +0.032=1sd 내).
## 종합 판정

특허는 **단독(count·LLM 미검정)·이벤트·나우캐스팅·다중소스 융합** 어디서도 방향/크기 알파 없음. [[attention-lead-lag-evidence]]·[[ml-bakeoff-datalab-result]]와 일관. 남은 유일한 미검정 = LLM significance/novelty(BQ quota 블록).

## 산출물 (UNTRACKED/미커밋)

- 신규: `scripts/patent_event_study.py`, `app/ml/research/fusion_db.py`(+bakeoff `--source fusion`/`--fusion-sources`). 개선: `scripts/enrich_patents_llm.py`(500건 커밋 재개성).
- 재현: 이벤트=`patent_event_study.py --prices-csv prices34.csv --benchmark KS11 --tickers <34> --window 20`. 융합=`bakeoff --source fusion --fusion-sources patent,hiring --tickers <34> --lookback 60 --horizon 20 --band 0.3 --folds 3 --signal-step 20 --prices-csv prices34.csv --benchmark KS11`.
- 테스트 ML 22 GREEN, ruff clean.
## 다음 단계

- [ ] (조건부) BQ quota 리셋/billing 후 enrich 재개 → LLM 어블레이션으로 Stage 2 완료.
- [ ] 특허 트랙 사실상 종료. 대체데이터 ML은 [[attention-spike-flag-design]](검색→매그니튜드) 등 *나우캐스팅/제품화*가 유일하게 견고. 방향성 알파는 대형주 대체데이터 단독/융합에서 미발견.

---

관련: [[patent-ml-rejected]] · 직전 `2026-06-26-patent-ml-rigorous-reverify.md` · [[ml-bakeoff-datalab-result]] · [[attention-lead-lag-evidence]]
