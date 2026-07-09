"""
Supabase -> Neon(collection) 대체데이터 이관 : 특허(순증분) + DataLab(전량).

설계 원칙
---------
- stock_id 재매핑: Supabase stock_id -> ticker -> Neon stock_id (id 직접복사 금지).
- id 충돌 회피: raw_documents / datalab_raw_documents 는 Neon 시퀀스가 새 id 발번.
  자식행은 부모의 external_id 로 다시 조인해 새 id 를 찾는다(= 절대 id 하드코딩 안 함).
- datalab_categories 만 id 보존(1~13 이미 양 DB 동일, 14~729 추가) -> 시퀀스 setval 로 정합.
- 중복/재실행 안전: 모든 INSERT 는 ON CONFLICT DO NOTHING (재실행 idempotent).
- 목데이터(KIPRIS) 는 이관 대상에서 제외하고, 별도로 Neon+Supabase 양쪽에서 삭제.
- 기본 dry-run(읽기전용). 실제 쓰기는 --execute 필요.

실행
----
  python scripts/migrate_altdata_to_neon.py            # dry-run (읽기전용, 계획만)
  python scripts/migrate_altdata_to_neon.py --execute  # 실제 이관 + 목데이터 삭제
"""
import argparse
import asyncio
import sys

import asyncpg

ENV = r"C:\Users\804\Documents\GitHub\signal-alpha\.env"


def load_url(name: str) -> str:
    for line in open(ENV, encoding="utf-8"):
        line = line.strip()
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    raise KeyError(name)


def neon_url() -> str:
    # asyncpg 는 channel_binding 파라미터를 이해 못 함 -> 제거
    return load_url("NEON_COLLECTION_URL").replace("&channel_binding=require", "")


def log(msg: str) -> None:
    print(msg, flush=True)


