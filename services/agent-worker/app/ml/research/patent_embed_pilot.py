"""Patent-embedding within-firm MAGNITUDE pilot (foreground, real numbers).

Reuses the tested research harness:
  - patent_embed_dataset: pooled_embedding, fit/apply within-firm demean, FoldPCA,
    build_identity_control, embed_texts_sync
  - labels_magnitude: forward_realized_vol, abnormal_volume_z, demean_within_firm
  - evaluation: embargo_folds
  - stats: benjamini_hochberg

Cost control: per-window pooling is capped to the K most-recent abstracts, texts are
truncated, and every unique text is embedded exactly once via an on-disk cache.

Verdict question: do patent TEXT-CONTENT embeddings predict a firm's *within-firm*
forward realized-vol / abnormal-volume (magnitude, not direction), beating an
identity-only control? Static-characteristic collapse is the null.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

# --- harness imports (PYTHONPATH must include services/agent-worker) ---------
from app.ml.research.datalab_dataset import PriceSeries, _as_date
from app.ml.research.patent_dataset import _window_rows
from app.ml.research.patent_embed_dataset import (
    FoldPCA,
    apply_demean,
    build_identity_control,
    fit_within_firm_demean,
    pooled_embedding,
)
from app.ml.research.labels_magnitude import (
    VolumeSeries,
    abnormal_volume_z,
    demean_within_firm,
    forward_realized_vol,
)
from app.ml.research.evaluation import embargo_folds
from app.ml.research.stats import benjamini_hochberg
from app.clients.embedding_client import GeminiEmbeddingClient

from scipy.stats import spearmanr
from sklearn.linear_model import Ridge

RNG = np.random.default_rng(0)


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
async def load_patents(dburl: str, stock_ids: list[int]) -> dict[int, list[dict]]:
    import asyncpg

    if dburl.startswith("postgresql+asyncpg"):
        dburl = dburl.replace("postgresql+asyncpg", "postgresql")
    conn = await asyncpg.connect(dburl)
    try:
        rows = await conn.fetch(
            """
            select stock_id,
                   patent_title as title,
                   extra_payload->>'abstract' as abstract,
                   extra_payload->>'publication_date' as pub
            from patent_raw_details
            where stock_id = any($1::bigint[])
              and length(coalesce(extra_payload->>'abstract','')) > 0
              and (extra_payload->>'publication_date') is not null
            """,
            stock_ids,
        )
    finally:
        await conn.close()
    out: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        pub = _parse_yyyymmdd(r["pub"])
        if pub is None:
            continue
        out[int(r["stock_id"])].append(
            {"publication_date": pub, "title": r["title"] or "", "abstract": r["abstract"] or ""}
        )
    for sid in out:
        out[sid].sort(key=lambda d: d["publication_date"])
    return out


def _parse_yyyymmdd(v):
    if v is None:
        return None
    s = str(v).strip()
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    return _as_date(s)


def load_prices(csv_path: str, ticker_by_id: dict[int, str]):
    import csv

    close_pairs: dict[str, list] = defaultdict(list)
    vol_pairs: dict[str, list] = defaultdict(list)
    want = set(ticker_by_id.values())
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            t = (row.get("ticker") or "").strip()
            if t not in want:
                continue
            try:
                d = date.fromisoformat(row["date"][:10])
                c = float(row["close"])
                v = float(row["volume"])
            except (ValueError, KeyError, TypeError):
                continue
            close_pairs[t].append((d, c))
            vol_pairs[t].append((d, v))
    prices = {sid: PriceSeries.from_pairs(close_pairs[t]) for sid, t in ticker_by_id.items()}
    vols = {sid: VolumeSeries.from_pairs(vol_pairs[t]) for sid, t in ticker_by_id.items()}
    return prices, vols


def weekly_dates(price: PriceSeries, start: date, end: date, step: int) -> list[date]:
    in_win = [d for d in price.dates if start <= d <= end]
    return in_win[::step]


# --------------------------------------------------------------------------- #
# Embedding with cache + per-window K cap
# --------------------------------------------------------------------------- #
def _text(row: dict, max_chars: int) -> str:
    t = (row.get("title") or "").strip()
    a = (row.get("abstract") or "").strip()
    return (t + "\n" + a).strip()[:max_chars]


class CachedEmbedder:
    """embed_texts() reads a pre-filled on-disk cache keyed by sha1(text)."""

    def __init__(self, cache: dict[str, list[float]], dim: int):
        self.cache = cache
        self.dim = dim

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        out = []
        for t in texts:
            v = self.cache.get(_key(t))
            if v is None:
                raise KeyError("text not pre-embedded; run pre-embed pass first")
            out.append(v)
        return np.array(out, dtype=float)


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def collect_windows(patents, signal_dates_by_stock, lookback, k_cap, max_chars):
    """Return per-(stock,as_of) the capped list of window texts, plus unique text set."""
    windows: dict[tuple[int, date], list[str]] = {}
    unique: set[str] = set()
    for sid, sdates in signal_dates_by_stock.items():
        rows = patents.get(sid, [])
        for as_of in sorted(set(sdates)):
            win = _window_rows(rows, as_of=as_of, lookback_days=lookback)
            if not win:
                continue
            # most-recent K by publication_date (cost cap)
            win = sorted(win, key=lambda r: r["publication_date"])[-k_cap:]
            texts = [t for t in (_text(r, max_chars) for r in win) if t]
            if not texts:
                continue
            windows[(sid, as_of)] = texts
            unique.update(texts)
    return windows, unique


async def _embed_all(client, missing, cache, cache_path, conc):
    """Embed each text via single embedContent, bounded concurrency (no batch API)."""
    sem = asyncio.Semaphore(conc)
    done = {"n": 0}

    async def one(t):
        async with sem:
            v = await client.embed(t)
        cache[_key(t)] = v
        done["n"] += 1
        if done["n"] % 200 == 0:
            cache_path.write_text(json.dumps(cache))
            print(f"    embedded {done['n']}/{len(missing)}", flush=True)

    await asyncio.gather(*(one(t) for t in missing))


def pre_embed(unique: list[str], cache: dict, cache_path: Path, batch: int):
    client = GeminiEmbeddingClient()  # EMBEDDING_MODEL from env
    missing = [t for t in unique if _key(t) not in cache]
    print(f"  pre-embed: model={client.model} dim={client.dim} {len(unique)} unique, "
          f"{len(missing)} missing (single embedContent calls)", flush=True)
    if missing:
        asyncio.run(_embed_all(client, missing, cache, cache_path, conc=12))
    cache_path.write_text(json.dumps(cache))
    return client.dim


# --------------------------------------------------------------------------- #
# Build observations (pooled embed + delta) reusing tested pooled_embedding
# --------------------------------------------------------------------------- #
def build_obs(windows, embedder):
    """Return arrays: emb(n,dim), delta(n,2), stock_ids(n), dates(n ordinal)."""
    by_stock: dict[int, list] = defaultdict(list)
    for (sid, as_of), texts in windows.items():
        by_stock[sid].append((as_of, texts))
    emb_rows, delta_rows, sids, dates = [], [], [], []
    for sid, items in by_stock.items():
        items.sort(key=lambda x: x[0])
        prev = None
        for as_of, texts in items:
            mat = embedder.embed_texts(texts)
            vec = mat.mean(axis=0)
            if prev is None:
                cos_move, dnorm = 0.0, 0.0
            else:
                nc, npv = np.linalg.norm(vec), np.linalg.norm(prev)
                if nc == 0 or npv == 0:
                    cos_move, dnorm = 0.0, float(np.linalg.norm(vec - prev))
                else:
                    cos_move = 1.0 - float(np.dot(vec, prev) / (nc * npv))
                    dnorm = float(np.linalg.norm(vec - prev))
            emb_rows.append(vec)
            delta_rows.append([cos_move, dnorm])
            sids.append(sid)
            dates.append(as_of.toordinal())
            prev = vec
    return (
        np.vstack(emb_rows),
        np.array(delta_rows, dtype=float),
        np.array(sids, dtype=int),
        np.array(dates, dtype=int),
    )


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #
def build_labels(sids, dates, prices, vols, horizon, baseline):
    rv, av = [], []
    for sid, dord in zip(sids, dates):
        as_of = date.fromordinal(int(dord))
        rv.append(forward_realized_vol(prices[sid], as_of, horizon))
        av.append(abnormal_volume_z(vols[sid], as_of, horizon, baseline_sessions=baseline))
    return np.array([np.nan if x is None else x for x in rv], dtype=float), np.array(
        [np.nan if x is None else x for x in av], dtype=float
    )


# --------------------------------------------------------------------------- #
# Evaluation: OOF within-firm rankIC per arm + permutation p
# --------------------------------------------------------------------------- #
def _within_firm_ic(pred, y, sids):
    """Mean per-firm Spearman(pred, y). y already within-firm demeaned."""
    ics = []
    for f in np.unique(sids):
        m = sids == f
        if m.sum() < 5 or np.std(pred[m]) == 0 or np.std(y[m]) == 0:
            continue
        ic = spearmanr(pred[m], y[m]).statistic
        if not np.isnan(ic):
            ics.append(ic)
    return float(np.mean(ics)) if ics else float("nan"), len(ics)


def oof_predictions(build_X, emb, delta, sids, dates, y_raw, folds, n_comp):
    """Return (pred, y_demeaned, sids, dates) pooled over test folds for one arm.

    build_X(train_mask) -> (X_all, ) using train-fit transforms only.
    y is within-firm demeaned with train-fit means each fold.
    """
    valid = ~np.isnan(y_raw)
    preds, ys, ps, ds = [], [], [], []
    for fold in folds:
        tr = np.intersect1d(fold.train_idx, np.where(valid)[0])
        te = np.intersect1d(fold.test_idx, np.where(valid)[0])
        if len(tr) < 20 or len(te) < 5:
            continue
        train_mask = np.zeros(len(y_raw), dtype=bool)
        train_mask[tr] = True
        X = build_X(train_mask, emb, delta, sids, n_comp)
        # within-firm demean of target: fit on train rows only
        fitm = np.zeros(len(y_raw), dtype=bool)
        fitm[tr] = True
        y_dm = demean_within_firm(np.nan_to_num(y_raw), sids, fit_mask=fitm)
        model = Ridge(alpha=1.0)
        model.fit(X[tr], y_dm[tr])
        pr = model.predict(X[te])
        preds.append(pr)
        ys.append(y_dm[te])
        ps.append(sids[te])
        ds.append(dates[te])
    if not preds:
        return None
    return (
        np.concatenate(preds),
        np.concatenate(ys),
        np.concatenate(ps),
        np.concatenate(ds),
    )


def perm_p(pred, y, sids, obs_ic, n_perm=1000):
    """Permutation p: shuffle y WITHIN firm, recompute within-firm IC."""
    count = 0
    for _ in range(n_perm):
        yp = y.copy()
        for f in np.unique(sids):
            m = np.where(sids == f)[0]
            yp[m] = y[m][RNG.permutation(len(m))]
        ic, _ = _within_firm_ic(pred, yp, sids)
        if not np.isnan(ic) and abs(ic) >= abs(obs_ic):
            count += 1
    return (count + 1) / (n_perm + 1)


# ---- arm builders (train-fit transforms) ---------------------------------- #
def build_X_embed(train_mask, emb, delta, sids, n_comp):
    means, gmean = fit_within_firm_demean(emb, sids, train_mask)
    dm = apply_demean(emb, sids, means, gmean)
    pca = FoldPCA(n_components=n_comp).fit(dm[train_mask])
    proj = pca.transform(dm)
    return np.hstack([proj, delta])


def build_X_embed_raw(train_mask, emb, delta, sids, n_comp):
    # NO within-firm demean -> identity leak arm (should look like identity if collapse)
    pca = FoldPCA(n_components=n_comp).fit(emb[train_mask])
    proj = pca.transform(emb)
    return np.hstack([proj, delta])


def build_X_identity(train_mask, emb, delta, sids, n_comp):
    # firm dummies + static firm-mean embedding block (train-fit means)
    means, _ = fit_within_firm_demean(emb, sids, train_mask)
    X, _ = build_identity_control(sids, firm_mean_embeddings=means)
    return X


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", required=True, help="stock_id:ticker,... e.g. 1:005930,2:000660")
    ap.add_argument("--prices-csv", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--lookback", type=int, default=90)
    ap.add_argument("--horizon", type=int, default=10)
    ap.add_argument("--baseline", type=int, default=20)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--k-cap", type=int, default=8)
    ap.add_argument("--max-chars", type=int, default=900)
    ap.add_argument("--n-comp", type=int, default=16)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--embargo", type=int, default=20)
    ap.add_argument("--cache", default="emb_cache.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--n-perm", type=int, default=1000)
    args = ap.parse_args()

    ticker_by_id = {}
    for tok in args.stocks.split(","):
        sid, tk = tok.split(":")
        ticker_by_id[int(sid)] = tk
    stock_ids = list(ticker_by_id)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    print(f"[pilot] stocks={ticker_by_id} period={start}..{end} lookback={args.lookback} "
          f"horizon={args.horizon} step={args.step} k_cap={args.k_cap}", flush=True)

    dburl = os.environ["DATABASE_URL"]
    patents = asyncio.run(load_patents(dburl, stock_ids))
    for sid in stock_ids:
        print(f"  stock {sid} ({ticker_by_id[sid]}): {len(patents.get(sid, []))} patents-with-abstract", flush=True)
    prices, vols = load_prices(args.prices_csv, ticker_by_id)

    signal_dates = {sid: weekly_dates(prices[sid], start, end, args.step) for sid in stock_ids}
    windows, unique = collect_windows(patents, signal_dates, args.lookback, args.k_cap, args.max_chars)
    unique = sorted(unique)
    print(f"[pilot] observations(windows)={len(windows)}  unique_texts_to_embed={len(unique)}", flush=True)

    if args.dry_run:
        print("[dry-run] stopping before API calls.", flush=True)
        return

    cache_path = Path(args.cache)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    dim = pre_embed(unique, cache, cache_path, batch=64)
    embedder = CachedEmbedder(cache, dim)

    emb, delta, sids, dates = build_obs(windows, embedder)
    print(f"[pilot] obs matrix: emb{emb.shape} delta{delta.shape} n={len(sids)} "
          f"firms={len(np.unique(sids))} dates={len(np.unique(dates))}", flush=True)

    rv, av = build_labels(sids, dates, prices, vols, args.horizon, args.baseline)
    print(f"[pilot] labels: realized_vol valid={np.sum(~np.isnan(rv))}  abn_vol valid={np.sum(~np.isnan(av))}", flush=True)

    folds = embargo_folds(dates, n_folds=args.folds, embargo=args.embargo)

    arms = {
        "embed_demeaned": build_X_embed,
        "embed_raw(identity-leak)": build_X_embed_raw,
        "identity_control": build_X_identity,
    }
    targets = {"realized_vol": rv, "abn_volume": av}

    results = []
    for tname, y in targets.items():
        for aname, builder in arms.items():
            oof = oof_predictions(builder, emb, delta, sids, dates, y, folds, args.n_comp)
            if oof is None:
                results.append((tname, aname, float("nan"), 0, float("nan"), float("nan"), 0))
                continue
            pred, y_dm, ps, ds = oof
            wf_ic, n_firms = _within_firm_ic(pred, y_dm, ps)
            pooled_ic = spearmanr(pred, y_dm).statistic if np.std(pred) > 0 else float("nan")
            pp = perm_p(pred, y_dm, ps, wf_ic, n_perm=args.n_perm) if not np.isnan(wf_ic) else float("nan")
            results.append((tname, aname, wf_ic, len(pred), pooled_ic, pp, n_firms))

    # BH-FDR across the whole family
    pvals = [r[5] for r in results]
    bh = benjamini_hochberg(pvals, alpha=0.05)

    print("\n================ RESULTS (within-firm MAGNITUDE) ================", flush=True)
    print(f"{'target':<12} {'arm':<26} {'wf_IC':>8} {'n_oof':>6} {'pooled_IC':>10} {'perm_p':>8} {'BH_q':>7} {'rej':>4} {'firms':>6}")
    for r, q, rej in zip(results, bh.qvalues, bh.rejected):
        tname, aname, wf, n, pooled, pp, nf = r
        print(f"{tname:<12} {aname:<26} {wf:>8.4f} {n:>6} {pooled:>10.4f} {pp:>8.4f} {q:>7.4f} {str(rej):>4} {nf:>6}", flush=True)
    print("=================================================================", flush=True)


if __name__ == "__main__":
    main()
