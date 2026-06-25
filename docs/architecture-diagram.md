# Signal α — 시스템 설계도 (현 구조)

> 현재 GitHub 구조(`origin/main`) 기준 배포 토폴로지와 DB 소유권 경계를 그린 설계도입니다.
> 텍스트 개요는 [architecture.md](./architecture.md), DB 스키마 기준은 `database/migrations/` 입니다.
> 배포는 **db / worker / backend / frontend 4유닛**으로 분리하며, backend와 worker는 런타임에
> 직접 호출하지 않고 **DB의 `api.*` 읽기 계약**으로만 연결됩니다(PR #474, Model A: 단일 Postgres +
> 소유권 경계).

## 1. 배포 토폴로지 & DB 소유권 경계

```mermaid
flowchart LR
    subgraph FE["프론트엔드 유닛"]
        web["web<br/>Next.js 대시보드"]
    end

    subgraph BE["백엔드 유닛"]
        ms["main-server<br/>FastAPI 사용자 API · 13 라우트<br/>(signals · watchlists · journals · dashboard ·<br/>reports · auth · admin · analytics · payments ·<br/>subscriptions · health)"]
    end

    subgraph WK["워커 유닛"]
        aw["agent-worker<br/>수집·정규화·분석·큐·LLM · 8 라우트<br/>(tasks · queue · dart · price ·<br/>schedules · dead_letter · observability · health)"]
        an["analyzer<br/>분석 루프 데몬"]
    end

    subgraph DBU["DB 유닛 — PostgreSQL (단일 인스턴스)"]
        direction TB
        subgraph PUB["public 스키마"]
            wt["worker 적재 테이블<br/>final_signals · stocks · analysis_results ·<br/>processing_queue · agent_results · signal_events · ..."]
            bt["backend 소유 13 테이블<br/>users · user_sessions · social_accounts ·<br/>watchlists · signal_journals · user_signal_reads ·<br/>signal_subscriptions · report_issuances · admin_* · ..."]
        end
        subgraph API["api 스키마 — 읽기 계약 (읽기전용 view)"]
            v["signals_current · signal_detail ·<br/>stocks · analysis_pipeline_status"]
        end
        v -. "view = owner 권한으로 base 조회" .-> wt
    end

    mig["db-migrate<br/>일회성 잡 (owner 롤)"]

    web -- HTTP --> ms
    ms -- "SELECT · signal_backend" --> v
    ms -- "DML · signal_backend" --> bt
    aw -- "DML · signal_worker" --> wt
    an -- "DML · signal_worker" --> wt
    mig -- "DDL + 롤/grant · owner" --> DBU

    classDef fe fill:#eef6ff,stroke:#3b82f6;
    classDef be fill:#eefbf0,stroke:#22c55e;
    classDef wk fill:#fff7ed,stroke:#f59e0b;
    classDef db fill:#f5f3ff,stroke:#8b5cf6;
    class web fe;
    class ms be;
    class aw,an wk;
    class wt,bt,v db;
```

> 다이어그램에 **없는 화살표가 곧 경계**입니다: `main-server`는 `agent-worker`를 런타임에 직접
> 호출하지 않고(워커 산출물은 `api.*` view로만 읽음), worker base 테이블(`final_signals` 등)에
> 직접 권한이 없습니다. 따라서 `main-server → agent-worker` 및 `main-server → public(worker 테이블)`
> 간선은 의도적으로 존재하지 않습니다.

**권한 롤 (DB 소유권 경계)**

| 롤 | 권한 | 사용 서비스 |
|---|---|---|
| `signal_worker` | `public` 전체 DML (수집·분석·시그널 적재) | agent-worker, analyzer |
| `signal_backend` | `api.*` SELECT + 소유 13 테이블 DML. worker base 테이블 직접 권한 없음 | main-server |
| owner (`signal_alpha`) | DDL · 롤/grant · view 소유 | db-migrate |

> **컷오버 주의**: 롤은 비밀번호 없이 생성됩니다. `WORKER_/BACKEND_DATABASE_URL` 미설정 시 compose가
> owner로 폴백하므로, 운영 배포 시 out-of-band로 비밀번호를 부여하고 두 URL을 해당 롤로 교체해야
> 권한 격리가 실제로 발효됩니다(`.env.example` 참고).

## 2. 레포 → 배포 유닛 매핑

```mermaid
flowchart TB
    subgraph repo["signal-alpha 모노레포"]
        webdir["web/ · Next.js"]
        msdir["services/main-server/"]
        awdir["services/agent-worker/"]
        dbdir["database/ · migrations + Dockerfile"]
        dadir["packages/data-access/<br/>backend facade + worker repos + api 읽기 계약"]
        scdir["packages/signal-core/"]
        mddir["packages/market-data/"]
        vmdir["packages/vol-models/"]
    end

    FEU["프론트엔드 유닛"]
    BEU["백엔드 유닛"]
    WKU["워커 유닛"]
    DBU2["DB 유닛 + db-migrate 잡"]

    webdir --> FEU
    msdir --> BEU
    awdir --> WKU
    dbdir --> DBU2
    dadir -. 공유 .-> BEU
    dadir -. 공유 .-> WKU
    scdir -. 공유 .-> BEU
    scdir -. 공유 .-> WKU
    mddir -. 공유 .-> WKU
    vmdir -. 공유 .-> WKU
```

- `packages/data-access`는 양쪽 유닛이 공유하되, **CI 가드**가 main-server(백엔드)의
  `signal_alpha_data_access.worker` / `.repositories` 직접 import를 차단합니다. 백엔드는
  `signal_alpha_data_access.backend` 읽기 계약 facade만 사용합니다.
- `signal-core`는 서비스 간 공통 데이터 계약·안전 규칙, `market-data`/`vol-models`는 워커 분석에 사용됩니다.

## 3. 멀티에이전트 분석 흐름 (요약)

```mermaid
flowchart TB
    in["입력: stock_name / stock_code<br/>(종목명 → 종목코드 / DART corp_code 표준화)"]
    subgraph fan["Fan-out 병렬 분석 (agent-worker)"]
        dart["DART Watcher"]
        report["Report 분석"]
        alt["Alternative Signal<br/>(datalab · hiring · patent · price)"]
    end
    agg["Debate Aggregation<br/>긍정/주의 근거 분리 → 소스 방향성 일치도"]
    fs["final_signals<br/>source alignment + evidence + needs_review"]
    api2["api.signals_current / signal_detail<br/>(읽기 계약 view)"]
    web2["web 대시보드"]

    in --> fan --> agg --> fs --> api2 --> web2
```

> 소스별 수집기/분석기는 `agent-worker/app/collectors/*` · `analyzers/*` 아래 소스 단위
> (`dart · report · price · datalab · hiring · patent`)로 구성됩니다. 단계별 큐·테이블 흐름은
> [data-pipeline.md](./data-pipeline.md), 집계 계약은 `spec/final-signal-aggregator-spec.md` 참고.
