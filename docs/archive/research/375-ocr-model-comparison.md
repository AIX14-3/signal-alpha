# OCR 모델 비교 — Hiring skill enrichment (#375)

> 상태: **선정 완료(2026-06-24) — Tesseract(kor+eng) 채택**. §4 비교표·§4.1 근거 참고.
> 관련: [#375](https://github.com/AIX14-3/signal-alpha/issues/375) ·
> [#390](https://github.com/AIX14-3/signal-alpha/issues/390)(Phase 1 벤치마크·선정) ·
> 설계 [hiring-skill-enrichment-design.md](../spec/hiring-skill-enrichment-design.md)

## 1. 목적

한국 대기업 공채의 **자격요건/우대사항은 대부분 포스터 이미지**로 게시된다(자소설 상세
`content` 8/8 표본이 `<img>` 뿐 — 텍스트 ~0자). 세부 기술(C#·SQL·ERP/MES·Embedded·ISP 등)을
얻으려면 **이미지 OCR → 텍스트 → 기술 키워드 추출**이 필요하다(설계 §2-A).

본 문서는 그 OCR 단계에 쓸 **엔진을 선정**하기 위한 후보 비교다. 분석 본체가 아니라
enrichment(Phase 1)의 부품 선택이다.

## 2. 후보 평가 — 오픈소스 5종 → 최종 3종

> **클라우드 OCR(Google Vision·Naver CLOVA·AWS Textract 등)과 Gemini Flash VLM 엔드투엔드는
> 비용 문제로 제외.** 2020~ 전체 backfill(수만 공고)에 건당 API 과금이 누적되고 외부 의존·
> 쿼터 리스크가 크다. → **오픈소스 self-host로 한정**(1회 backfill + 지속 enrichment에 적합).

오픈소스 5종을 **라이선스·한국어 기술적 타당성** 1차 데스크 게이트로 거른 결과:

| # | 엔진 | 한국어 | 라이선스 | 런타임/백엔드 | 판정 |
|---|---|---|---|---|---|
| 1 | **PaddleOCR (PP-OCRv6)** | 지원(korean rec) | Apache-2.0 | PaddlePaddle (CPU/GPU) | ✅ **선정** — 표·밀집 레이아웃·대량처리 강점(PP-Structure) |
| 2 | **Tesseract** | `kor.traineddata` | Apache-2.0 | 네이티브 C++ (pytesseract) | ✅ **선정(베이스라인)** — 경량·CPU, 반드시 이겨야 할 하한 |
| 3 | **Surya** | 90+ 언어(한국어 포함) | GPL-3.0 / 상업조건(확인 권장) | PyTorch (GPU 권장) | ✅ **선정** — 레이아웃·읽기순서·표 강점(정확·느림) |
| 4 | **KLOCR** | 한국어 특화 | **CC-BY-NC-SA-4.0 (비상용)** | torch (HuggingFace) | ❌ **탈락 — 라이선스** |
| 5 | **DocTR** (Mindee) | **한국어 vocab 미지원** | Apache-2.0 | PyTorch/TF | ❌ **탈락 — 기술적 타당성** |

### 2.1 탈락 (게이트에서 제외)
- **KLOCR — 라이선스 탈락.** HuggingFace 배포 라이선스가 **CC-BY-NC-SA-4.0**으로 **비상용(NC)
  조항**을 포함 → 상업적 사용 불가. 한국어 정확도가 좋더라도 라이선스 게이트에서 탈락.
- **DocTR — 기술적 타당성 탈락.** 한국어 인식 **vocab 미지원**(다국어 인식이 라틴/유럽어 중심)
  → 한국 포스터 OCR에 부적합. 정확도 측정 이전에 기술 게이트에서 탈락.

### 2.2 최종 벤치마크 대상 (3종)
> **PaddleOCR (PP-OCRv6) · Surya · Tesseract** — §3 방법론으로 정량 측정한다.
> (Surya 의 상업 라이선스 조건은 채택 확정 전 최종 확인.)

## 3. 평가 방법론

### 3.1 평가셋 (ground truth)
- **자소설 포스터 이미지 20~30장 손라벨.** 다양성 확보: 밀집 표 / 저해상도 / 영문 혼용 /
  세로쓰기 등 포함. 라벨 = 그 이미지에 등장하는 **기술 키워드 집합**(자격요건만).
- **개인정보 미수집**(자소서·지원자 식별정보 제외, 설계 §4). 이미지 출처 = Phase 0가 보존할
  `extra_payload.image_url`.

### 3.2 지표
| 지표 | 정의 |
|---|---|
| 기술키워드 **precision/recall** (kor/eng 분리) | 사전 매칭된 기술 키워드 기준. **1차 선정 축** |
| **CER**(문자오류율) | 원문 텍스트 정확도(레이아웃 무관 하위지표) |
| 표/레이아웃 보존 | 표 행·열 구조가 텍스트 순서로 보존되는지(정성) |
| **속도** (s/img) | CPU·GPU 각각. 대량 backfill 처리량 추정 |
| 메모리 / 설치 난이도 | self-host 운영비 |
| 라이선스 | 상업적 사용 가능 여부(게이트) |

### 3.3 하니스(예정)
```
extra_payload.image_url (Phase 0)  → 이미지 다운로드(로컬 캐시)
  → 엔진별 실행(동일 입력)  → 텍스트
  → 기술 키워드 사전 매칭(base_site.TECH_KEYWORDS 확장)  → P/R 집계
```
- 모든 엔진 **동일 평가셋·동일 키워드 사전**으로 측정(차이는 OCR 정보만).
- 엔진 의존성은 격리 설치(uv `--with` / 분리 venv)로 충돌 회피.

## 4. 비교표 (측정 완료 — 2026-06-24)

> 평가셋 = 자소설 IT/개발 포스터 **라벨 5장**(GT 기술키워드 집합, 빈 라벨 포스터 제외).
> 지표 = **micro precision/recall/F1**(사전 매칭된 기술키워드 기준, kor+eng 합산) +
> **median per-image 레이턴시**. 환경 = **CPU**(Windows, GPU 미사용). 워밍업(첫 이미지 1회 폐기)으로
> 모델 로드 비용 분리. 하니스: [`scripts/research/ocr_harness.py`](../../services/agent-worker/scripts/research/ocr_harness.py).
> CER·표보존은 이번 라운드 미측정(1차 선정축 = 기술키워드 P/R/F1).

| 모델 | micro P | micro R | **micro F1** | median 속도 | 라이선스 | 비고 |
|---|---|---|---|---|---|---|
| **Tesseract** (kor+eng) | 0.870 | 0.741 | **0.80** | **~3.0s/img** | Apache-2.0 | ✅ **선정** — 최고 F1·최속·CPU 경량 |
| PaddleOCR (PP-OCRv5, server det) | 0.816 | 0.574 | 0.674 | ~112s/img | Apache-2.0 | recall↓(긴 포스터 누락) · CPU 매우 느림 |
| Surya | — | — | — | — | GPL-3.0/상업조건 | ⛔ 환경 차단(미측정) · CPU 실익 낮음 · 후속 불필요 |

> 주1: PaddleOCR 속도는 PP-OCRv5 **server** det + CPU(oneDNN 우회) 기준이라 과도하게 느림 —
> 경량 mobile det로 개선 여지 있으나 recall 열위가 선정 결론을 바꾸지 않음.
> 주2: Surya 는 uv ephemeral 설치가 `sympy` 빌드캐시 락(`os error 32`, 점유 프로세스 권한상승/재부팅
> 필요)으로 측정 불가. GPU 권장 엔진이라 현 CPU 운영 환경에선 실익이 낮아 **후속 측정 생략**.
> KLOCR(라이선스 NC)·DocTR(한국어 vocab 미지원)은 §2.1에서 탈락 → 측정 대상 제외.

### 4.1 선정 결과 — **Tesseract(kor+eng) 확정**

게이트 순서(§5: ① 라이선스 → ② 한국어 P/R → ③ self-host 비용) 적용:
- **Tesseract — 채택.** Apache-2.0(게이트 통과). 5장 micro **F1 0.80**으로 최고, **~3s/img**로 최속,
  CPU 단독·설치 경량. 운영 조건: `kor`+`eng` traineddata 배치 + `TESSDATA_PREFIX` 지정.
- **PaddleOCR — 보류.** Apache-2.0이나 **F1 0.674로 베이스라인(Tesseract)을 유의하게 못 이김**
  (recall 0.574, 긴 포스터 누락 多) + CPU 레이턴시 비현실적(server det ~112s/img). §5 "베이스라인
  못 이기면 보류" 규칙 적용. (GPU + mobile det 재검토는 대량 backfill 처리량이 문제될 때만.)
- **Surya — 후속 불필요.** **환경 차단으로 미측정**(uv sympy 빌드캐시 락). GPU 권장 엔진이라
  **CPU 운영 환경에서 실익 낮음** → 재측정 생략, GPU 도입 시에만 재검토.

**결론:** Phase 1 enrichment OCR 엔진 = **Tesseract(kor+eng) 단독 채택**. PaddleOCR·Surya 는 보류.

## 5. 선정 기준 / 다음 단계

**게이트 순서:** ① 라이선스(상업적 사용 가능) → ② 한국어 P/R → ③ self-host 비용(속도·메모리).
정확도가 좋아도 라이선스 불가면 탈락. 베이스라인(Tesseract)을 유의하게 못 이기면 그 엔진은 보류.

**다음 단계:**
1. ~~**Phase 0** — `image_url` 을 `extra_payload` 에 보존~~ (완료).
2. ~~평가셋 이미지 수집·손라벨~~ (자소설 포스터 5장 라벨 완료, [`labels_draft.json`](../../services/agent-worker/data/eval_set/) — 향후 20~30장 확대 여지).
3. ~~하니스 실행 → §4 표 채움~~ (Tesseract·PaddleOCR 측정 완료, Surya 환경 차단·생략 — §4).
4. **(완료) 채택 = Tesseract(kor+eng).** → **다음:** Phase 1 `ENRICH_HIRING_SKILL` 잡 구현
   (하니스의 `extract_skills`/사전 재사용, 엔진 = Tesseract).
