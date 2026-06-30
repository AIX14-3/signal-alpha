"""Turn DART disclosure titles into searchable PIT EVENT keywords: "[종목명] 이슈".

Raw disclosure titles are formal filing-form names (주요주주특정증권등소유상황보고서…)
that nobody types into a search box. Instead we detect ~20 market-moving disclosure
TYPES by substring and emit the plain-language search phrase people actually use —
e.g. a 유상증자결정 filing → keyword "삼성전자 유상증자". The event's rcept_dt is the
point-in-time anchor (first_avail_date), so the keyword is honestly tied to a real
dated event, not hand-picked with hindsight (the TYPE list is fixed, not cherry-picked).

Per stock we emit one keyword per event type that actually occurred, with
first_avail_date = the earliest rcept_dt of that type for that stock. Output:
  kw_dart_event/patent_keywords_<ticker>.json   = [{"keyword": "[name] issue"}]  (collect)
  dart_event_meta/patent_keywords_<ticker>.json = [{ticker, keyword, first_avail_date}]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# Clean searchable company names (the universe file has abbreviations like "NC").
NAMES = {
    "005930": "삼성전자", "066570": "LG전자", "005380": "현대차", "000270": "기아",
    "035720": "카카오", "036570": "엔씨소프트", "259960": "크래프톤", "352820": "하이브",
    "097950": "CJ제일제당", "004370": "농심", "003230": "삼양식품", "090430": "아모레퍼시픽",
    "282330": "BGF리테일", "007070": "GS리테일", "033780": "KT&G", "271560": "오리온",
    "023530": "롯데쇼핑", "139480": "이마트", "051900": "LG생활건강", "000100": "유한양행",
    "003490": "대한항공",
}

# (title substrings that signal the type) -> searchable plain issue phrase.
EVENT_TYPES: list[tuple[list[str], str]] = [
    (["유상증자"], "유상증자"),
    (["무상증자"], "무상증자"),
    (["전환사채"], "전환사채"),
    (["신주인수권부사채"], "신주인수권부사채"),
    (["교환사채"], "교환사채"),
    (["자기주식취득", "자기주식 취득", "자기주식취득신탁"], "자사주 매입"),
    (["자기주식처분", "자기주식 처분"], "자사주 처분"),
    (["공급계약", "단일판매"], "공급계약"),
    (["합병"], "합병"),
    (["분할"], "분할"),
    (["영업양수", "영업양도"], "영업양도"),
    (["소송"], "소송"),
    (["횡령", "배임"], "횡령"),
    (["잠정실적", "영업실적", "매출액또는손익", "영업(잠정)실적"], "실적"),
    (["감자"], "감자"),
    (["최대주주변경", "최대주주 변경"], "최대주주 변경"),
    (["현금ㆍ현물배당", "현금배당", "주식배당", "결산배당", "분기배당"], "배당"),
    (["주식분할", "액면분할"], "액면분할"),
    (["상장폐지", "관리종목", "상장적격성"], "상장폐지"),
    (["부도", "회생절차", "파산"], "부도"),
    (["타법인주식및출자증권취득", "타법인주식취득"], "지분투자"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", default="dart_disclosures.json")
    ap.add_argument("--kw-out", default="kw_dart_event")
    ap.add_argument("--meta-out", default="dart_event_meta")
    args = ap.parse_args()

    data: dict[str, list[dict]] = json.loads(Path(args.inp).read_text(encoding="utf-8"))
    kw_dir = Path(args.kw_out)
    meta_dir = Path(args.meta_out)
    kw_dir.mkdir(exist_ok=True)
    meta_dir.mkdir(exist_ok=True)

    grand = 0
    for ticker, rows in data.items():
        name = NAMES.get(ticker)
        if not name:
            print(f"{ticker}: no clean name, skipped")
            continue
        # earliest rcept_dt per issue type that actually occurred for this stock
        first_date: dict[str, str] = {}
        for r in rows:
            title = r.get("report_nm") or ""
            rcept = r.get("rcept_dt") or ""
            if len(rcept) < 8:
                continue
            iso = f"{rcept[:4]}-{rcept[4:6]}-{rcept[6:8]}"
            for patterns, issue in EVENT_TYPES:
                if any(p in title for p in patterns):
                    if issue not in first_date or iso < first_date[issue]:
                        first_date[issue] = iso
        items = [
            {"ticker": ticker, "keyword": f"{name} {issue}", "first_avail_date": iso}
            for issue, iso in sorted(first_date.items(), key=lambda kv: kv[1])
        ]
        (kw_dir / f"patent_keywords_{ticker}.json").write_text(
            json.dumps([{"keyword": it["keyword"]} for it in items], ensure_ascii=False),
            encoding="utf-8",
        )
        (meta_dir / f"patent_keywords_{ticker}.json").write_text(
            json.dumps(items, ensure_ascii=False), encoding="utf-8"
        )
        grand += len(items)
        print(f"{ticker} {name}: {len(items)} events  {[it['keyword'].split(' ',1)[1] for it in items]}")

    print(f"\n[out] {grand} event-keywords across {len(data)} tickers -> {kw_dir}/ + {meta_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
