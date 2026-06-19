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

# 가드 로직을 복제하지 않고 실제 함수를 재사용 → 배지가 분석기 가드(#290)와 절대 어긋나지 않음.
from app.evidence_loaders.hiring_loader import WARMUP_PRIOR_DAYS, _warming_up_pairs

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

# ── 3개년 네이버 검색량 트렌드 (원시 시계열 hiring_search_trend, #291) ──
trend = q(
    "SELECT period_date, search_index FROM hiring_search_trend "
    "WHERE stock_id = :sid ORDER BY period_date",
    sid=stock_id,
)
if trend.empty:
    st.caption("📈 원시 검색 시계열 미적재 — 부트스트랩 재실행 필요(#291).")
else:
    st.subheader("📈 3개년 네이버 검색량 트렌드 (원시 시계열)")
    trend["period_date"] = pd.to_datetime(trend["period_date"])  # 시계열 축 인식
    st.line_chart(trend.set_index("period_date"))
    st.caption("baseline(avg/분기계수)의 산출 근거가 되는 주간 검색지수 원본. 절벽/공백은 수집 누락 신호.")

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

# ── 🌱 소스별 Warming-up 상태 (#316) — 분석기 가드 함수(_warming_up_pairs) 직접 재사용 ──
st.subheader("🌱 소스별 Warming-up 상태")
src_df = q(
    "SELECT observed_date, "
    "COALESCE(extra_payload->>'source_type', 'PORTAL_UNKNOWN') AS source_key "
    "FROM hiring_raw_details WHERE stock_id = :sid",
    sid=stock_id,
)
if src_df.empty:
    st.info("수집 이력 없음 — 이 종목은 아직 공고가 수집되지 않았습니다.")
else:
    # 가드와 동일 판정: 행을 (source_key, observed_date ISO) 로 만들어 실제 _warming_up_pairs 호출.
    rows = [
        {
            "source_key": r.source_key,
            "observed_date": pd.Timestamp(r.observed_date).date().isoformat(),
        }
        for r in src_df.itertuples(index=False)
    ]
    warming = _warming_up_pairs(rows)  # 제외 (source_key, date) 집합

    dates_by_src: dict[str, set[str]] = {}
    for row in rows:
        dates_by_src.setdefault(row["source_key"], set()).add(row["observed_date"])

    status_rows = []
    for src, dset in sorted(dates_by_src.items()):
        dates = sorted(dset)
        last = dates[-1]
        if (src, last) in warming:  # 최신 수집일이 제외(웜업) 대상인가
            prev = [d for d in dates if d < last]
            reason = (
                "첫 등장" if not prev
                else f"{(pd.Timestamp(last) - pd.Timestamp(prev[-1])).days}일 공백 후 재개"
            )
            grace = (pd.Timestamp(last) + pd.Timedelta(days=WARMUP_PRIOR_DAYS)).date().isoformat()
            status = "⏳ 웜업 중 (이 날짜 제외)"
        else:
            reason = f"직전 {WARMUP_PRIOR_DAYS}일 내 이력 보유"
            grace = "-"
            status = "✅ 반영 중"
        status_rows.append({
            "소스": src,
            "최신 수집일": last,
            "수집일수": len(dates),
            "상태": status,
            "사유": reason,
            "재수집 유예 마감": grace,
        })

    st.dataframe(pd.DataFrame(status_rows), use_container_width=True)
    st.caption(
        f"Warming-up 가드(#290): 소스가 **직전 {WARMUP_PRIOR_DAYS}일 내 이력**이 있어야 그 날짜 공고가 분석에 "
        "반영됩니다(첫 등장·장기공백 후 재개는 가짜 급등이라 제외). ⏳ 소스는 '재수집 유예 마감'일까지 다시 "
        "수집되면 그 날부터 반영됩니다. → 신호가 중립/없음이어도 **고장이 아니라 웜업 중**일 수 있습니다."
    )
