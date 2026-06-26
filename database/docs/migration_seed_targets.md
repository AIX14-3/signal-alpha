# 마이그레이션 / 시드 타깃 규칙 (2-인스턴스 분리)

> 수집(워커) DB 와 백엔드(서비스) DB 를 **물리적으로 분리된 Postgres 인스턴스 2개**로 운영한다.
> 모든 마이그레이션·시드는 어느 DB 로 갈지 `-- target:` 헤더로 **명시**한다. 미선언은 금지
> (러너 기본값이 `collection` 이라, 백엔드 마이그가 실수로 수집 DB 로 샌다).

## 타깃 3종

| target | 의미 | 적용 DB |
|---|---|---|
| `collection` | 수집 raw + 워커 파이프라인 내부 | 수집 DB 만 |
| `backend` | 회원·세션·구독·결제·관리자·약관·유저콘텐츠 | 백엔드 DB 만 |
| `all` | PUBLISHED 발행 테이블 + 공유 인프라(api view·롤·타입·함수) | 양쪽 |

`migrate.py apply --target collection` 은 `collection`+`all` 파일을, `--target backend` 는
`backend`+`all` 파일을 적용한다. 시드도 동일하게 `-- target:` 으로 필터된다.

## 테이블 → 타깃 매핑 (단일 출처 = `database/db_partition.py`)

- **BACKEND**(→ `backend`): users · user_sessions · social_accounts · subscription_plans ·
  signal_subscriptions · portone_verifications · payments · admin_accounts · admin_sessions ·
  admin_audit_log · terms_agreements · watchlists · signal_journals · user_signal_reads · report_issuances.
- **PUBLISHED**(→ `all`): stocks · final_signals · analysis_results · agent_results · signal_events ·
  source_documents. (백엔드 read-model `api.signals_current`/`api.signal_detail` 이 JOIN 하는 집합.)
- **COLLECTION**(→ `collection`): 그 외 전부(dart_* · datalab_* · hiring_* · patent_* · ohlcv_data ·
  processing_queue · dead_letter · ml_inferences · meta_signals · event_study_panel 등). 명시 열거하지
  않고 `actual − BACKEND − PUBLISHED` 로 도출한다(테이블 추가 시 자동 collection).

## cross-DB FK 는 존재할 수 없다

물리 분리된 두 인스턴스 사이엔 Postgres FK 가 불가능하다. 예: `analysis_requests.user_id`(COLLECTION)
→ `users`(BACKEND), `agent_results`(PUBLISHED) → `xgb_model_versions`(COLLECTION). 재베이스라인
생성기(`rebaseline.py`)는 **각 DB 에 참조 대상이 없는 FK 제약을 제거**한다(컬럼은 남고 제약만 제거).
정합성은 DB FK 가 아니라 **앱레벨 publisher**(수집→백엔드 복사)가 담당한다.

## baseline 구성 (재베이스라인 결과)

| 파일 | target | 내용 | 작성 |
|---|---|---|---|
| `0001_infra_roles.sql` | all | signal_worker/signal_backend 롤 | 정적(커밋) |
| `0002_published_baseline.sql` | all | PUBLISHED 6종 + 전역 객체(타입/함수/확장) | **rebaseline.py 생성** |
| `0003_collection_baseline.sql` | collection | COLLECTION 테이블 | **rebaseline.py 생성** |
| `0004_backend_baseline.sql` | backend | BACKEND 15종 | **rebaseline.py 생성** |
| `0005_api_read_contract.sql` | all | api.signals_current/signal_detail/stocks | 정적 |
| `0005b_api_pipeline_status_collection.sql` | collection | api.analysis_pipeline_status(processing_queue 의존) | 정적 |
| `0006_collection_grants.sql` | collection | signal_worker GRANT | 정적 |
| `0007_backend_grants.sql` | backend | signal_backend GRANT + api SELECT | 정적 |

## 규칙

1. **모든 마이그/시드는 `-- target:` 을 단독 라인으로 명시한다.** 인라인 주석 금지
   (`-- target: all   -- 설명` 은 파서가 인식 못 함). 설명은 다음 줄에 별도 주석.
2. 새 마이그는 `migrate.py new "이름" --target {collection|backend}` 로 만든다(템플릿이 헤더를 넣음).
3. 가드: `python database/tools/check_targets.py` — 미태그/오타 발견 시 exit 1(CI 권장).
4. 표 baseline(0002/0003/0004) 재생성은 `database/rebaseline.py` (스키마 출처 = 정상 DB).
   손으로 테이블 DDL 을 고치지 말 것(드리프트).
5. 부트스트랩/리셋 절차: [docs/runbooks/db-2-instance-bootstrap.md](../../docs/runbooks/db-2-instance-bootstrap.md).
