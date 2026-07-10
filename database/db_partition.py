"""2-DB 물리 분리 테이블 분류 (#525/#531 WS-A).

수집(워커) DB / 백엔드(서비스) DB 로 물리 분리할 때 각 테이블의 소속을 정한다.

세 버킷:
- **BACKEND**   : 백엔드(서비스) 소유. 회원·세션·구독·결제·관리자·약관·유저 콘텐츠. 백엔드 DB 에만.
- **PUBLISHED** : 워커가 생산하지만 백엔드/프론트가 읽는 발행 산출물(최종/소스 리포트).
                  **양쪽 DB 에 존재** — 수집 DB(워커가 기록) + 백엔드 DB(발행 사본, api.* view 대체).
- COLLECTION    : 그 외 전부(수집 raw + 워커 파이프라인 내부). 수집 DB 에만.

설계 메모:
- COLLECTION 은 **명시 열거하지 않는다**: 부트스트랩이 실제 DB 를 introspect 해
  ``actual_tables - BACKEND - PUBLISHED`` 로 도출 → 테이블 추가 시 분류 드리프트가 없다.
- PUBLISHED 집합은 백엔드 read-model(api.signals_current / api.signal_detail)이 JOIN 하는
  테이블과 일치한다: final_signals(+stocks/analysis_results) + agent_results + signal_events.
- 백엔드 DB 가 보유할 테이블 = BACKEND ∪ PUBLISHED. 이들 간 FK 는 유지되고, 이들에서
  COLLECTION 으로 향하는 cross-DB FK 는 부트스트랩의 DROP ... CASCADE 가 제거한다.
"""

from __future__ import annotations

# 백엔드(서비스) 소유 — 백엔드 DB 에만 존재.
BACKEND_TABLES: frozenset[str] = frozenset(
    {
        # 회원·인증
        "users",
        "user_sessions",
        "social_accounts",
        # 구독·결제
        "subscription_plans",
        "signal_subscriptions",
        "portone_verifications",
        "payments",
        # 관리자·감사
        "admin_accounts",
        "admin_sessions",
        "admin_audit_log",
        # 약관
        "terms_agreements",
        # 유저 콘텐츠 / 발행 이력
        "watchlists",
        "signal_journals",
        # 저널 outcome 확정 결과 — 워커 러너가 BACKEND_DATABASE_URL 로 기록(collection_schedules
        # 와 같은 워커→백엔드 계약). 원본 저널(user_memo)에는 워커 쓰기 권한 없음.
        "signal_journal_outcomes",
        # 저널 차트용 종가 시리즈 — 같은 러너가 저널 있는 종목만 동기화(종목×거래일 1행).
        "signal_journal_chart_prices",
        # 종목별 일봉 종가 시리즈(공개 홈 차트) — 발행 러너가 분석 종목 전체를 동기화
        # (signal_journal_chart_prices 와 같은 워커→백엔드 계약, 저널 유무와 무관).
        "stock_price_daily",
        # 종목 회사 로고(공개 홈/리포트 로고) — 정적 참조 데이터라 backend DB 가 직접 보유한다
        # (수집 DB 복사·발행 크론잡 없음). 적재 툴 load_stock_logos.py 가 백엔드 DB 로 1회성
        # upsert 하고 main-server 가 여기서 직접 SELECT. 테이블 이름은 과거 발행 사본
        # 마이그(20260710_1100)의 이름을 유지한다(check_targets 과거 마이그 locality 재검증과
        # 충돌 않게 backend 에 stock_logos 를 새로 만들지 않는다 — 수집 원본은 별도 마이그로 DROP).
        "stock_logo_published",
        "user_signal_reads",
        "report_issuances",
        # 수집 스케줄 제어 평면 (어드민/MCP 가 쓰고 워커 스케줄러가 폴링). 백엔드 DB 보유.
        "collection_schedules",
        "collection_schedule_runs",
        # 지정학 리스크 Kill-Switch — 관리자 수동 토글(안전 핵심 경로)이 워커 없이
        # 동작해야 하므로 백엔드 소유. 워커 guard 데몬은 BACKEND_DATABASE_URL 로
        # 이력·제안을 기록(journal_outcomes 와 같은 워커→백엔드 계약).
        "guard_site_status",
        "guard_news_events",
        "guard_recommendations",
        "guard_status_audit",
        # 커뮤니티 게시판 — 유저 콘텐츠(공개 공유 레이어). 저자·저널 FK 로 users/
        # signal_journals 와 같은 백엔드 DB 에 공존. 랭킹 스냅샷은 워커가
        # BACKEND_DATABASE_URL 로 기록(journal_outcomes 와 같은 워커→백엔드 계약).
        "community_posts",
        "community_comments",
        "community_reactions",
        "community_post_views",
        "community_reports",
        "community_post_rankings",
        # 종목별 뉴스(토스식 뉴스 목록/건수) — 워커 뉴스 데몬이 BACKEND_DATABASE_URL 로
        # 적재하고 main-server 가 api.stock_news 로 읽는다(guard_news_events 와 같은
        # 워커→백엔드 계약). display-only 라 시그널/점수 파이프라인과 무관.
        "stock_news",
        # 종목별 뉴스 다이제스트(LLM 종합 한 줄) — 워커 뉴스 데몬이 stock_news 후보를
        # LLM 으로 요약해 BACKEND_DATABASE_URL 로 UPSERT, main-server 가
        # api.stock_news_digest 로 읽는다. stock_news 와 같은 display-only 계약.
        "stock_news_digest",
        # 매매 부검 — 유저 매매 기록(수기 입력, 공통 정규화). backend 가 INSERT/DELETE
        # (수기 입력·삭제) + 부검 조회 SELECT. 워커는 overlay 산출용 SELECT 만.
        "user_trade_fills",
        # 매매 부검 — 유저 매매 계획(선택 입력, Plan vs Actual 기준선). backend CRUD.
        "user_trade_plans",
        # 매매 부검 — PIT 관측신호 오버레이(내부자 공시 등). 워커가 수집 DB에서 읽어
        # 멱등 적재(이중풀), backend 는 부검 조회 SELECT. signal_date=known_at(PIT).
        "user_trade_signal_overlays",
    }
)

