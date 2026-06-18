# services/agent-worker/script/dashboard_validator.py
"""데이터 정합성 '눈검증'용 Streamlit 미니 대시보드.

완성 대시보드가 아니라 검증 도구다 — keyword_generator→부트스트랩으로 적재된
3년치 검색 기준선(hiring_baseline)과 일별 채용 공고/신호(hiring_raw_details,
hiring_signals)가 기업별로 온전한지 눈으로 확인한다. 차트의 절벽/Null/튀는 선이
파이프라인 버그를 즉시 드러낸다.

실행:
    uv sync --all-packages --group dashboard      # 최초 1회(streamlit/pandas 설치)
    uv run streamlit run services/agent-worker/script/dashboard_validator.py
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


@st.cache_resource
def get_engine():
    return create_engine(DB_URL, future=True)


def q(sql: str, **params) -> pd.DataFrame:
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or None)


st.set_page_config(page_title="채용 데이터 검증기", layout="wide")
st.title("🔍 채용 변화율 데이터 검증기")
st.caption("완성 대시보드 아님 — 정합성 눈검증용. 빈/이상 구간이 파이프라인 버그를 드러냅니다.")

companies = q("SELECT id, name FROM stocks WHERE is_target = TRUE ORDER BY name")
if companies.empty:
    st.error("stocks(is_target=TRUE)가 비어 있습니다. 시드/마이그레이션을 확인하세요.")
    st.stop()

name = st.selectbox("정합성 확인할 기업 선택", companies["name"].tolist())
stock_id = int(companies.loc[companies["name"] == name, "id"].iloc[0])

# ── 3년치 검색 기준선 (hiring_baseline) — 2일치 hiring만 있어도 검증 가능한 핵심 ──
st.subheader("📊 3년치 검색 기준선 (hiring_baseline)")
bl = q("SELECT * FROM hiring_baseline WHERE stock_id = :sid", sid=stock_id)
if bl.empty:
    st.warning("이 기업의 baseline이 없습니다 — 부트스트랩 미실행 또는 stocks 매칭 실패 의심.")
else:
    r = bl.iloc[0]
    cols = st.columns(5)
    cols[0].metric("평균 검색량", f"{r['avg_search_volume']:.2f}")
    for i, qn in enumerate(["q1_factor", "q2_factor", "q3_factor", "q4_factor"], start=1):
        cols[i].metric(f"Q{i} 계수", f"{r[qn]:.2f}")
    st.caption(
        f"데이터 기간: {r['data_start_date']} ~ {r['data_end_date']} · "
        f"키워드그룹: {r['keyword_group_name']}"
    )
    factors = pd.DataFrame(
        {"분기계수": [r["q1_factor"], r["q2_factor"], r["q3_factor"], r["q4_factor"]]},
        index=["Q1", "Q2", "Q3", "Q4"],
    )
    st.bar_chart(factors)
    st.caption("계수 1.0 = 연중 평균. >1 분기에 검색(=채용 관심)이 몰림. 전부 1.0이면 데이터 빈약 신호.")

# ── 일별 채용 공고 / 신호 (현재 2일치 — 며칠 누적돼야 추이 의미) ──
st.subheader("📈 일별 채용 공고·신호")
st.caption("현재 hiring 데이터가 며칠치뿐이면 선 차트는 점 몇 개로 보입니다(고장 아님).")

jobs = q(
    "SELECT observed_date, SUM(job_count) AS job_count "
    "FROM hiring_raw_details WHERE stock_id = :sid "
    "GROUP BY observed_date ORDER BY observed_date",
    sid=stock_id,
)
sig = q(
    "SELECT observed_date, job_count, baseline, relative_strength, calculation_phase, is_spike "
    "FROM hiring_signals WHERE stock_id = :sid ORDER BY observed_date",
    sid=stock_id,
)

left, right = st.columns(2)
with left:
    st.markdown("**일별 공고 수 (hiring_raw_details)**")
    if jobs.empty:
        st.info("공고 데이터 없음.")
    else:
        st.line_chart(jobs.set_index("observed_date"))
with right:
    st.markdown("**상대 강도 / 신호 (hiring_signals)**")
    if sig.empty:
        st.info("신호 데이터 없음 — analyzer 미실행.")
    else:
        st.line_chart(sig.set_index("observed_date")[["relative_strength"]])

if not sig.empty:
    st.markdown("**신호 원본(hiring_signals)**")
    st.dataframe(sig, use_container_width=True)
