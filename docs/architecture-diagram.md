# Signal α — 시스템 설계도 (현 구조)

> 현재 GitHub 구조(`origin/main`) 기준 배포 토폴로지와 DB 소유권 경계를 그린 설계도입니다.
> 텍스트 개요는 [architecture.md](./architecture.md), DB 스키마 기준은 `database/migrations/` 입니다.
> 배포는 **db / worker / backend / frontend 4유닛**으로 분리하며, DB 는 **물리적으로 분리된 Postgres
> 인스턴스 2개**(수집 DB / 백엔드 DB)입니다. backend와 worker는 런타임에 직접 호출하지 않고, 워커가
> 산출물을 백엔드 DB 로 **앱레벨 발행(publish)** 하면 backend 는 백엔드 DB 의 `api.*` 읽기 계약으로
> 읽습니다(#531 2-인스턴스 분리. cross-DB FK 불가 → publisher 가 정합성 담당).
> 마이그/시드 타깃 규칙: [database/docs/migration_seed_targets.md](../database/docs/migration_seed_targets.md).

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

    subgraph CDB["수집 DB — Postgres 인스턴스 ①"]
        direction TB
        cwt["COLLECTION + PUBLISHED 테이블<br/>final_signals · stocks · analysis_results ·<br/>processing_queue · agent_results · signal_events ·<br/>dart_* · datalab_* · hiring_* · ml_inferences · ..."]
        cv["api.* view (수집 DB)"]
        cv -. "owner 권한 base 조회" .-> cwt
    end

    subgraph BDB["백엔드 DB — Postgres 인스턴스 ②"]
        direction TB
        bt["BACKEND 15 테이블<br/>users · sessions · subscriptions · payments ·<br/>watchlists · journals · admin_* · report_issuances · ..."]
        bpub["PUBLISHED 발행 사본<br/>final_signals · stocks · analysis_results · ..."]
        bv["api.signals_current · signal_detail"]
        bv -. "owner 권한 base 조회" .-> bpub
    end

    mig["db-migrate<br/>--target collection / backend (owner 롤)"]

    web -- HTTP --> ms
    ms -- "SELECT · signal_backend" --> bv
    ms -- "DML · signal_backend" --> bt
    aw -- "DML · signal_worker" --> cwt
    an -- "DML · signal_worker" --> cwt
    aw -- "publish(PUBLISH_SIGNALS) · BACKEND_DATABASE_URL" --> bpub
    mig -- "DDL + 롤/grant" --> CDB
    mig -- "DDL + 롤/grant" --> BDB

    classDef fe fill:#eef6ff,stroke:#3b82f6;
    classDef be fill:#eefbf0,stroke:#22c55e;
    classDef wk fill:#fff7ed,stroke:#f59e0b;
    classDef db fill:#f5f3ff,stroke:#8b5cf6;
    class web fe;
    class ms be;
    class aw,an wk;
    class cwt,cv,bt,bpub,bv db;
```

> 다이어그램에 **없는 화살표가 곧 경계**입니다: `main-server`는 `agent-worker`를 런타임에 직접
> 호출하지 않고(워커 산출물은 `api.*` view로만 읽음), worker base 테이블(`final_signals` 등)에
> 직접 권한이 없습니다. 따라서 `main-server → agent-worker` 및 `main-server → public(worker 테이블)`
> 간선은 의도적으로 존재하지 않습니다.

**권한 롤 (DB 소유권 경계)**

| 롤 | 권한 | 사용 서비스 (DB) |
|---|---|---|
| `signal_worker` | `public` 전체 DML (수집·분석·시그널 적재) | agent-worker, analyzer (수집 DB) |
| `signal_backend` | `api.*` SELECT + 소유 15 테이블 DML. PUBLISHED base 직접 권한 없음 | main-server (백엔드 DB) |
| owner | DDL · 롤/grant · view 소유 (`--target collection`/`backend` 각각) | db-migrate (양 DB) |

> 롤은 양 인스턴스에 동일하게 생성되며(0001_infra_roles, target all), grant 는 DB 별로 부여됩니다
> (0006 수집 / 0007 백엔드). 마이그/시드 타깃 분류는
> [database/docs/migration_seed_targets.md](../database/docs/migration_seed_targets.md),
> 부트스트랩·리셋 절차는 [db-2-instance-bootstrap 런북](./runbooks/db-2-instance-bootstrap.md) 참고.

> **컷오버 주의**: 롤은 비밀번호 없이 생성됩니다. 운영 배포 시 out-of-band로 비밀번호를 부여하고
> `WORKER_/BACKEND_DATABASE_URL` 을 해당 롤로 교체해야 권한 격리가 발효됩니다(`.env.example` 참고).

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