# 워커 생산 + 백엔드/프론트 소비 — 양쪽 DB 에 존재(백엔드는 발행 사본).
# 백엔드 read-model(api.signals_current / api.signal_detail)이 JOIN 하는 테이블과 일치.
PUBLISHED_TABLES: frozenset[str] = frozenset(
    {
        "stocks",
        "final_signals",
        "analysis_results",
        "agent_results",
        "signal_events",
        # 이벤트 출처 메타(source_name/url/is_official) — signal_events 가 FK 참조하고
        # api.signal_detail 이 JOIN 한다. 빠지면 상세 뷰가 self-contained 하지 않다.
        "source_documents",
    }
)

# 부트스트랩/마이그레이션 원장 — 각 DB 가 자체 보유(분류 대상 아님).
LEDGER_TABLE = "schema_migrations"

# 드롭됐지만 그 테이블을 CREATE/ALTER 하던 과거 타임스탬프 마이그가 히스토리에 남아 있는
# 테이블. check_targets 는 **모든** 활성 마이그(과거 포함)의 target↔테이블 locality 를 현재
# 분류로 정적 재검증하므로, 원 소유 DB 분류를 알아야 그 과거 마이그가 통과한다.
# 이 집합은 라이브 스키마엔 없으므로 backend_keep()/validate()(=존재해야 하는 집합)에는
# 넣지 않는다 — ownership map 을 정직하게 유지하고, check_targets.bucket_of 만 참조한다.
#   user_broker_credentials: 증권사 API 연동 삭제(수기 입력 전환)로 20260709_1000 이 DROP.
#     과거 20260707_1600(CREATE)·_1700(ALTER, target: backend)이 backend 분류를 요구.
RETIRED_BACKEND_TABLES: frozenset[str] = frozenset(
    {
        "user_broker_credentials",
    }
)


def backend_keep() -> frozenset[str]:
    """백엔드 DB 가 보유할 테이블 = BACKEND ∪ PUBLISHED."""
    return BACKEND_TABLES | PUBLISHED_TABLES


def collection_only(all_tables: frozenset[str] | set[str]) -> set[str]:
    """실제 테이블 집합에서 백엔드 보유분·원장을 뺀 수집 전용 테이블.

    부트스트랩이 백엔드 DB 에서 DROP 할 대상이다(수집 DB 에만 남김).
    """
    return set(all_tables) - backend_keep() - {LEDGER_TABLE}


def validate(all_tables: frozenset[str] | set[str]) -> list[str]:
    """분류 정합성 점검. 문제 메시지 목록 반환(빈 리스트=정상).

    - BACKEND ∩ PUBLISHED 는 공집합이어야 한다.
    - 선언된 BACKEND/PUBLISHED 가 실제 테이블에 존재해야 한다(오타·삭제 감지).
    """
    issues: list[str] = []
    overlap = BACKEND_TABLES & PUBLISHED_TABLES
    if overlap:
        issues.append(f"BACKEND/PUBLISHED 중복 분류: {sorted(overlap)}")
    actual = set(all_tables)
    missing = (BACKEND_TABLES | PUBLISHED_TABLES) - actual
    if missing:
        issues.append(f"분류됐지만 실제 DB 에 없는 테이블: {sorted(missing)}")
    return issues
