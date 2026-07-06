# Cloud SQL 데이터 적재 런북 (로컬/Supabase → GCP Cloud SQL)

> 대상: 팀원이 **자기 로컬 Postgres**(또는 Supabase 과거 코퍼스)에 쌓아둔 대체데이터를
> GCP **Cloud SQL** 로 옮길 때. 마이그레이션(스키마+시드)은 이미 끝난 DB에 **실데이터를 채우는** 절차다.
>
> ⚠️ **가장 흔한 함정**: 행만 복사하고 **시퀀스를 안 맞추면**, 앱이 다음 INSERT 할 때
> `duplicate key value violates unique constraint "..._pkey"` 로 죽는다. §4 를 반드시 실행할 것.

## 0. 먼저 이해할 것 — DB가 2개다

| DB | Cloud SQL 인스턴스 | 담는 것 |
|---|---|---|
| **collection** | `sa-pg` | 워커/원천 데이터: `patents`, `datalab_*`, `hiring_*`, `source_documents`, `collector_runs`, `agent_results` 등 |
| **backend** | `sa-be` | 유저·결제·`api.*` 발행본 |

**팀원의 로컬 대체데이터(특허·DataLab·채용)는 전부 `collection` DB(`sa-pg`)로 간다.**
backend 는 앱이 발행 시 자동 복사하므로 수동 적재 대상이 아니다.

- 접속 DSN·비밀번호: `docs/gcp-deploy-resume-and-domain.md` §11 참조(Cloud SQL Private IP).
- 스키마/시드는 `database/migrate.py apply --seeds --target collection` 이 이미 만들어 둔 상태여야 함(빈 테이블).

---

## 1. 방법 A — pg_dump 로 통째로 (권장, 시퀀스 자동 포함)

`pg_dump` 는 **시퀀스 setval 까지 덤프에 포함**하므로 §4 를 안 해도 되는 게 장점.
테이블을 골라 데이터만 옮긴다(스키마는 이미 있으므로 `--data-only`):

```bash
# 1) 로컬(원본)에서 대체데이터 테이블만 데이터 덤프
pg_dump "$LOCAL_DSN" --data-only --no-owner --no-acl \
  -t public.patents -t public.datalab_categories -t public.datalab_search_trends \
  -t public.source_documents -t public.collector_runs \
  -f altdata_dump.sql
#   (대상 테이블은 실제 사용 테이블로 조정. 전체 원천을 옮기려면 -t 를 빼고 스키마 지정)

# 2) Cloud SQL(collection = sa-pg)로 적재
psql "$COLL_URL" -v ON_ERROR_STOP=1 -f altdata_dump.sql
```

> ⚠️ `--data-only` 라도 대상 테이블에 **이미 행이 있으면 PK 충돌**한다. 빈 DB(마이그 직후)에 넣거나,
> 재적재면 먼저 `TRUNCATE ... RESTART IDENTITY CASCADE` 로 비우고 넣을 것.
> FK 순서 문제가 나면 적재 동안만 `SET session_replication_role = replica;` (세션 한정) 로 트리거/FK 검증을 끈다.

## 2. 방법 B — 테이블 단위 CSV (\copy)

큰 테이블 몇 개만, 혹은 pg_dump 접근이 애매할 때:

```bash
# 내보내기(로컬)
psql "$LOCAL_DSN" -c "\copy (SELECT * FROM public.patents) TO 'patents.csv' CSV HEADER"
# 들여오기(Cloud SQL)
psql "$COLL_URL" -c "\copy public.patents FROM 'patents.csv' CSV HEADER"
```

> CSV 방식은 **시퀀스를 절대 안 옮긴다** → §4 를 **반드시** 실행해야 함.

## 3. Supabase 과거 코퍼스(특허 155k·DataLab 281k)도 동일

원본이 Supabase면 `$LOCAL_DSN` 자리에 Supabase 연결 문자열을 넣고 §1(pg_dump) 그대로.
용량은 Cloud SQL 10GB 로 여유(과거 Neon 512MB 벽과 다름). 목데이터(`source_name='KIPRIS'`,
`application_no LIKE 'MOCK%'`, 21건)는 `-t` 대신 `\copy (SELECT ... WHERE NOT ...)` 로 제외하고 넣을 것.

---

## 4. ★ 적재 후 필수 — 시퀀스 재싱크 (setval)

CSV/수동 INSERT 로 넣었거나 조금이라도 의심되면 **무조건** 실행.
아래를 `collection`(sa-pg)에서 돌리면 **모든 serial 컬럼의 setval 문을 생성**해 준다:

```sql
-- 1) setval 문 생성 (출력을 복사)
SELECT format(
  'SELECT setval(%L, COALESCE((SELECT MAX(%I) FROM %I.%I), 1), true);',
  pg_get_serial_sequence(quote_ident(n.nspname)||'.'||quote_ident(c.relname), a.attname),
  a.attname, n.nspname, c.relname)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE c.relkind = 'r'
  AND n.nspname NOT IN ('pg_catalog','information_schema')
  AND pg_get_serial_sequence(quote_ident(n.nspname)||'.'||quote_ident(c.relname), a.attname) IS NOT NULL;
-- 2) 위 출력(SELECT setval(...) 여러 줄)을 그대로 다시 실행
```

이러면 각 시퀀스가 `max(id)` 로 밀려, 앱의 다음 INSERT 가 충돌하지 않는다.

### 지금 당장 필요한 즉시 복구 (collector_runs 중복키 해소)
현재 Cloud SQL 의 `collector_runs` 시퀀스가 드리프트해 수집이 실패 중이다. 아래 한 줄이면 해소:

```sql
SELECT setval('public.collector_runs_id_seq',
              (SELECT COALESCE(MAX(id), 1) FROM public.collector_runs), true);
```

---

## 5. 검증

```sql
-- 행이 들어갔는지
SELECT count(*) FROM public.patents;
SELECT count(*) FROM public.source_documents;
-- 시퀀스가 max 이상인지 (다음 값이 max 보다 커야 정상)
SELECT last_value FROM public.collector_runs_id_seq;
SELECT max(id) FROM public.collector_runs;   -- last_value >= 이 값 이어야 함
```

적재 + 시퀀스 재싱크 후, 수집 Job(`altdata-collect`)을 재실행하면 중복키 없이 돌아야 한다.
최종 왕복 확인은 발행 스모크: 백엔드 DSN 에서 `SELECT count(*) FROM api.signals_current;` ≥ 1.

## 6. 롤/권한 참고
런타임 롤(`signal_worker`)은 마이그(`0006_collection_grants.sql`)로 이미 grant 돼 있다.
적재는 보통 `postgres`(소유자)로 하므로 추가 grant 불필요. 새 테이블을 만든 게 아니라
**기존 테이블에 데이터만 넣는** 것이면 권한 이슈 없음.
