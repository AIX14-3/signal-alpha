# 저널 커뮤니티 게시판 상세기획 (Spec)

> 입력: `journal-board-design.md` · 기획 하네스 생성 · 작성일: 2026-07-07 · 상태: 상세기획(확정)
> Source of Truth: `HARNESS.md` · 다이어그램: `diagrams/journal-board-*.mermaid`

## 1. 개요 / 목표

지금 저널은 **owner 전용 비공개 + 구독 전용(402)**. 여기에 **공개 커뮤니티 레이어**를 얹어, 유저가 자기
투자 판단 기록을 게시글로 **공유**하고(opt-in), 서로 **댓글**로 의견을 나누고, **반응(좋아요·북마크)** 하고,
**인기/주간 인기** 랭킹으로 좋은 판단을 발견한다. 스탠스 = glass-box·track-record(예측 판매 아님).

**확정 결정(가드레일 2라운드):**
| 항목 | 결정 |
|---|---|
| 공유 단위 | **라이브 참조** — 게시글이 `signal_journals` 항목을 참조, 원본 변경 반영 |
| 저자·공개 범위 | **닉네임**(없으면 member_code) + **종목·판단·메모 공개, 손익/수익률 기본 비공개** |
| 손익 공개 | **기본 비공개 + 작성자 토글**(`show_pnl`) → 켜면 **수익률%만** 노출, 절대금액·가격·PII는 항상 제외 |
| 게이팅 | **읽기 전체 공개(비로그인 포함) · 쓰기(게시/댓글/반응/신고) 로그인 필수** |
| 인기 산식 | **가중합**(좋아요·댓글·조회) + **워커 배치(시간당)**, 주간 = 롤링 7일. 가중치 수치는 튜닝값 |
| 조회수 | **중복 방지** — 유저/세션당 1회 |
| 댓글 | **1단계 대댓글**(`parent_comment_id` 1레벨) |
| 모더레이션 | **신고 기반 자동숨김** — 신고 임계 도달 시 hidden + 관리자 검토 + 작성자 본인 삭제 |

## 2. 재사용 자산 & 제약 (plan-search)

| 개념 | 상태 | 경로 | 요지 |
|---|---|---|---|
| 커뮤니티 인프라 | **전무→신규** | — | board/post/comment/reaction/feed/ranking 없음 |
| 저널(비공개·구독전용) | 참조 | `services/main-server/app/api/routes/journals.py` | `user_id` 스코프, visibility 컬럼 없음, 모든 EP 402 |
| 저널 테이블 | 참조(라이브) | `signal_journals` (`database/migrations/0004_backend_baseline.sql:245`) | stock_id·user_view·user_memo·outcome_* — 공유는 **화이트리스트만** |
| 유저 식별 | 재사용 | `public.users` (`0004…:531`) | member_code(비-PII)·nickname(≤50). 아바타 없음. email/phone 민감 |
| 구독 게이트 | 재사용 훅 | `_subscription_active()` (`app/api/routes/auth.py:490`) | import해서 게이팅. 데코 없음, 명령형 |
| 인증 | 재사용 | `get_current_user` | Bearer. 쓰기 EP에 Depends |
| 웹 리스트 패턴 | 참고 | `web/src/stores/journalStore.ts`, `app/mypage/page.tsx`, `components/JournalChart.tsx`, `AppShell.tsx` | items[]+load, 단순 limit |
| 주간 랭킹 배치 | 패턴 재사용 | `services/agent-worker/run_journal_outcomes.py` | 워커 러너 모델(탐색 확인) |
| 페이지네이션 | **신규** | — | 커서 프리미티브 부재(전부 limit) |

## 3. 요구사항 (plan-split)

### 3.1 기능 요구사항 (FR)
| ID | 설명 | 근거 | 수용기준 |
|---|---|---|---|
| FR-1 | 유저가 **소유한** 저널 항목을 게시글로 공유(라이브 참조), `show_pnl` 토글 | design §1①, journals.py 소유검증 | 비소유 journal_id → 403. 생성 시 title·body·journal_id·show_pnl |
| FR-2 | 게시글 CRUD + soft delete | design §1② | 작성자만 수정/삭제. 삭제=`deleted_at`, 피드서 제외 |
| FR-3 | 댓글 / **1단계 대댓글** | design §1② | 로그인 필요. `parent_comment_id`는 1레벨만(대댓글에 대댓글 불가). 삭제된 게시글엔 댓글 불가 |
| FR-4 | 반응(좋아요·북마크) 토글 | design §1③ | 유저·타깃·타입당 1회(unique). 재요청=토글 |
| FR-5 | 인기 + 주간 인기 랭킹 | design §1④ | `/popular?window=weekly\|all` 상위 N. 워커 갱신 |
| FR-6 | 피드 정렬: 최신 / 인기 / 주간 인기 | design §1④ | 커서 페이지네이션, 비로그인 열람 가능 |
| FR-7 | 게시글/댓글 **신고** → 임계 자동숨김 | 가드레일(모더레이션) | 로그인 필요. 신고 누적 ≥ 임계 → `hidden`, 관리자 검토 큐. 중복 신고 방지 |

