"""Propose a ~N-stock R&D-intensive patent-ML universe (large/mid/small) via FDR.

Read-only. Joins FDR ``KRX-DESC`` (Sector/Industry/Products) with the market-cap
listing, KEEPS ONLY R&D-intensive industries (companies that actually file patents
— semiconductors, electronics, IT, pharma/bio, auto parts, chemicals/materials,
machinery, etc.), stratifies into large/mid/small by market cap, samples a mix,
always includes the loaded anchors (005930/000660/035420), and writes a proposal
JSON for review. No DB writes, no patent load.

    uv run python scripts/select_universe_fdr.py --n 50 --out universe.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ANCHORS = ["005930", "000660", "035420"]

# R&D-intensive industry keywords (matched against Sector + Industry + Products, Korean).
RND_KEYWORDS = [
    "반도체", "전자", "전기", "디스플레이", "소프트웨어", "소프트", "정보", "통신", "인터넷",
    "제약", "의약", "바이오", "생명", "의료", "헬스", "진단", "자동차", "부품", "화학",
    "소재", "2차전지", "이차전지", "전지", "배터리", "기계", "장비", "정밀", "광학", "로봇",
    "항공", "방산", "나노", "센서", "에너지", "태양", "수소", "게임", "콘텐츠", "재료",
]


def _marcap_map() -> dict[str, float]:
    import FinanceDataReader as fdr
    try:
        df = fdr.StockListing("KRX")
        cols = {c.lower(): c for c in df.columns}
        code = cols.get("code") or cols.get("symbol")
        mc = cols.get("marcap") or cols.get("marketcap")
        if code and mc:
            return {
                str(r[code]).zfill(6): float(r[mc])
                for _, r in df.iterrows()
                if r[mc] == r[mc] and r[mc]
            }
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] marcap listing unavailable: {exc}")
    return {}


def _desc_rows():
    import FinanceDataReader as fdr
    df = fdr.StockListing("KRX-DESC")
    cols = {c.lower(): c for c in df.columns}
    code, name, market = cols.get("code"), cols.get("name"), cols.get("market")
    sector, industry, products = cols.get("sector"), cols.get("industry"), cols.get("products")
    out = []
    for _, r in df.iterrows():
        t = str(r[code]).zfill(6) if code else None
        if not t or not t.isdigit():
            continue
        out.append({
            "ticker": t,
            "name": str(r[name]) if name else "",
            "market": str(r[market]) if market else "",
            "sector": str(r[sector]) if sector else "",
            "industry": str(r[industry]) if industry else "",
            "products": str(r[products]) if products else "",
        })
    return out


def _is_rnd(r: dict) -> bool:
    blob = f"{r.get('sector','')} {r.get('industry','')} {r.get('products','')}"
    return any(k in blob for k in RND_KEYWORDS)


def _tier(rank: int, total: int) -> str:
    q = rank / max(total, 1)
    return "large" if q <= 0.15 else ("mid" if q <= 0.55 else "small")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Propose R&D-intensive patent-ML universe (FDR)")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--out", default="universe.json")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)

    rows = _desc_rows()
    caps = _marcap_map()
    for r in rows:
        r["marcap"] = caps.get(r["ticker"])
    rnd = [r for r in rows if _is_rnd(r) and r.get("marcap")]
    print(f"[fdr] {len(rows)} listed, {len(rnd)} R&D-intensive with market-cap")
    if not rnd:
        raise SystemExit("No R&D-intensive stocks with market-cap; check FDR columns.")

    rnd.sort(key=lambda r: r["marcap"], reverse=True)
    for i, r in enumerate(rnd):
        r["tier"] = _tier(i, len(rnd))
    by_ticker = {r["ticker"]: r for r in rnd}

    rng = random.Random(args.seed)
    by_tier = {"large": [], "mid": [], "small": []}
    for r in rnd:
        by_tier[r["tier"]].append(r)
    targets = {"large": int(args.n * 0.3), "mid": int(args.n * 0.4)}
    targets["small"] = args.n - targets["large"] - targets["mid"]

    chosen: dict[str, dict] = {}
    for a in ANCHORS:  # anchors first (may be from any tier)
        if a in by_ticker:
            chosen[a] = by_ticker[a]
    for tier, k in targets.items():
        pool = [r for r in by_tier[tier] if r["ticker"] not in chosen]
        rng.shuffle(pool)
        # spread across sectors
        cap = max(2, k // 6)
        per_sector: dict[str, int] = {}
        picked = 0
        for r in pool:
            if picked >= k:
                break
            s = r.get("sector") or "?"
            if per_sector.get(s, 0) >= cap:
                continue
            chosen[r["ticker"]] = r
            per_sector[s] = per_sector.get(s, 0) + 1
            picked += 1
        for r in pool:  # backfill
            if picked >= k:
                break
            if r["ticker"] not in chosen:
                chosen[r["ticker"]] = r
                picked += 1

    universe = sorted(chosen.values(), key=lambda r: -(r.get("marcap") or 0))
    summary: dict[str, int] = {}
    for r in universe:
        summary[r.get("tier", "?")] = summary.get(r.get("tier", "?"), 0) + 1

    Path(args.out).write_text(
        json.dumps(
            [{"ticker": r["ticker"], "name": r["name"], "market": r["market"],
              "sector": r.get("sector", ""), "industry": r.get("industry", ""),
              "tier": r.get("tier", ""), "marcap_won": int(r.get("marcap") or 0)}
             for r in universe],
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[universe] {len(universe)} stocks  tiers={summary}")
    for r in universe:
        print(f"  {r['ticker']} {r['name'][:14]:<14} {r.get('market',''):<6} {r.get('tier',''):<5} "
              f"{r.get('sector','')[:20]:<20}")
    print(f"[out] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
