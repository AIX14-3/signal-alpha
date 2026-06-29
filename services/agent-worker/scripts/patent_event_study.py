"""Event study: cumulative abnormal return (CAR) around patent PUBLICATION dates.

A different lens than the bake-off. Instead of asking "do patent features rank
next quarter's winners?", it asks the cleaner micro question: **when a patent
becomes public, does the stock move abnormally around that day?** If patents
carry directional information the market reacts to, CAR should drift (or jump)
near day 0; a flat CAR through 0 says publication itself is a non-event.

- Abnormal return AR_t = stock_return_t - benchmark_return_t (market model w/ beta=1).
- Event = a (stock, publication_date) with >=1 patent made public that day.
- CAR_t = mean over events of the cumulative AR from -W to t (trading days).
- Look-ahead safe by construction: publication_date is the day info became public.

Buckets (optional): by burst size (#patents published that day) and, for enriched
stocks, by max LLM significance. Reuses the FDR price CSV (ohlcv_data untouched).

    uv run python scripts/patent_event_study.py --prices-csv prices34.csv \
        --benchmark KS11 --window 20 --tickers <comma>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
from collections import defaultdict
from datetime import date

import psycopg2


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env = open(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env")).read()
    m = re.search(r"DATABASE_URL=([^\n\r]+)", env)
    if not m:
        raise SystemExit("DATABASE_URL not found (env or repo-root .env)")
    return m.group(1).strip().strip('"').strip("'")


def _load_prices(path: str) -> dict[str, list[tuple[date, float]]]:
    import csv

    out: dict[str, list[tuple[date, float]]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                d = date.fromisoformat(str(row["date"])[:10])
                c = float(row["close"])
            except (ValueError, KeyError, TypeError):
                continue
            out[(row.get("ticker") or "").strip()].append((d, c))
    return {t: sorted(set(ps)) for t, ps in out.items()}


def _returns(series: list[tuple[date, float]]) -> tuple[list[date], dict[date, float]]:
    """Daily simple returns keyed by the day they are realized; plus the date axis."""
    dates = [d for d, _ in series]
    ret: dict[date, float] = {}
    for i in range(1, len(series)):
        prev, cur = series[i - 1][1], series[i][1]
        if prev:
            ret[series[i][0]] = cur / prev - 1.0
    return dates, ret


def _fetch_events(url: str, tickers: list[str]) -> dict[str, list[tuple[date, int, float | None]]]:
    """Per ticker: list of (publication_date, n_patents_that_day, max_significance)."""
    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.ticker, pd.extra_payload->>'publication_date' AS pub, pd.llm_features, pd.llm_status
        FROM patent_raw_details pd
        JOIN raw_documents r ON r.id = pd.raw_document_id
        JOIN stocks s ON s.id = r.stock_id
        WHERE r.source_name = 'GOOGLE_PATENTS' AND s.ticker = ANY(%s)
        """,
        (tickers,),
    )
    by_day: dict[tuple[str, date], list[float | None]] = defaultdict(list)
    for ticker, pub, feats, status in cur.fetchall():
        if not pub or len(str(pub)) != 8 or not str(pub).isdigit():
            continue
        try:
            d = date(int(pub[:4]), int(pub[4:6]), int(pub[6:8]))
        except ValueError:
            continue
        sig = None
        if status == "success" and feats:
            f = feats if isinstance(feats, dict) else json.loads(feats)
            v = f.get("significance")
            try:
                sig = float(v) if v is not None else None
            except (TypeError, ValueError):
                sig = None
        by_day[(ticker, d)].append(sig)
    cur.close()
    conn.close()
    out: dict[str, list[tuple[date, int, float | None]]] = defaultdict(list)
    for (ticker, d), sigs in by_day.items():
        present = [s for s in sigs if s is not None]
        out[ticker].append((d, len(sigs), max(present) if present else None))
    return out