### 3.2 비기능 요구사항 (NFR)
| ID | 설명 | 근거 | 수용기준 |
|---|---|---|---|
| NFR-1 | **프라이버시 화이트리스트** — 공개 응답은 stock·user_view·user_memo·작성시각. price/절대손익/PII **항상 제외**. `show_pnl=true`인 게시글만 **수익률%(outcome_change_pct)** 추가 노출 | HARNESS 절대규칙, 가드레일 | 피드/상세 API `SELECT *` 금지, 명시 투영. show_pnl=false → 수익률 필드 부재. 가격/절대금액 항상 부재 |
| NFR-2 | 게이팅 — 읽기 공개(비로그인), 쓰기 로그인 | 가드레일 | GET 무인증 200, 쓰기 EP 무인증 401 |
| NFR-3 | 커서 페이지네이션(신규) | plan-search 부재 | `?cursor=`+`next_cursor`, 안정 정렬((score,id)/(created_at,id)) |
| NFR-4 | 어뷰징 방지 — 자가 반응 금지, 반응 unique, **조회수 유저/세션당 1회** | plan-logic-check, 가드레일 | 본인 게시글 좋아요 무효. 같은 뷰어 재조회 시 view 미증가 |
| NFR-5 | 라이브 참조 정합 — 원본 삭제/비공개 시 graceful | 가드레일(라이브 선택) | 원본 없음 → "원본 없음" 표기, 500 금지 |
| NFR-6 | **모더레이션** — 신고 기반 자동숨김 + 관리자 검토 + 작성자 삭제 | 가드레일(안전) | 신고 임계 도달 → hidden 전이, 피드 제외. 관리자 복원/영구삭제 가능 |

### 3.3 데이터 모델 초안 (신규 마이그레이션, 타임스탬프 파일명·LF·불변)

- `20260707_HHMM_community_posts.sql` — **community_posts**
  `id bigint PK`, `author_user_id bigint FK users(id)`, `journal_id bigint FK signal_journals(id) NULL`(라이브 참조),
  `title varchar(200)`, `body text`, `show_pnl boolean NOT NULL DEFAULT false`,
  `view_count int NOT NULL DEFAULT 0`, `status varchar(10) NOT NULL DEFAULT 'visible' CHECK IN ('visible','hidden')`,
  `created_at/updated_at timestamptz`, `deleted_at timestamptz NULL`. idx(created_at,id), idx(status).
- `20260707_HHMM_community_comments.sql` — **community_comments**
  `id`, `post_id FK community_posts`, `parent_comment_id bigint NULL self-FK`(1레벨 강제는 앱단),
  `author_user_id FK users`, `body text`, `status`('visible'|'hidden'), `created_at/updated_at`, `deleted_at NULL`. idx(post_id,created_at).
- `20260707_HHMM_community_reactions.sql` — **community_reactions**
  `id`, `user_id FK users`, `target_type varchar(10) CHECK IN ('post','comment')`, `target_id bigint`,
  `type varchar(10) CHECK IN ('like','bookmark')`, `created_at`, **UNIQUE(user_id, target_type, target_id, type)**.
- `20260707_HHMM_community_post_views.sql` — **community_post_views**(조회 중복방지)
  `post_id FK`, `viewer_key varchar(64)`(user_id or 세션 해시), `viewed_on date`, **UNIQUE(post_id, viewer_key, viewed_on)**.
  삽입 성공 시에만 posts.view_count 증가.
- `20260707_HHMM_community_reports.sql` — **community_reports**(신고)
  `id`, `reporter_user_id FK users`, `target_type`('post'|'comment'), `target_id`, `reason varchar(30)`,
  `created_at`, **UNIQUE(reporter_user_id, target_type, target_id)**(중복신고 방지). 집계 ≥ 임계 → 대상 status='hidden'.
- `20260707_HHMM_community_post_rankings.sql` — **community_post_rankings**
  `post_id FK`, `window_kind varchar(10) CHECK IN ('weekly','all')`(window=PG 예약어라 window_kind), `score numeric(12,3)`,
  `likes int`, `comments int`, `views int`, `computed_at timestamptz`, **PK(post_id, window_kind)**.

> 구현 메모: 위 6개 테이블은 FK 의존순서·원자성을 위해 단일 마이그레이션 `20260707_1500_community_board.sql`(target: backend)로 통합 적용. 격리 postgres에서 적용·검증 완료.

> 저자 핸들·저널 필드는 스냅샷하지 않고 `users`/`signal_journals` 라이브 조인(공유 단위 라이브 참조와 일관).

### 3.4 신규 코드 (경로 제안)
- 백엔드: `services/main-server/app/api/routes/community.py`(app/main.py 등록). EP:
  `POST/GET/PATCH/DELETE /api/community/posts`·`.../{id}`, `.../{id}/comments`, `.../{id}/reactions`,
  `POST .../{id}/report`·`.../comments/{cid}/report`, `GET /api/community/popular?window=`.
  게이팅 = `get_current_user`(쓰기·신고) + 읽기 무인증. 관리자 모더레이션은 기존 `admin.py`/`admin_auth.py` 패턴 재사용.
