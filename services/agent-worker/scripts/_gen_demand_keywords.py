"""Generate kw_demand/ keyword files (one per ticker) for the demand-keyword
direction test. Evergreen consumer-demand keywords curated as-of-2016 flagship
products (no anachronistic product names). Output format matches the DataLab
collector's --kw-dir contract: patent_keywords_<ticker>.json = [{"keyword": ...}].
"""

import json
from pathlib import Path

DEMAND = {
    "005930": ["갤럭시", "삼성 TV", "삼성 노트북", "비스포크"],
    "066570": ["LG그램", "디오스", "트롬", "스타일러", "휘센"],
    "005380": ["그랜저", "아반떼", "쏘나타", "싼타페"],
    "000270": ["쏘렌토", "스포티지", "카니발", "K5", "모닝"],
    "035720": ["카카오톡", "카카오페이", "카카오뱅크", "카카오택시"],
    "036570": ["리니지", "리니지M", "블레이드앤소울", "아이온"],
    "259960": ["배틀그라운드", "배그", "펍지"],
    "352820": ["방탄소년단", "BTS", "세븐틴"],
    "097950": ["햇반", "비비고", "스팸", "백설", "다시다"],
    "004370": ["신라면", "짜파게티", "너구리", "새우깡", "안성탕면"],
    "003230": ["삼양라면", "불닭볶음면", "짜짜로니"],
    "090430": ["설화수", "라네즈", "헤라", "이니스프리", "마몽드"],
    "282330": ["CU", "CU편의점", "CU택배"],
    "007070": ["GS25", "GS슈퍼마켓", "GS프레시"],
    "033780": ["에쎄", "레종", "릴"],
    "271560": ["초코파이", "포카칩", "오징어땅콩", "꼬북칩"],
    "023530": ["롯데마트", "롯데백화점", "롯데온"],
    "139480": ["이마트", "노브랜드", "트레이더스", "피코크"],
    "051900": ["더후", "숨37", "페리오", "엘라스틴"],
    "000100": ["유한락스", "안티푸라민", "삐콤씨"],
    "003490": ["대한항공", "스카이패스"],
}

outdir = Path("kw_demand")
outdir.mkdir(exist_ok=True)
total = 0
for ticker, kws in DEMAND.items():
    rows = [{"keyword": k} for k in kws]
    (outdir / f"patent_keywords_{ticker}.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8"
    )
    total += len(kws)
print(f"wrote {len(DEMAND)} tickers, {total} keywords to {outdir}/")
