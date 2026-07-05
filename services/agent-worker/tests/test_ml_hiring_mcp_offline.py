"""MCP-오프라인 채용 로더 테스트 — precise rematch 재현 + build 배선 검증.

precise rematch 의 정확성(정규화 exact match·모호이름 드롭·비유니버스 드롭)이 confirmed 런과
동형인지, build_dataset_from_dumps 가 build_revenue_dataset 로 올바로 연결되는지 확인한다.
"""

from __future__ import annotations

import csv

from app.ml.research.datalab_dataset import Dataset
from app.ml.research.hiring_mcp_offline import (
    build_dataset_from_dumps,
    posting_counts_by_ticker,
    rematch_postings,
)

# (id, ticker, name, short_name)
STOCKS = [
    (1, "005930", "삼성전자", "삼성전자"),
    (2, "000660", "SK하이닉스", "SK하이닉스"),
    (3, "035420", "NAVER", "네이버"),
]


def test_posting_counts_exact_and_whitespace_insensitive():
    # 'SK 하이닉스'(공백) == 'SK하이닉스', '없는회사'는 유니버스 밖 → 무시.
    counts = posting_counts_by_ticker(
        STOCKS,
        [("삼성전자", 20), ("SK 하이닉스", 5), ("네이버", 15), ("없는회사", 99)],
    )
    assert counts == {"005930": 20, "000660": 5, "035420": 15}


def test_ambiguous_name_dropped():
    # 두 종목이 같은 정규화 이름을 공유하면 그 이름은 어느 쪽에도 귀속되지 않는다.
    stocks = STOCKS + [(4, "900000", "삼성전자", None)]  # '삼성전자' 중복
    counts = posting_counts_by_ticker(stocks, [("삼성전자", 20), ("네이버", 15)])
    assert "005930" not in counts and "900000" not in counts
    assert counts == {"035420": 15}


def test_rematch_drops_non_universe_and_maps_by_norm_name():
    postings = [
        {"source_name": "삼성전자", "observed_date": "2021-03-02", "duty_groups": ["웹개발"]},
        {"source_name": "SK하이닉스", "observed_date": "2021-04-01", "duty_groups": []},
        {"source_name": "카카오", "observed_date": "2021-05-01", "duty_groups": []},  # 비유니버스
    ]
    id_by_ticker, by_stock, matched, dropped = rematch_postings(
        STOCKS, postings, tickers={"005930", "000660"}, with_duty=True
    )
    assert matched == 2 and dropped == 1          # 카카오 드롭
    assert set(by_stock) == {1, 2}
    assert by_stock[1][0]["duty_groups"] == ["웹개발"]
    assert "035420" not in id_by_ticker           # 유니버스 밖 티커는 map 에 없음


def _write_revenue_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "corp_code", "year", "quarter", "period_label",
                    "single_revenue_krw", "known_at", "fs_div"])
        for r in rows:
            w.writerow(r)


def test_build_dataset_from_dumps_wires_through(tmp_path):
    # 두 종목 × 2년 Q1 매출(YoY 가능) + 채용 포스팅 → Dataset 산출(배선 확인).
    rev = tmp_path / "rev.csv"
    _write_revenue_csv(rev, [
        ("005930", "x", 2021, 1, "2021Q1", 1000, "2021-05-15", "CFS"),
        ("005930", "x", 2022, 1, "2022Q1", 1200, "2022-05-15", "CFS"),
        ("000660", "y", 2021, 1, "2021Q1", 500, "2021-05-15", "CFS"),
        ("000660", "y", 2022, 1, "2022Q1", 400, "2022-05-15", "CFS"),
    ])
    postings = []
    for sn in ("삼성전자", "SK하이닉스"):
        for d in ("2020-06-01", "2020-09-01", "2021-06-01", "2021-09-01",
                  "2021-12-01", "2022-01-15"):
            postings.append({"source_name": sn, "observed_date": d,
                             "duty_groups": ["웹개발", "QA"]})
    ds = build_dataset_from_dumps(
        stocks_rows=STOCKS, postings=postings, revenue_csv=str(rev),
        tickers=["005930", "000660"], feature_set="volume+duty",
        min_observations=1, min_cross_section=1,
    )
    assert isinstance(ds, Dataset)
    assert set(ds.stock_ids.tolist()) <= {1, 2}
    assert any(n.startswith("hiring__tech_share") for n in ds.feature_names)  # duty 피처 포함
