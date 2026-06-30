"""Extract PIT surge keywords from DART disclosure titles → per-ticker JSON.

Mirrors scripts/extract_period_keywords.py but with DartTitleSource and a DART
disclosure-boilerplate stopword set. Per ticker we dedupe period-keywords to the
top-``--per-ticker`` by surge score (keeping the EARLIEST first_avail_date so the
PIT anchor is honest), then write two files per ticker:

  kw_dart/patent_keywords_<ticker>.json   = [{"keyword": k}, ...]   (DataLab collect)
  dart_meta/patent_keywords_<ticker>.json = [{ticker, keyword, first_avail_date}]  (PIT gate)

The cap keeps the downstream DataLab collection inside the daily quota.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.keywords.extract import extract_period_keywords  # noqa: E402
from app.ml.keywords.sources import DartTitleSource  # noqa: E402

# Structural disclosure tokens that carry no event signal (surge already suppresses
# constant boilerplate; this trims the long tail of report-form words).
DART_STOP = {
    "보고서", "신고서", "정정", "공시", "결정", "기재", "첨부", "기재정정", "첨부정정",
    "사업보고서", "분기보고서", "반기보고서", "감사보고서", "주요사항보고서", "정기보고서",
    "임원", "주요주주", "특정증권등", "소유상황보고서", "소유주식", "변동신고서", "최대주주",
    "대량보유", "의결권", "안내공시", "조회공시", "안내", "조회", "풍문", "보도", "제출",
    "관련", "등의", "주주총회", "영업보고", "결산", "현황", "내역", "공고", "투자판단",
    "수시공시", "자율공시", "지주회사", "특수관계인", "계열회사",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", default="dart_disclosures.json")
    ap.add_argument("--kw-out", default="kw_dart", help="dir for [{keyword}] collect files")
    ap.add_argument("--meta-out", default="dart_meta", help="dir for first_avail_date meta")
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--top-k", type=int, default=10, help="top terms per period")
    ap.add_argument("--min-count", type=int, default=3)
    ap.add_argument("--per-ticker", type=int, default=12, help="cap unique keywords/ticker")
    args = ap.parse_args()

    src = DartTitleSource(args.inp)
    kw_dir = Path(args.kw_out)
    meta_dir = Path(args.meta_out)
    kw_dir.mkdir(exist_ok=True)
    meta_dir.mkdir(exist_ok=True)

    grand_total = 0
    for ticker in src.tickers():
        pks = extract_period_keywords(
            src.records(ticker),
            months=args.months,
            top_k=args.top_k,
            min_count=args.min_count,
            extra_stopwords=DART_STOP,
        )
        # Dedupe by keyword: earliest first_avail_date (honest PIT anchor), best score.
        best: dict[str, dict] = {}
        for k in pks:
            cur = best.get(k.keyword)
            if cur is None:
                best[k.keyword] = {
                    "ticker": ticker,
                    "keyword": k.keyword,
                    "first_avail_date": k.first_avail_date,
                    "score": k.score,
                }
            else:
                if k.first_avail_date < cur["first_avail_date"]:
                    cur["first_avail_date"] = k.first_avail_date
                if k.score > cur["score"]:
                    cur["score"] = k.score
        # Top per-ticker by score.
        ranked = sorted(best.values(), key=lambda d: d["score"], reverse=True)[: args.per_ticker]

        (kw_dir / f"patent_keywords_{ticker}.json").write_text(
            json.dumps([{"keyword": d["keyword"]} for d in ranked], ensure_ascii=False),
            encoding="utf-8",
        )
        (meta_dir / f"patent_keywords_{ticker}.json").write_text(
            json.dumps(
                [
                    {"ticker": d["ticker"], "keyword": d["keyword"],
                     "first_avail_date": d["first_avail_date"]}
                    for d in ranked
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        grand_total += len(ranked)
        print(f"{ticker}: {len(ranked)} kw  e.g. {[d['keyword'] for d in ranked[:5]]}")

    print(f"\n[out] {grand_total} keywords across {len(src.tickers())} tickers "
          f"-> {kw_dir}/ + {meta_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
