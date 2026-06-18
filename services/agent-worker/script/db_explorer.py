# services/agent-worker/script/db_explorer.py
"""DB 탐색기 — 팀원 온보딩용 전체 스키마·라이브 적재현황 시각화 (Streamlit).

구조 ERD(database/erd/signal_alpha_core_erd.md)를 '대체'가 아니라 '보완'한다:
이 도구는 **지금 DB에 실제로 무엇이 얼마나 들어있는지**(라이브 적재현황)를 Zone(도메인)별로
브라우징한다. 본인 작업 영역의 테이블을 찾고, 컬럼/FK/샘플 데이터를 눈으로 확인하는 용도.

설치/실행 (uv 워크스페이스 — pip install 금지):
    uv sync --all-packages --group dashboard       # ⚠️ --all-packages 필수(없으면 멤버 deps 소실)
    uv run streamlit run services/agent-worker/script/db_explorer.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT.parent.parent / ".env")  # repo root .env → DATABASE_URL

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha",
)
ERD_DOC = "database/erd/signal_alpha_core_erd.md"

# ── Zone(도메인) 분류 ────────────────────────────────────────────────────────
# token이 '_'로 끝나면 접두사 매칭, 아니면 정확 매칭. 첫 매칭 Zone 반환, 미스는 "기타".
ZONE_MAPPING: dict[str, list[str]] = {
    "Core (공통)": ["stocks", "collector_runs"],
    "수집·처리 공통": ["raw_documents", "processing_queue", "dead_letter", "source_documents", "validation_logs"],
    "Zone C — Hiring": ["hiring_"],
    "Zone C — DataLab": ["datalab_"],
    "Zone C — DART": ["dart_"],
    "Zone C — Patent": ["patent_"],
    "Zone C — Report": ["report_"],
    "Zone A — Market/Price": ["sec_filings", "ohlcv_data", "price_snapshots", "fundamentals"],
    "Zone E — Analysis/Score": [
        "analysis_", "ai_scores", "ml_scores", "quant_scores", "ta_scores", "agent_results",
        "final_signals", "score_history", "backtest_results", "signal_events", "signal_metrics",
        "xgb_model_versions",
    ],
    "Zone B/F — User/Billing": [
        "users", "social_accounts", "terms_agreements", "portone_verifications", "subscription_plans",
        "signal_subscriptions", "watchlists", "signal_journals", "user_signal_reads", "user_sessions",
    ],
    "Zone G — Admin": ["admin_"],
    "Meta": ["schema_migrations"],
}
ZONE_ORDER = list(ZONE_MAPPING) + ["기타"]


def get_zone_name(table: str) -> str:
    for zone, tokens in ZONE_MAPPING.items():
        for tok in tokens:
            if tok.endswith("_") and table.startswith(tok):
                return zone
            if not tok.endswith("_") and table == tok:
                return zone
    return "기타"


# ── 메타데이터 조회 (read-only, 캐시) ────────────────────────────────────────
_FK_SQL = """
SELECT con.conrelid::regclass::text AS tbl, att.attname AS col,
       con.confrelid::regclass::text AS ref_tbl, refatt.attname AS ref_col