async def main(execute: bool) -> None:
    supa = await asyncpg.connect(load_url("DATABASE_URL"))
    neon = await asyncpg.connect(neon_url(), timeout=60)
    mode = "EXECUTE (실제 쓰기)" if execute else "DRY-RUN (읽기전용)"
    log(f"===== 대체데이터 Supabase -> Neon 이관 [{mode}] =====\n")

    # ---------------------------------------------------------------
    # 0. 종목 매핑 준비
    # ---------------------------------------------------------------
    supa_stock_ticker = {r["id"]: r["ticker"] for r in await supa.fetch("SELECT id, ticker FROM stocks")}
    neon_ticker_id = {r["ticker"]: r["id"] for r in await neon.fetch("SELECT id, ticker FROM stocks")}
    log(f"[0] Supabase stocks={len(supa_stock_ticker)}  Neon stocks={len(neon_ticker_id)}")

    # 이관에 필요한 ticker 집합 (특허 실데이터 + DataLab category_stocks)
    pat_tickers = {supa_stock_ticker[r["stock_id"]] for r in await supa.fetch(
        """SELECT DISTINCT p.stock_id FROM patent_raw_details p
           JOIN raw_documents r ON r.id = p.raw_document_id
           WHERE r.source_name <> 'KIPRIS'""")}
    dl_tickers = {supa_stock_ticker[r["stock_id"]] for r in await supa.fetch(
        "SELECT DISTINCT stock_id FROM datalab_category_stocks")}
    needed = pat_tickers | dl_tickers
    missing = sorted(t for t in needed if t not in neon_ticker_id)
    log(f"    필요 ticker={len(needed)} (특허 {len(pat_tickers)} + DataLab {len(dl_tickers)}) "
        f"-> Neon 부족 {len(missing)}개")

    # ---------------------------------------------------------------
    # 1. Neon 에 없는 종목 삽입
    # ---------------------------------------------------------------
    if missing:
        rows = await supa.fetch(
            """SELECT ticker, name, market, sector, is_active, is_target, short_name
               FROM stocks WHERE ticker = ANY($1::text[])""", missing)
        log(f"[1] 종목 삽입 대상 {len(rows)}개: {missing[:8]}{' ...' if len(missing) > 8 else ''}")
        if execute:
            await neon.executemany(
                """INSERT INTO stocks (ticker, name, market, sector, is_active, is_target, short_name)
                   VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (ticker) DO NOTHING""",
                [(r["ticker"], r["name"], r["market"], r["sector"], r["is_active"],
                  r["is_target"], r["short_name"]) for r in rows])
            neon_ticker_id = {r["ticker"]: r["id"] for r in await neon.fetch("SELECT id, ticker FROM stocks")}
            log(f"    삽입 완료. Neon stocks={len(neon_ticker_id)}")
        else:
            # dry-run: 종목을 실제로 넣지 않으므로, 이후 재매핑 카운트가 맞도록 임시 sentinel(-1) 부여
            for t in missing:
                neon_ticker_id.setdefault(t, -1)
    else:
        log("[1] 부족 종목 없음")

    def remap(supa_stock_id: int) -> int | None:
        return neon_ticker_id.get(supa_stock_ticker.get(supa_stock_id))

    # ---------------------------------------------------------------
    # 2. 특허 순증분 (Supabase 실데이터 - Neon 기존)
    # ---------------------------------------------------------------
    supa_apps = {r["application_no"] for r in await supa.fetch(
        """SELECT p.application_no FROM patent_raw_details p
           JOIN raw_documents r ON r.id = p.raw_document_id WHERE r.source_name <> 'KIPRIS'""")}
    neon_apps = {r["application_no"] for r in await neon.fetch(
        "SELECT application_no FROM patent_raw_details WHERE application_no NOT LIKE 'MOCK%'")}
    net_new = sorted(supa_apps - neon_apps)
    log(f"\n[2] 특허: Supabase 실데이터 {len(supa_apps)} / Neon 기존 {len(neon_apps)} "
        f"-> 순증분 {len(net_new)}건")

    if net_new:
        prows = await supa.fetch(
            """SELECT r.stock_id, r.source_name, r.external_id, r.source_hash, r.title, r.source_url,
                      r.published_at, r.collect_status, r.collect_error, r.collected_at, r.collector_ver,
                      p.application_no, p.patent_title, p.applicant_name, p.application_date,
                      p.tech_category, p.is_new_category, p.extra_payload, p.llm_features, p.llm_status
               FROM patent_raw_details p JOIN raw_documents r ON r.id = p.raw_document_id
               WHERE r.source_name <> 'KIPRIS' AND p.application_no = ANY($1::text[])""", net_new)
        # stock_id 재매핑 + 매핑 실패 확인
        staged, dropped = [], 0
        for r in prows:
            nsid = remap(r["stock_id"])
            if nsid is None:
                dropped += 1
                continue
            staged.append((nsid, r["source_name"], r["external_id"], r["source_hash"], r["title"],
                           r["source_url"], r["published_at"], r["collect_status"], r["collect_error"],
                           r["collected_at"], r["collector_ver"], r["application_no"], r["patent_title"],
                           r["applicant_name"], r["application_date"], r["tech_category"],
                           r["is_new_category"], r["extra_payload"], r["llm_features"], r["llm_status"]))
        log(f"    적재 준비 {len(staged)}건 (재매핑 실패 {dropped}건)")

        if execute and staged:
            async with neon.transaction():
                await neon.execute("""CREATE TEMP TABLE stg_pat (
                    stock_id bigint, source_name text, external_id text, source_hash text, title text,
                    source_url text, published_at timestamptz, collect_status text, collect_error text,
                    collected_at timestamptz, collector_ver text, application_no text, patent_title text,
                    applicant_name text, application_date date, tech_category text, is_new_category bool,
                    extra_payload text, llm_features text, llm_status text) ON COMMIT DROP""")
                await neon.copy_records_to_table("stg_pat", records=staged)
                # 부모: raw_documents (id 는 시퀀스 발번), 중복 skip
                await neon.execute("""
                    INSERT INTO raw_documents (stock_id, collector_run_id, source_type, source_name,
                        external_id, source_hash, title, source_url, published_at, collect_status,
                        collect_error, collected_at, collector_ver)
                    SELECT stock_id, NULL, 'PATENT', source_name, external_id, source_hash, title,
                        source_url, published_at, collect_status, collect_error, collected_at, collector_ver
                    FROM stg_pat ON CONFLICT DO NOTHING""")
                # 자식: patent_raw_details, 부모 external_id(=application_no)로 새 id 조인, r.stock_id 사용(FK 보장)
                await neon.execute("""
                    INSERT INTO patent_raw_details (raw_document_id, stock_id, application_no, patent_title,
                        applicant_name, application_date, tech_category, is_new_category, extra_payload,
                        llm_features, llm_status)
                    SELECT r.id, r.stock_id, s.application_no, s.patent_title, s.applicant_name,
                        s.application_date, s.tech_category, s.is_new_category,
                        s.extra_payload::jsonb, s.llm_features::jsonb, s.llm_status
                    FROM stg_pat s
                    JOIN raw_documents r ON r.source_type='PATENT' AND r.external_id = s.application_no
                    ON CONFLICT DO NOTHING""")
            log("    특허 이관 완료")

    # ---------------------------------------------------------------
    # 3. DataLab 설정(categories / keywords / category_stocks)
    # ---------------------------------------------------------------
    cats = await supa.fetch("SELECT id, name, sector, description, is_active FROM datalab_categories")
    kws = await supa.fetch("""SELECT category_id, keyword, keyword_group, source, is_active, polarity,
        polarity_source, polarity_confidence, polarity_model, polarity_rationale, polarity_classified_at,
        review_status, validation_active_days, validation_window_days, validation_coverage, validated_at
        FROM datalab_category_keywords""")
    cstocks = await supa.fetch("SELECT category_id, stock_id, weight FROM datalab_category_stocks")
    log(f"\n[3] DataLab 설정: categories {len(cats)} / keywords {len(kws)} / category_stocks {len(cstocks)}")

    if execute:
        async with neon.transaction():
            await neon.executemany(
                """INSERT INTO datalab_categories (id, name, sector, description, is_active)
                   VALUES ($1,$2,$3,$4,$5) ON CONFLICT (id) DO NOTHING""",
                [(c["id"], c["name"], c["sector"], c["description"], c["is_active"]) for c in cats])
            # id 보존이므로 시퀀스를 최대 id 로 정합
            await neon.execute("SELECT setval('datalab_categories_id_seq', (SELECT max(id) FROM datalab_categories))")
            await neon.executemany(
                """INSERT INTO datalab_category_keywords (category_id, keyword, keyword_group, source,
                    is_active, polarity, polarity_source, polarity_confidence, polarity_model,
                    polarity_rationale, polarity_classified_at, review_status, validation_active_days,
                    validation_window_days, validation_coverage, validated_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                   ON CONFLICT (category_id, keyword) DO NOTHING""",
                [tuple(k[c] for c in k.keys()) for k in kws])
            cs_rows, cs_drop = [], 0
            for cs in cstocks:
                nsid = remap(cs["stock_id"])
                if nsid is None:
                    cs_drop += 1
                    continue
                cs_rows.append((cs["category_id"], nsid, cs["weight"]))
            await neon.executemany(
                """INSERT INTO datalab_category_stocks (category_id, stock_id, weight)
                   VALUES ($1,$2,$3) ON CONFLICT (category_id, stock_id) DO NOTHING""", cs_rows)
        log(f"    설정 이관 완료 (category_stocks 재매핑 실패 {cs_drop}건)")

    # ---------------------------------------------------------------
    # 4. DataLab 본체(raw_documents + raw_details)
    # ---------------------------------------------------------------
    dl_doc_cnt = await supa.fetchval("SELECT count(*) FROM datalab_raw_documents")
    dl_det_cnt = await supa.fetchval("SELECT count(*) FROM datalab_raw_details")
    neon_dl_det = await neon.fetchval("SELECT count(*) FROM datalab_raw_details")
    log(f"\n[4] DataLab 본체: Supabase docs {dl_doc_cnt} / details {dl_det_cnt}  (Neon 기존 details {neon_dl_det})")

    if execute:
        # 부모 documents (id 시퀀스 발번)
        docs = await supa.fetch("""SELECT category_id, source_name, external_id, source_hash, title,
            source_url, published_at, collect_status, collect_error, collected_at, collector_ver
            FROM datalab_raw_documents""")
        async with neon.transaction():
            await neon.execute("""CREATE TEMP TABLE stg_dld (
                category_id bigint, source_name text, external_id text, source_hash text, title text,
                source_url text, published_at timestamptz, collect_status text, collect_error text,
                collected_at timestamptz, collector_ver text) ON COMMIT DROP""")
            await neon.copy_records_to_table("stg_dld", records=[
                (d["category_id"], d["source_name"], d["external_id"], d["source_hash"], d["title"],
                 d["source_url"], d["published_at"], d["collect_status"], d["collect_error"],
                 d["collected_at"], d["collector_ver"]) for d in docs])
            await neon.execute("""
                INSERT INTO datalab_raw_documents (category_id, collector_run_id, source_name, external_id,
                    source_hash, title, source_url, published_at, collect_status, collect_error,
                    collected_at, collector_ver)
                SELECT category_id, NULL, source_name, external_id, source_hash, title, source_url,
                    published_at, collect_status, collect_error, collected_at, COALESCE(NULLIF(collector_ver,''),'1.0')
                FROM stg_dld ON CONFLICT DO NOTHING""")
        log(f"    documents {len(docs)}건 적재")

        # 자식 details : 부모 external_id 를 함께 끌어와 새 id 로 조인
        det = await supa.fetch("""SELECT dd.external_id AS parent_external_id, d.category_id, d.keyword,
            d.keyword_group, d.observed_date, d.search_index, d.previous_search_index, d.change_pct,
            d.period_type, d.device, d.gender, d.age_group, d.is_spike, d.extra_payload
            FROM datalab_raw_details d JOIN datalab_raw_documents dd ON dd.id = d.raw_document_id""")
        async with neon.transaction():
            await neon.execute("""CREATE TEMP TABLE stg_dldet (
                parent_external_id text, category_id bigint, keyword text, keyword_group text,
                observed_date date, search_index numeric, previous_search_index numeric, change_pct numeric,
                period_type text, device text, gender text, age_group text, is_spike bool,
                extra_payload text) ON COMMIT DROP""")
            await neon.copy_records_to_table("stg_dldet", records=[
                (d["parent_external_id"], d["category_id"], d["keyword"], d["keyword_group"],
                 d["observed_date"], d["search_index"], d["previous_search_index"], d["change_pct"],
                 d["period_type"], d["device"], d["gender"], d["age_group"], d["is_spike"],
                 d["extra_payload"]) for d in det])
            await neon.execute("""
                INSERT INTO datalab_raw_details (raw_document_id, category_id, keyword, keyword_group,
                    observed_date, search_index, previous_search_index, change_pct, period_type, device,
                    gender, age_group, is_spike, extra_payload)
                SELECT dd.id, s.category_id, s.keyword, s.keyword_group, s.observed_date, s.search_index,
                    s.previous_search_index, s.change_pct, s.period_type, s.device, s.gender, s.age_group,
                    s.is_spike, s.extra_payload::jsonb
                FROM stg_dldet s
                JOIN datalab_raw_documents dd ON dd.source_name='NAVER_DATALAB' AND dd.external_id = s.parent_external_id
                ON CONFLICT DO NOTHING""")
        log(f"    details {len(det)}건 적재")

    # ---------------------------------------------------------------
    # 5. 목데이터(KIPRIS) 삭제 : Neon + Supabase 양쪽
    # ---------------------------------------------------------------
    neon_mock = await neon.fetchval("SELECT count(*) FROM raw_documents WHERE source_type='PATENT' AND source_name='KIPRIS'")
    supa_mock = await supa.fetchval("SELECT count(*) FROM raw_documents WHERE source_type='PATENT' AND source_name='KIPRIS'")
    log(f"\n[5] 목데이터(KIPRIS) 삭제 대상 : Neon {neon_mock}건 / Supabase {supa_mock}건 "
        f"(patent_raw_details 는 ON DELETE CASCADE 로 함께 삭제)")
    if execute:
        d1 = await neon.execute("DELETE FROM raw_documents WHERE source_type='PATENT' AND source_name='KIPRIS'")
        d2 = await supa.execute("DELETE FROM raw_documents WHERE source_type='PATENT' AND source_name='KIPRIS'")
        log(f"    Neon {d1} / Supabase {d2}")

    # ---------------------------------------------------------------
    # 6. 사후 검증
    # ---------------------------------------------------------------
    if execute:
        log("\n[6] 사후 검증 (Neon)")
        for t, sql in [
            ("patent_raw_details", "SELECT count(*) FROM patent_raw_details"),
            ("  그중 MOCK", "SELECT count(*) FROM patent_raw_details WHERE application_no LIKE 'MOCK%'"),
            ("  특허 종목수", "SELECT count(DISTINCT stock_id) FROM patent_raw_details"),
            ("datalab_categories", "SELECT count(*) FROM datalab_categories"),
            ("datalab_category_keywords", "SELECT count(*) FROM datalab_category_keywords"),
            ("datalab_category_stocks", "SELECT count(*) FROM datalab_category_stocks"),
            ("datalab_raw_documents", "SELECT count(*) FROM datalab_raw_documents"),
            ("datalab_raw_details", "SELECT count(*) FROM datalab_raw_details"),
        ]:
            log(f"    {t}: {await neon.fetchval(sql)}")

    await supa.close()
    await neon.close()
    log("\n===== 완료 =====" + ("" if execute else "  (dry-run 이었습니다. 실제 적용하려면 --execute)"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="실제 쓰기(기본은 dry-run 읽기전용)")
    args = ap.parse_args()
    asyncio.run(main(args.execute))