def _car_for_event(
    event_day: date, axis: list[date], ar: dict[date, float], window: int
) -> list[float] | None:
    """Abnormal-return path over [-window, +window] trading days around event_day.

    event_day is mapped to the first trading day on/after it; needs full window on
    both sides or the event is skipped (no partial-window bias).
    """
    pos = next((i for i, d in enumerate(axis) if d >= event_day), None)
    if pos is None or pos - window < 0 or pos + window >= len(axis):
        return None
    return [ar.get(axis[pos + k], 0.0) for k in range(-window, window + 1)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Patent publication-date event study (CAR)")
    ap.add_argument("--prices-csv", required=True)
    ap.add_argument("--benchmark", default="KS11")
    ap.add_argument("--tickers", required=True, help="comma-separated")
    ap.add_argument("--window", type=int, default=20, help="trading days each side of event")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2023-12-31")
    args = ap.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    lo, hi = date.fromisoformat(args.start), date.fromisoformat(args.end)
    prices = _load_prices(args.prices_csv)
    if args.benchmark not in prices:
        raise SystemExit(f"benchmark {args.benchmark} not in {args.prices_csv}")
    _, bench_ret = _returns(prices[args.benchmark])

    ar_by_ticker: dict[str, tuple[list[date], dict[date, float]]] = {}
    for t in tickers:
        if t not in prices:
            continue
        axis, ret = _returns(prices[t])
        abn = {d: r - bench_ret.get(d, 0.0) for d, r in ret.items()}
        ar_by_ticker[t] = (axis, abn)

    events = _fetch_events(_database_url(), tickers)
    W = args.window
    # collect AR paths, overall and by bucket
    paths: list[list[float]] = []
    paths_burst: dict[str, list[list[float]]] = {"single": [], "burst(>=5)": []}
    paths_sig: dict[str, list[list[float]]] = {"sig_hi": [], "sig_lo": []}
    sig_vals = [s for evs in events.values() for (_, _, s) in evs if s is not None]
    sig_med = statistics.median(sig_vals) if sig_vals else None
    for t, evs in events.items():
        if t not in ar_by_ticker:
            continue
        axis, abn = ar_by_ticker[t]
        for d, n, sig in evs:
            if not (lo <= d <= hi):
                continue
            path = _car_for_event(d, axis, abn, W)
            if path is None:
                continue
            paths.append(path)
            paths_burst["burst(>=5)" if n >= 5 else "single"].append(path)
            if sig is not None and sig_med is not None:
                paths_sig["sig_hi" if sig >= sig_med else "sig_lo"].append(path)

    def _summ(name: str, ps: list[list[float]]) -> None:
        if not ps:
            print(f"  {name:>12}: (no events)")
            return
        m = len(ps)
        mean_ar = [statistics.mean(col) for col in zip(*ps)]
        car = []
        run = 0.0
        for a in mean_ar:
            run += a
            car.append(run)
        # CAR over the post-event window [0, +W] as the headline drift
        car0 = car[W]  # cumulative AR up to event day (t=0)
        carW = car[-1]
        post = carW - car0
        # crude t-stat on the post-event CAR across events
        post_per_event = [sum(p[W:]) for p in ps]
        sd = statistics.pstdev(post_per_event) or 1e-9
        tstat = (statistics.mean(post_per_event)) / (sd / (m ** 0.5))
        # Nowcasting lens (magnitude, not direction): post-event abnormal-return
        # volatility vs pre-event. >1 means publication precedes a vol pickup.
        ratios = []
        for p in ps:
            pre = statistics.pstdev(p[:W]) if W >= 2 else 0.0
            post_v = statistics.pstdev(p[W + 1:]) if W >= 2 else 0.0
            if pre > 1e-9:
                ratios.append(post_v / pre)
        vol_ratio = statistics.median(ratios) if ratios else float("nan")
        print(
            f"  {name:>12}: n={m:>5}  CAR[-W..0]={car0:+.4f}  CAR[0..+W]={post:+.4f}  "
            f"CAR[full]={carW:+.4f}  post t={tstat:+.2f}  vol_post/pre={vol_ratio:.3f}"
        )

    print(
        f"\n[event-study] window=±{W} td  benchmark={args.benchmark}  "
        f"events_used={len(paths)}  sig_median={sig_med}\n"
    )
    print("CAR (cumulative abnormal return, AR=stock-benchmark):")
    _summ("ALL", paths)
    _summ("single", paths_burst["single"])
    _summ("burst(>=5)", paths_burst["burst(>=5)"])
    _summ("sig_hi", paths_sig["sig_hi"])
    _summ("sig_lo", paths_sig["sig_lo"])
    print(
        "\nRead: a directional publication effect would show CAR[0..+W] clearly >0 "
        "(or <0) with |t|>~2; ~0 means publication is a non-event for direction."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
