# SEC EDGAR 공시 수집 타당성 스파이크

해외(미국) 공시를 DART처럼 가져올 수 있는지 검증하는 스파이크.
**DB·ORM 없이 fetch/parse만** 확인한다. 데이터가 정상 수신되면 → 테이블 생성 → ORM 단계로 진행.

> 설계 배경: `Desktop/signal-alpha-수집설계도/해외공시_데이터수집_계획.md` (SEC EDGAR = 미국판 DART)

## 무엇을 하나
1. `company_tickers.json` 으로 **티커 → CIK** 매핑
2. `data.sec.gov/submissions/CIK##########.json` 으로 **최근 공시 목록** 조회
3. form / 제출일 / accession / 원문 문서 URL 파싱·출력

## 실행
```bash
uv run python spikes/sec-edgar/fetch_filings.py                      # 기본: NVDA AMD MSFT
uv run python spikes/sec-edgar/fetch_filings.py NVDA AAPL TSM        # 특정 티커
uv run python spikes/sec-edgar/fetch_filings.py NVDA --forms 8-K,10-K,10-Q --limit 10
```

- SEC는 **연락처 포함 `User-Agent` 헤더를 요구**한다(없으면 차단). 환경변수 `SEC_USER_AGENT`로 교체 가능 — 운영 시 팀 공용 주소로 바꿀 것.
- 권장 호출률 ~10 req/s. 스파이크는 요청 간 0.2s 간격을 둔다.

## 검증 결과 (2026-06-16)
- ✅ `company_tickers.json` 로드: 약 10,400개 티커
- ✅ NVDA / AMD / MSFT: 최근 10-Q·8-K·10-K 정상 수신(제출일·accession·원문 URL 포함)
- ✅ 비상장(OPENAI 등): CIK 미발견으로 graceful 처리 → 상장 상대방 공시로 우회 필요(설계서 §4)
- ⇒ **데이터 정상 수신 확인. 다음 단계(테이블·ORM) 착수 가능.**

## 다음 단계 (이 스파이크 이후)
1. **테이블**: `database/migrations/NNN_sec_filings.sql` — 공시 목록 적재 스키마
   (cik, ticker, form, filing_date, accession_no, primary_doc, doc_url, fetched_at …)
2. **ORM/리포지토리**: `packages/data-access` 패턴으로 SEC filings 리포지토리
3. **수집기**: `services/agent-worker/app/collectors/sec/`(`cik_map.py` / `filings.py`) — `collectors/dart` 미러
4. **신호 emit**: 공통 신호 스키마(`source:"sec"`, `evidence_refs`에 accession/form) — `DART_LangChain_데이터준비_계획.md` §3

## Form ↔ DART 대응 (참고)
| SEC | 의미 | DART |
|---|---|---|
| 10-K / 10-Q | 연차/분기 보고서 | 사업·분기보고서 |
| 8-K | 수시 중요사건 | 수시공시 |
| Form 4 | 내부자 거래 | 임원·주요주주 소유 |
| SC 13D/13G | 5%+ 대량보유 | majorstock |
| 20-F / 6-K | 외국기업(ADR) | TSMC 등 |
