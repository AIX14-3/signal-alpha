#!/usr/bin/env python
"""Collect DataLab search series for a FLAT list of terms (not per-ticker patent keywords).

Reuses the proven NaverDataLabClient. Each term is one weekly/daily call over the full
range (consistent cross-year normalization). Resumable via a .done sidecar. Output is the
standard ticker,keyword,period,ratio CSV (load_keyword_series-compatible); `ticker` is set
to --group so downstream loaders treat all terms as one pseudo-universe.

Used for: FEARS macro concern terms (--group MACRO) and signed per-stock query pairs.

    DATABASE not needed. Requires NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (.env).
    python scripts/collect_datalab_terms.py --group MACRO --time-unit date \
        --terms "경기침체,실업,파산,공매도,폭락" --out fears_terms.csv
    python scripts/collect_datalab_terms.py --terms-file signed_queries.txt --out signed_kw.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_terms(args) -> list[tuple[str, str]]:
    """Return [(group, keyword)]. --terms uses --group; --terms-file lines are 'group,keyword' or 'keyword'."""
    out: list[tuple[str, str]] = []
    if args.terms:
        out += [(args.group, t.strip()) for t in args.terms.split(",") if t.strip()]
    if args.terms_file:
        for ln in Path(args.terms_file).read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            if "," in ln:
                g, kw = ln.split(",", 1)
                out.append((g.strip(), kw.strip()))
            else:
                out.append((args.group, ln))
    # dedup preserve order
    seen, uniq = set(), []
    for g, kw in out:
        if (g, kw) not in seen:
            seen.add((g, kw))
            uniq.append((g, kw))
    return uniq


async def _run(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Collect DataLab series for a flat term list (local)")
    p.add_argument("--terms", default="", help="comma-separated keywords (uses --group as ticker)")
    p.add_argument("--terms-file", default="", help="file: one 'group,keyword' or 'keyword' per line")
    p.add_argument("--group", default="TERM", help="pseudo-ticker label for --terms")
    p.add_argument("--start", default="2016-01-01")
    p.add_argument("--end", default=None, help="default = yesterday")
    p.add_argument("--time-unit", default="date", choices=["date", "week", "month"])
    p.add_argument("--out", default="datalab_terms.csv")
    p.add_argument("--min-nonzero", type=int, default=4)
    args = p.parse_args(argv)
    if not args.end:
        from datetime import date, timedelta
        args.end = (date.today() - timedelta(days=1)).isoformat()

    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass
    # also try repo-root .env explicitly
    root_env = Path("C:/Users/804/Documents/GitHub/signal-alpha/.env")
    if root_env.exists() and not os.environ.get("NAVER_CLIENT_ID"):
        try:
            from dotenv import dotenv_values
            for k, v in dotenv_values(root_env).items():
                if v and not os.environ.get(k):
                    os.environ[k] = v
        except ImportError:
            pass
    cid = os.environ.get("NAVER_CLIENT_ID")
    csec = os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and csec):
        raise SystemExit("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET are required")

    from app.clients.naver_datalab_client import NaverDataLabClient, NaverDataLabError

    terms = load_terms(args)
    if not terms:
        raise SystemExit("no terms (use --terms or --terms-file)")

    out_path = Path(args.out)
    done_path = out_path.with_suffix(out_path.suffix + ".done")
    done = set(done_path.read_text(encoding="utf-8").splitlines()) if done_path.exists() else set()
    csv_exists = out_path.exists()

    client = NaverDataLabClient(client_id=cid, client_secret=csec)
    csv_fh = open(out_path, "a", newline="", encoding="utf-8")
    writer = csv.writer(csv_fh)
    if not csv_exists:
        writer.writerow(["ticker", "keyword", "period", "ratio"])
    done_fh = open(done_path, "a", encoding="utf-8")

    valid = dropped = failed = skipped = 0
    try:
        for group, kw in terms:
            tag = f"{group}|{kw}"
            if tag in done:
                skipped += 1
                continue
            try:
                recs = await client.search(keyword_group=group, keywords=[kw],
                                           start_date=args.start, end_date=args.end,
                                           time_unit=args.time_unit)
            except NaverDataLabError as exc:
                print(f"  ! {group} {kw!r}: {exc}")
                failed += 1
                continue
            nonzero = [r for r in recs if r.ratio > 0]
            if len(nonzero) >= args.min_nonzero:
                writer.writerows((group, kw, r.period, r.ratio) for r in recs)
                valid += 1
                print(f"  +{group} {kw!r}: {len(nonzero)} nonzero pts")
            else:
                dropped += 1
                print(f"  -{group} {kw!r}: no data ({len(nonzero)} nonzero)")
            done_fh.write(tag + "\n")
            done_fh.flush()
            done.add(tag)
    finally:
        csv_fh.close()
        done_fh.close()

    print(f"\n[out] {args.out}: +{valid} with data, {dropped} no-data, {skipped} skipped, "
          f"{failed} failed (re-run to retry)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
