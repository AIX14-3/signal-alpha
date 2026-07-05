"""OpenDART 매출 리더 순수 로직 테스트 (네트워크 없음).

- known_at_from_rcept: 접수번호 앞 8자리 → 공시일, 비정상 입력 None
- extract_revenue: 손익계산서 매출 계정(account_id 우선·계정명 폴백), 당기금액
- single_quarter_rows: 누적(YTD) → 단일분기 차분, 결측 분기 처리, known_at 부여
"""
from __future__ import annotations

from datetime import date

from app.ml.research.fundamentals_dart import (
    extract_revenue,
    known_at_from_rcept,
    single_quarter_rows,
)
from app.ml.research.fundamentals_dataset import quarter_end_date, yoy_growth


def test_known_at_from_rcept():
    assert known_at_from_rcept("20230515000123") == "2023-05-15"
    assert known_at_from_rcept("  20221114000999 ") == "2022-11-14"
    assert known_at_from_rcept("") is None
    assert known_at_from_rcept("badvalue") is None
    assert known_at_from_rcept("20231345000000") is None  # invalid month/day


def test_extract_revenue_prefers_account_id():
    items = [
        {"sj_div": "BS", "account_id": "ifrs-full_Assets", "thstrm_amount": "999", "rcept_no": "20230515000001"},
        {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액",
         "thstrm_amount": "1,000,000", "rcept_no": "20230515000001"},
    ]
    assert extract_revenue(items) == (1000000, "20230515000001")


def test_extract_revenue_prefers_cumulative_add_amount():
    # 중간보고서: thstrm_amount=3개월(단일), thstrm_add_amount=누적 → 누적을 잡아야 함
    items = [
        {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액",
         "thstrm_amount": "77203607000000", "thstrm_add_amount": "154985105000000",
         "rcept_no": "20220816000001"},
    ]
    assert extract_revenue(items) == (154985105000000, "20220816000001")


def test_extract_revenue_falls_back_to_thstrm_when_no_add():
    # 연간보고서: add 비어있음 → thstrm_amount(연간누적) 사용
    items = [
        {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액",
         "thstrm_amount": "302231360000000", "thstrm_add_amount": "",
         "rcept_no": "20230307000001"},
    ]
    assert extract_revenue(items) == (302231360000000, "20230307000001")


def test_extract_revenue_falls_back_to_name():
    items = [
        {"sj_div": "IS", "account_id": "", "account_nm": "매출액",
         "thstrm_amount": "500", "rcept_no": "20230515000002"},
    ]
    assert extract_revenue(items) == (500, "20230515000002")


def test_extract_revenue_none_when_no_income_statement_revenue():
    items = [{"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "100", "rcept_no": "x"}]
    assert extract_revenue(items) is None


def test_single_quarter_differencing_full_year():
    # 누적: Q1=100, H1=250, 9M=420, FY=600  → 단일: Q1=100,Q2=150,Q3=170,Q4=180
    cumulative = {
        "11013": (100, "20220515000001"),
        "11012": (250, "20220814000001"),
        "11014": (420, "20221114000001"),
        "11011": (600, "20230315000001"),
    }
    rows = single_quarter_rows("005930", "00126380", 2022, cumulative, fs_div="CFS")
    by_q = {r.quarter: r.single_revenue_krw for r in rows}
    assert by_q == {1: 100, 2: 150, 3: 170, 4: 180}
    # known_at 은 각 분기를 드러낸 보고서 공시일
    by_known = {r.quarter: r.known_at for r in rows}
    assert by_known[2] == "2022-08-14"
    assert by_known[4] == "2023-03-15"
    assert rows[0].period_label == "2022Q1"


def test_single_quarter_skips_when_adjacent_missing():
    # H1 누적 결측 → Q2(차분 불가)·다음 분기 차분 영향. Q1만, Q3는 직전(H1) 없어 스킵.
    cumulative = {
        "11013": (100, "20220515000001"),
        "11014": (420, "20221114000001"),  # H1 없음 → Q3 차분 불가
        "11011": (600, "20230315000001"),  # 9M 있으니 Q4 차분 가능
    }
    rows = single_quarter_rows("005930", "00126380", 2022, cumulative, fs_div="CFS")
    by_q = {r.quarter: r.single_revenue_krw for r in rows}
    assert by_q.get(1) == 100      # Q1 OK
    assert 2 not in by_q           # H1 결측 → Q2 없음
    assert 3 not in by_q           # 직전(H1) 결측 → Q3 차분 불가
    assert by_q.get(4) == 180      # 9M(420)→FY(600) 차분 가능


# --- 데이터셋 순수 함수 ------------------------------------------------------

def test_quarter_end_date():
    assert quarter_end_date(2022, 1) == date(2022, 3, 31)
    assert quarter_end_date(2022, 2) == date(2022, 6, 30)
    assert quarter_end_date(2022, 3) == date(2022, 9, 30)
    assert quarter_end_date(2022, 4) == date(2022, 12, 31)


def test_yoy_growth_uses_prior_year_same_quarter():
    rev = {
        (2021, 1): 100.0, (2021, 2): 100.0,
        (2022, 1): 120.0,  # +20% vs 2021Q1
        (2022, 2): 90.0,   # -10% vs 2021Q2
    }
    g = yoy_growth(rev)
    assert abs(g[(2022, 1)] - 0.20) < 1e-9
    assert abs(g[(2022, 2)] - (-0.10)) < 1e-9
    assert (2021, 1) not in g   # 전년 동기(2020Q1) 없음 → 제외


def test_yoy_growth_skips_zero_prior():
    rev = {(2021, 1): 0.0, (2022, 1): 50.0}
    assert (2022, 1) not in yoy_growth(rev)
