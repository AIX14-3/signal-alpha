# 제안: 시장 레짐 태깅 레이어 (opt-in · 비판정 근거 레이어)

> 작성 2026-07-07 · 담당(대체데이터) 이슬 · 상태: 설계 초안 + 러너블 스텁(기본 OFF)
> 근거: 채용→매출 "개발자 채용 타이밍" 신호가 **섹터 파도**였음을 증명(섹터-중립화 시 소멸) + 기존 에이전트 불변식(`dart/evidence.py`, DataLab cause 태그)

---

## 한 줄 결론

**시장/섹터 흐름의 CONTROL은 결정론(숫자)이 소유하고, LLM은 그 위에 레짐 TAG(근거/맥락)만 얹는다 — 판정/점수/방향은 절대 만들지 않는다.** 기본 OFF → 프로덕션 동작 불변.

---

## 1. 동기 — 섹터 교란(confound)의 교훈

채용→매출 "개발자 채용 타이밍" 엣지는 종목 고유 신호처럼 보였으나, **섹터-중립화(sector-neutralization)를 적용하자 소멸**했다. 즉 우리가 잡았던 건 종목의 timing이 아니라 그 종목이 올라탄 **섹터 파도**였다.

교훈은 두 갈래다.

1. **CONTROL은 결정론이어야 한다.** 섹터/시장 흐름 제거(중립화)는 숫자 연산이다 — LLM이 개입할 자리가 아니다. 이 통제를 놓치면 어떤 소스든 섹터 베타를 알파로 오인한다.
2. **LLM의 정당한 역할은 레짐 TAGGING이다.** "지금은 AI capex 붐 국면" 같은 국면 인식은 사람이 읽는 맥락/근거로서 가치가 있다 — 단, 그것이 숫자(방향/점수)로 새어들어가면 안 된다.

이 레이어는 이 분리를 **코드로 강제**한다: 숫자는 결정론 PIT 피처(섹터수익률 분산 등)가 소유하고, LLM은 그 위에 태그+근거만 붙인다.

## 2. 레짐 분류 체계 (taxonomy)

작고 상호 구별 가능한 최소 enum. `neutral`이 안전 기본값이며, enum 밖 응답은 전부 `label=None`으로 결정론 강등된다.

| 라벨 | 의미 | 대표 결정론 지문 |
|---|---|---|
| `ai_capex_boom` | AI/반도체 capex 주도의 섹터 쏠림 위험선호 | 섹터수익률 **분산 高**(소수 섹터 독주) |
| `rate_tightening` | 금리 상승/긴축 지배 국면 | 금리 프록시 상승 · 성장주 약세 |
| `credit_stress` | 신용 스프레드 확대/유동성 경색 | 스프레드 확대 · 광범위 하락 |
| `risk_on` | 분산된 광범위 위험선호 | 섹터수익률 **평균 高 · 분산 低** |
| `neutral` | 뚜렷한 레짐 없음(기본값) | 지문 미약 / 미결정 |

> enum은 의도적으로 작게 유지한다. LLM이 매핑 못 하는 상태는 `neutral`이 아니라 **`None`(태그 폐기)**로 떨어뜨려, "억지 태그"보다 "무태그"를 선택한다(`dart/evidence.py`가 실패 시 근거 블록을 생략하는 것과 동일).

## 3. 불변식 — 비판정(non-verdict)

기존 두 에이전트를 그대로 미러링한다.

| 자산 | 판정(방향/점수) | LLM 산출물 | 실패 시 |
|---|---|---|---|
| `dart/evidence.py` | **미채택** | summary/key_facts/risk_flags → `method_detail` additive | 근거 블록 생략(결정론) |
| DataLab cause 태그 | **불변** | cause 태그 + rationale, score/direction 복사만 | 태그=None, 규칙 예비판정 |
| **regime 태그(본 제안)** | **없음(애초 산출 안 함)** | `RegimeTag`(label + rationale + confidence + model) | `label=None` 결정론 강등 |

`RegimeTag`에는 `score`/`direction`/`verdict`/`target_price` 필드가 **존재하지 않는다**(dataclass 레벨에서 원천 차단). `confidence`는 display/audit용 provenance일 뿐 **부호 있는 숫자가 아니며 ML 피처로 읽지 않는다**. 프롬프트는 매수/매도·목표주가를 금지하고, `_reject_investment_advice` 아이디어(정규식 가드)로 rationale에 투자조언 표현이 감지되면 **태그를 폐기**(`label=None`)한다.

## 4. 두 채널 통합 (핵심 설계)

레짐은 **두 개의 완전히 분리된 채널**로 흐른다. LLM 출력은 채널 A에만 존재하고, **채널 B로 절대 읽어오지 않는다**(DataLab의 ML-경계 규칙 `agent.py:174-178`과 동일 — LLM per-run 산출물은 display/audit, ML 피처는 point-in-time 재계산).

| | 채널 A — 근거 카드 | 채널 B — 결정론 PIT 피처 |
|---|---|---|
| 산출 | `classify_regime(context, *, client)` → `RegimeTag` | `regime_features(asof, *, sector_return_rows)` → `regime__*` |
| 성질 | LLM, 비결정적, opt-in | 순수 함수, 결정론, PIT |
| 소비처 | `method_detail["regime_evidence"]`(표시/감사) | `source_features.assemble_features` → 메타러너 |
| 기본값 | OFF(`REGIME_LLM_ENABLED`) | 미주입(`regime_rows=None`) |
| 숫자 반영 | **금지** | **여기서만** |