FROM pg_constraint con
JOIN LATERAL unnest(con.conkey, con.confkey) WITH ORDINALITY AS k(conkey, confkey, ord) ON true
JOIN pg_attribute att    ON att.attrelid = con.conrelid    AND att.attnum = k.conkey
JOIN pg_attribute refatt ON refatt.attrelid = con.confrelid AND refatt.attnum = k.confkey
WHERE con.contype = 'f' AND con.connamespace = 'public'::regnamespace
ORDER BY tbl, col
"""


@st.cache_resource
def get_engine():
    return create_engine(DB_URL, future=True)


@st.cache_data(ttl=300)
def fetch_metadata() -> dict:
    eng = get_engine()
    with eng.connect() as c:
        tables = [r[0] for r in c.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
        )).fetchall()]
        counts = {}
        for t in tables:
            try:
                counts[t] = c.execute(text(f'SELECT count(*) FROM "{t}"')).scalar()
            except Exception:
                counts[t] = None
        cols = pd.read_sql(text(
            "SELECT table_name, column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema='public' "
            "ORDER BY table_name, ordinal_position"
        ), c)
        fks = pd.read_sql(text(_FK_SQL), c)
    rows = [{"table": t, "zone": get_zone_name(t), "rows": counts[t],
             "status": "✅" if (counts[t] or 0) > 0 else "⬜"} for t in tables]
    return {"tables": pd.DataFrame(rows), "columns": cols, "fks": fks}


@st.cache_data(ttl=300)
def fetch_sample(table: str) -> pd.DataFrame:
    with get_engine().connect() as c:
        return pd.read_sql(text(f'SELECT * FROM "{table}" LIMIT 20'), c)


# ── UI ───────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Signal-Alpha DB 탐색기", layout="wide")
st.title("🗂️ Signal-Alpha DB 탐색기")
st.caption(f"라이브 적재현황 · 구조 상세 ERD는 `{ERD_DOC}` 참조 · 캐시 5분(좌측 새로고침)")

if st.sidebar.button("🔄 메타 새로고침"):
    fetch_metadata.clear()
    fetch_sample.clear()

meta = fetch_metadata()
tdf = meta["tables"]
counts = dict(zip(tdf["table"], tdf["rows"]))


def badge(tbl: str) -> str:
    return "✅" if (counts.get(tbl) or 0) > 0 else "⬜"


# 🛰️ 활성 데이터 흐름 Status Bar
st.markdown("#### 🛰️ 활성 데이터 흐름 (지금 흐르는 파이프라인)")
flow = ["stocks", "collector_runs", "raw_documents", "hiring_raw_details", "hiring_signals"]
st.markdown(" ➔ ".join(f"**{t}**({badge(t)})" for t in flow))
st.caption(
    f"보조: hiring_baseline({badge('hiring_baseline')} 3년 검색기준선) · "
    f"hiring_sources({badge('hiring_sources')}) · "
    f"datalab_categories({badge('datalab_categories')} 시드) · processing_queue({badge('processing_queue')})"
)

# 개요 메트릭
total = len(tdf)
populated = int((tdf["rows"].fillna(0) > 0).sum())
m1, m2, m3 = st.columns(3)
m1.metric("전체 테이블", total)
m2.metric("적재됨 ✅", populated)
m3.metric("비어있음 ⬜", total - populated)

st.divider()

# 사이드바 필터
st.sidebar.header("필터")
zones = [z for z in ZONE_ORDER if z in set(tdf["zone"])]
sel_zones = st.sidebar.multiselect("Zone", zones, default=zones)
only_pop = st.sidebar.checkbox("채워진 것만", value=False)
search = st.sidebar.text_input("테이블 이름 검색", "")

view = tdf[tdf["zone"].isin(sel_zones)].copy()
if only_pop:
    view = view[view["rows"].fillna(0) > 0]
if search:
    view = view[view["table"].str.contains(search, case=False)]

# Zone별 적재 표
st.subheader("📋 Zone별 적재 현황")
view = view.assign(_z=pd.Categorical(view["zone"], categories=ZONE_ORDER, ordered=True))
view = view.sort_values(["_z", "rows"], ascending=[True, False])
st.dataframe(
    view[["status", "zone", "table", "rows"]].rename(
        columns={"status": "상태", "zone": "Zone", "table": "테이블", "rows": "행수"}
    ),
    use_container_width=True, hide_index=True,
)

st.divider()

# 테이블 드릴다운 — 3탭
st.subheader("🔍 테이블 상세")
table = st.selectbox("테이블 선택", sorted(tdf["table"].tolist()))
zone = get_zone_name(table)
n = counts.get(table)
st.markdown(f"**{table}** · Zone: `{zone}` · 행수: **{n if n is not None else '?'}** {badge(table)}")

tab1, tab2, tab3 = st.tabs(["📦 실제 데이터 샘플", "🧬 스키마 & FK", "🗺️ ERD & 작업영역"])

with tab1:
    if (n or 0) == 0:
        st.warning("⚠️ 빈 테이블 — 적재된 데이터가 없습니다 (다른 도메인/미래 영역이거나 미실행).")
    else:
        sample = fetch_sample(table)
        st.dataframe(sample, use_container_width=True)
        jsonb_cols = meta["columns"].query("table_name == @table and data_type == 'jsonb'")["column_name"].tolist()
        if jsonb_cols and not sample.empty:
            with st.expander("🧩 JSONB 상세 보기 (트리)"):
                jc = st.selectbox("JSONB 컬럼", jsonb_cols, key="jc")
                ri = st.number_input("행 인덱스", 0, len(sample) - 1, 0, key="ri")
                st.json(sample.iloc[int(ri)][jc])

with tab2:
    cols = meta["columns"].query("table_name == @table")[
        ["column_name", "data_type", "is_nullable", "column_default"]
    ].rename(columns={"column_name": "컬럼", "data_type": "타입",
                      "is_nullable": "Null허용", "column_default": "기본값"})
    st.markdown("**컬럼**")
    st.dataframe(cols, use_container_width=True, hide_index=True)
    fks = meta["fks"]
    out = fks[fks["tbl"] == table]
    inb = fks[fks["ref_tbl"] == table]
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**FK Out (이 테이블 → 참조 대상)**")
        if out.empty:
            st.caption("없음")
        else:
            for _, r in out.iterrows():
                st.markdown(f"- `{r['col']}` → `{r['ref_tbl']}.{r['ref_col']}`")
    with c2:
        st.markdown("**FK In (이 테이블을 참조하는 쪽)**")
        if inb.empty:
            st.caption("없음")
        else:
            for _, r in inb.iterrows():
                st.markdown(f"- `{r['tbl']}.{r['col']}` → `{r['ref_col']}`")

with tab3:
    st.markdown(f"이 테이블은 **{zone}** 도메인에 속합니다.")
    st.markdown(
        "- 같은 Zone 테이블: "
        + ", ".join(f"`{t}`" for t in sorted(tdf[tdf['zone'] == zone]['table']))
    )
    st.markdown(
        f"- 구조·관계 상세(엔티티 다이어그램)는 저장소의 **`{ERD_DOC}`**(Zone A~G mermaid ERD)를 참고하세요."
    )
    st.caption("이 탐색기는 ERD의 '구조'를 보완하는 '라이브 적재현황' 뷰입니다.")