- 워커: `services/agent-worker/run_community_rankings.py`(+ `app/publish/community_rankings.py`),
  run_journal_outcomes 패턴. 크론(시간당) → community_post_rankings UPSERT(가중합, 롤링 7일).
- 웹: `web/src/stores/communityStore.ts`(**커서 페이지네이션 신규**), `app/community/page.tsx`(피드),
  `app/community/[postId]/page.tsx`(상세), `components/PostCard.tsx`·`CommentList.tsx`·`ReactionButton.tsx`·`ReportButton.tsx`,
  `AppShell` 재사용. 저널 공유 진입 = `app/mypage/page.tsx`에 "공유"(+show_pnl 토글) 액션.

## 4. 시퀀스 (plan-sequence)
`diagrams/journal-board-sequence.mermaid` — 공유(소유검증)·피드(화이트리스트 조인)·반응 토글·주간 배치.

## 5. 유저 플로우 (plan-flow)
`diagrams/journal-board-userflow.mermaid` — 피드→상세(비로그인 열람)→반응/댓글(로그인 유도)→공유(구독자).

## 6. 예외 · 테스트 케이스 (plan-logic-check)
| 요구사항 | 케이스 | 유형 | 기대 결과 |
|---|---|---|---|
| FR-1 | 남의 journal_id로 공유 | 보안 | 403 NOT_OWNER |
| FR-1/NFR-2 | 무인증 게시글 작성 | 보안 | 401 |
| NFR-1 | 피드 응답에 가격/절대손익 포함? | 프라이버시 | 항상 부재 |
| NFR-1 | show_pnl=false 게시글에 수익률% 포함? | 프라이버시 | 부재. show_pnl=true만 수익률% 노출 |
| NFR-1 | 비구독 뷰어의 피드 열람 | 프라이버시 | 화이트리스트 필드만 200(구독 불요) |
| NFR-5 | 참조 원본 저널 삭제 후 상세 | 예외 | "원본 없음" 표기, 500 아님 |
| FR-4/NFR-4 | 본인 게시글 좋아요 | 어뷰징 | 차단/무효 |
| FR-4 | 같은 반응 두 번 | 엣지 | unique 위반 없이 토글 |
| NFR-4 | 같은 뷰어 새로고침 반복 | 어뷰징 | view_count 미증가(당일 1회) |
| FR-3 | 대댓글에 또 대댓글 | 엣지 | 거부(1레벨 초과) |
| FR-3 | 삭제된 게시글에 댓글 | 예외 | 거부(404/409) |
| FR-7 | 같은 유저 중복 신고 | 엣지 | unique로 1회만 집계 |
| FR-7 | 신고 임계 도달 | 정상 | 대상 status='hidden', 피드 제외, 관리자 큐 |
| FR-5 | 반응 0·소표본 주간 랭킹 | 경계 | 빈/부분 목록 정상 |
| FR-5 | 롤링 7일 경계(직전/직후) | 경계 | 포함/제외 일관 |
| FR-6/NFR-3 | 커서 중간 삽입/삭제 | 엣지 | 중복·누락 없는 안정 정렬 |

## 7. 튜닝 파라미터 (구현 시 조정, 구조 아님)
- 인기 가중치 `w_like / w_comment / w_view`(초기 예: 3 / 5 / 1) 및 배치 주기(초기: 시간당).
- 신고 자동숨김 임계(초기 예: 서로 다른 유저 5건).
- 조회 중복방지 윈도우(초기: 당일 기준 `viewed_on`).
> 값은 운영 데이터로 튜닝. 구조·스키마는 위에서 확정.

## 8. 검증 노트 (plan-assemble)
- **역추적** — FR-1~7·NFR-1~6 전부 `journal-board-design.md`(§1 기능, §3 프라이버시, §5 열린질문) + 가드레일 결정으로 추적됨. 과설계 없음.
- **자산 실재** — journals.py·signal_journals(0004:245)·users(0004:531)·`_subscription_active`(auth.py:490)·journalStore·run_journal_outcomes 경로 **Grep/Glob 확인**. 신규는 §3.3/§3.4에 "신규" 명시.
- **일관성** — 다이어그램 노드(공유·피드·반응·배치·원본없음)가 FR/NFR과 일치. (신고/숨김 흐름은 관리자 모더레이션 시퀀스로 구현 시 보강 여지)
- **도메인 금지** — 프라이버시 위반 0: 피드/상세 화이트리스트, 가격/절대손익/PII 제외, 수익률%는 작성자 opt-in만(NFR-1). 예측률 과장 문구 없음.
- **미결정 보존** — 이전 5개 미결정 중 구조적 4건 확정, 나머지는 §7 튜닝 파라미터로 강등(임의 확정 아님, 값만 미정).