```
                (결정론 입력: 섹터수익률/매크로 프록시)
                          │
          ┌───────────────┴────────────────┐
   [채널 B] regime_features(asof)     [채널 A] classify_regime(context)
     → regime__sector_return_*          → RegimeTag(label, rationale)
          │                                    │
   assemble_features → 메타러너          method_detail["regime_evidence"]
   (숫자 = 결정론 소유)                   (표시/감사 전용 · 숫자 미반영)
```

### 4-A. 근거 카드 → `method_detail`

`build_regime_tagger(settings)` 팩토리가 `RegimeClassifier`를 반환(또는 OFF 시 None). 호출부는 `RegimeTag`를 받아 **복사한** `method_detail`에 `regime_evidence` 키만 additive로 붙인다(기존 키/점수 불변). 이는 스텁이며 아직 어떤 프로덕션 핸들러에도 배선되지 않았다 — `evidence.py`처럼 "호출될 수 있는 헬퍼"로만 존재한다.

### 4-B. 결정론 PIT 레짐 피처 → `assemble_features`

`regime_features`는 순수 함수로 섹터별 일간 수익률에서 PIT 프록시를 산출한다:

| 피처 키 | 정의 |
|---|---|
| `regime__sector_return_dispersion` | 최신일 섹터수익률 표준편차(**쏠림 강도** — 섹터 파도의 지문) |
| `regime__sector_return_spread` | 최신일 max-min |
| `regime__sector_return_mean` | 최신일 평균(광범위 방향성 프록시) |
| `regime__sector_count` | 최신일 섹터 표본수(커버리지) |

`assemble_features(asof, …, regime_rows=None)`에 **additive·off-by-default** hook을 두었다: `regime_rows`를 넘긴 호출자에게만 `"regime"` 블록을 추가하고, 기존 호출자(`regime_rows=None`)에게는 출력이 **byte-identical**하다. 조인 지점은 `source_features.py`의 반환 dict 조립부(price 블록 다음)이며, `KNOWN_AT`의 PIT 게이트와 같은 규율(`date > asof` 행 제거)을 `regime_features` 내부에서 강제한다.

> **레짐 피처 정의는 결정론에서 출발한다.** v1 프록시는 횡단면 섹터수익률 분산 / 매크로 프록시 — 순수 숫자다. LLM은 그 위에 **rationale만** 얹는다(정의를 대체하지 않는다). 이것이 §1 교훈의 핵심 구현: 섹터 교란 통제는 숫자가, 국면 서사는 LLM이.

## 5. 게이트 · 폴백 · 가드레일

- **Env 게이트**: `regime_use_llm = _env_bool("REGIME_LLM_ENABLED", default=False)` (기존 `dart_use_llm` 관용구 그대로). 팩토리는 플래그 OFF **또는** Gemini 키 부재 시 `None` 반환 → OFF 경로 byte-identical.
- **결정론 폴백**: transport/JSON 예외, 비-dict 페이로드, enum 밖 라벨, 투자조언 rationale — **전부** `RegimeTag(label=None, error=…)`로 강등(예외 재발생 없음). 호출부는 "무태그"를 OFF 경로와 동일하게 처리.
- **모델 재사용**: `GeminiJsonClient.generate_json`(temperature 0.2, responseMimeType JSON, bounded retry, `.model` provenance) 재사용. 새 transport 없음.
- **프롬프트 가드**: 매수/매도·목표주가·방향/점수 전망 금지 명시 + 정규식 후검(`_PROHIBITED_ADVICE`).

## 6. 향후 작업 — 정직한 하니스 검증

레짐 **피처**(채널 B)가 메타러너에서 lift를 준다는 믿음은 다른 대체데이터와 **동일한 정직한 하니스**를 통과한 뒤에만 채택한다:

- **BH-FDR**: sweep-wide 다중검정 보정(채용→매출이 94종목 생존 후 117 확대서 marginal 된 선례 준수).
- **Held-out**: 사전등록 스윕 + held-out 확인.
- **Shuffle/permutation**: 라벨 셔플 null 대비.
- **Within-firm + sector-neutral**: ⚠️ 이 레이어의 존재 이유가 섹터 교란이므로, 레짐 피처가 **섹터-중립화 후에도** 살아남는지가 최종 관문(특허→변동성이 within-firm 붕괴한 선례). 레짐 피처가 단지 섹터 베타의 다른 표현이라면 기각한다.

LLM 태그(채널 A)는 이 검증과 무관하게 display/audit로만 남으며, 숫자 경로에 영향을 주지 않으므로 검증 대상이 아니다.

## 7. 재사용 자산 & 스코프

재사용: `clients/gemini_client.py`(LLM), `agents/dart/evidence.py`·DataLab cause(비판정 선례), `ml/source_features.py`(PIT 어셈블리), `core/config.py`(env 관용구).

**스코프**: 대체데이터 신호 경로만. DART/main-server 프로덕션 분석기는 건드리지 않는다. 본 제안의 스텁은 어떤 핸들러에도 배선되지 않았고(기본 OFF), 실런/머지는 하니스 신호 확보 후 별도 결정.

## 부록: 산출물

- 설계: 본 문서
- 스텁 코드: `app/agents/regime/{__init__,classifier,features}.py`, `app/core/config.py`(플래그), `app/ml/source_features.py`(additive hook), `docker-compose.yml`(env 전달)
- 테스트: `tests/test_regime_agent.py`(팩토리 OFF·malformed 강등·비판정·PIT·backward-compat)
