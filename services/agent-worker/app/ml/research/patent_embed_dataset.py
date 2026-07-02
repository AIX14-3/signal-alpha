"""Patent (title+abstract) EMBEDDING features, with identity-leak defenses.

Motivation (see the volatility/magnitude + patent-embedding memos): a naive
patent embedding mostly re-encodes a firm's *static tech-area identity* ("this is
a semiconductor company"). That correlates with size/sector and lets a model
re-learn stock identity instead of any new signal — the "static-characteristic
collapse" trap. This module builds embedding features but wraps them in four
defenses so what survives is CONTENT CHANGE, not identity:

  1. PIT gate (look-ahead 0) — reuses ``patent_dataset._window_rows``, i.e. windows
     by *publication_date* exactly as the directional harness does. No new leakage
     surface.
  2. within-firm demean — subtract each firm's mean embedding, learned on the TRAIN
     fold ONLY (:func:`fit_within_firm_demean` / :func:`apply_demean`). Removes the
     firm's baseline tech identity; what's left is "unusual FOR THIS FIRM".
  3. delta features — cosine move (and vector delta norm) from the previous window's
     pooled embedding → captures a CONTENT PIVOT, identity-free by construction.
  4. fold-fit PCA — fit on TRAIN rows only, transform test (:class:`FoldPCA`). No
     test statistics leak into the projection.

The embedder is an INJECTED interface (:class:`Embedder`) — this module never
hardcodes a network call. Unit tests pass a deterministic fake; the pilot wires
main's ``GeminiEmbeddingClient`` (async ``embed_batch``) via
:func:`embed_texts_sync`.

Feature naming follows ``features.py``: every column is prefixed ``patent_emb__``
so numeric indicators and embeddings concat without collision. Comparison arms:
``build_feature_set`` with ``mode`` in {"numeric", "embed", "concat"}.

Pure Python + numpy (sklearn only for PCA). Deterministic; no DB, no network.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import numpy as np

from .datalab_dataset import Dataset, PriceSeries, _as_date
from .patent_dataset import _window_rows

_EMB_PREFIX = "patent_emb"


# --------------------------------------------------------------------------- #
# Embedder interface (injected — never a hardcoded network call here)
# --------------------------------------------------------------------------- #
class Embedder(Protocol):
    """Minimal contract: map a batch of texts to fixed-length vectors.

    The real implementation is main's ``GeminiEmbeddingClient`` (async). Tests
    supply a deterministic fake. Callers that hold an async client should use
    :func:`embed_texts_sync` to bridge into this sync dataset builder.
    """

    def embed_texts(self, texts: list[str]) -> np.ndarray:  # (n_texts, dim)
        ...


def embed_texts_sync(async_client, texts: list[str]) -> np.ndarray:
    """Adapt main's async ``embed_batch`` into a sync ``(n, dim)`` array.

    Kept out of the hot path and dependency-light so the pilot can wire the real
    ``GeminiEmbeddingClient`` without this module importing it. Runs the coroutine
    on a fresh event loop; raises if called inside a running loop (use the async
    client directly there).
    """
    if not texts:
        return np.empty((0, 0), dtype=float)
    vecs = asyncio.run(async_client.embed_batch(texts))
    return np.asarray(vecs, dtype=float)


def _patent_text(row: dict) -> str:
    """Concatenate a patent's title + abstract into one embeddable string."""
    title = (row.get("title") or "").strip()
    abstract = (row.get("abstract") or "").strip()
    return (title + "\n" + abstract).strip()


def pooled_embedding(
    window_rows: list[dict], embedder: Embedder, dim_hint: int | None = None
) -> np.ndarray | None:
    """Mean-pool the embeddings of every patent text in a window.

    Returns the average vector, or ``None`` when the window has no non-empty text
    (so the caller can drop the observation rather than emit an all-zero row).
    """
    texts = [t for t in (_patent_text(r) for r in window_rows) if t]
    if not texts:
        return None
    mat = embedder.embed_texts(texts)
    mat = np.asarray(mat, dtype=float)
    if mat.size == 0:
        return None
    return mat.mean(axis=0)


def _cosine_delta(cur: np.ndarray, prev: np.ndarray | None) -> tuple[float, float]:
    """(cosine_move, delta_norm) between the current and previous pooled vectors.

    ``cosine_move`` = 1 - cosine_similarity ∈ [0, 2] (0 = identical content
    direction, larger = a bigger pivot). ``delta_norm`` = L2 norm of the raw
    difference. Both are 0.0 when there is no previous window (first observation).
    """
    if prev is None:
        return 0.0, 0.0
    nc, npv = np.linalg.norm(cur), np.linalg.norm(prev)
    if nc == 0 or npv == 0:
        return 0.0, float(np.linalg.norm(cur - prev))
    cos_sim = float(np.dot(cur, prev) / (nc * npv))
    return 1.0 - cos_sim, float(np.linalg.norm(cur - prev))


@dataclass
class EmbedObservations:
    """Per-(stock, as_of) pooled embeddings + delta features, pre-fold.

    Held un-demeaned / un-PCA'd on purpose: within-firm demean and PCA must be fit
    on the train fold only, so the leakage-safe transforms are applied downstream
    (per fold), not baked in here.
    """

    embeddings: np.ndarray  # (n, dim) pooled, PIT-gated
    delta: np.ndarray  # (n, 2) [cosine_move, delta_norm]
    stock_ids: np.ndarray  # (n,)
    dates: np.ndarray  # (n,) ordinal days
    dropped: Counter = field(default_factory=Counter)

    def __len__(self) -> int:
        return len(self.stock_ids)


def build_embed_observations(
    *,
    patent_rows_by_stock: dict[int, list[dict]],
    signal_dates_by_stock: dict[int, list[date]],
    embedder: Embedder,
    lookback_days: int = 60,
) -> EmbedObservations:
    """Pool PIT-gated patent text per (stock, as_of) and compute delta features.

    Windowing reuses ``patent_dataset._window_rows`` (publication_date gate → same
    look-ahead 0 guarantee as the directional harness). For each stock the delta is
    computed against that stock's PREVIOUS non-empty window in chronological order,
    so it measures content movement over time. Observations whose window has no
    embeddable text are dropped and counted.
    """
    emb_rows: list[np.ndarray] = []
    delta_rows: list[list[float]] = []
    stock_ids: list[int] = []
    dates: list[int] = []
    dropped: Counter = Counter()

    for stock_id, signal_dates in signal_dates_by_stock.items():
        rows = patent_rows_by_stock.get(stock_id, [])
        prev_vec: np.ndarray | None = None
        for as_of in sorted(set(signal_dates)):
            window = _window_rows(rows, as_of=as_of, lookback_days=lookback_days)
            vec = pooled_embedding(window, embedder)
            if vec is None:
                dropped["no_patent_text"] += 1
                continue
            cos_move, dnorm = _cosine_delta(vec, prev_vec)
            emb_rows.append(vec)
            delta_rows.append([cos_move, dnorm])
            stock_ids.append(stock_id)
            dates.append(as_of.toordinal())
            prev_vec = vec

    if emb_rows:
        embeddings = np.vstack(emb_rows)
    else:
        embeddings = np.empty((0, 0), dtype=float)
    return EmbedObservations(
        embeddings=embeddings,
        delta=np.array(delta_rows, dtype=float).reshape(len(delta_rows), 2),
        stock_ids=np.array(stock_ids, dtype=int),
        dates=np.array(dates, dtype=int),
        dropped=dropped,
    )


# --------------------------------------------------------------------------- #
# Defense 2: within-firm demean (fit on train fold only)
# --------------------------------------------------------------------------- #
def fit_within_firm_demean(
    embeddings: np.ndarray, stock_ids: np.ndarray, fit_mask: np.ndarray
) -> tuple[dict[int, np.ndarray], np.ndarray]:
    """Learn each firm's MEAN embedding from ``fit_mask`` rows only (train fold).

    Returns ``(per_firm_means, global_mean)``. Only rows selected by ``fit_mask``
    contribute, so no test-fold embedding leaks into the centering statistic. Firms
    absent from the fit set fall back to ``global_mean`` at apply time.
    """
    fit_mask = np.asarray(fit_mask, dtype=bool)
    fit_emb = embeddings[fit_mask]
    fit_ids = stock_ids[fit_mask]
    if fit_emb.size == 0:
        dim = embeddings.shape[1] if embeddings.ndim == 2 else 0
        return {}, np.zeros(dim, dtype=float)
    global_mean = fit_emb.mean(axis=0)
    means: dict[int, np.ndarray] = {}
    for sid in np.unique(fit_ids):
        means[int(sid)] = fit_emb[fit_ids == sid].mean(axis=0)
    return means, global_mean


def apply_demean(
    embeddings: np.ndarray,
    stock_ids: np.ndarray,
    means: dict[int, np.ndarray],
    global_mean: np.ndarray,
) -> np.ndarray:
    """Subtract each row's firm mean (or the global mean if the firm is unseen).

    The identity guard: after this, a firm whose patents never change sits at ~0,
    and only DEVIATIONS from its own baseline tech content remain.
    """
    out = np.empty_like(embeddings, dtype=float)
    for i, sid in enumerate(stock_ids):
        out[i] = embeddings[i] - means.get(int(sid), global_mean)
    return out


# --------------------------------------------------------------------------- #
# Defense 4: fold-fit PCA (fit on train, transform test — no leakage)
# --------------------------------------------------------------------------- #
@dataclass
class FoldPCA:
    """Thin PCA wrapper that must be fit on TRAIN rows and applied to TEST rows.

    Keeping fit/transform explicit (vs a single fit_transform over all rows) is the
    leakage guard: the projection basis is derived only from training embeddings.
    """

    n_components: int = 16
    _pca: object | None = None

    def fit(self, X_train: np.ndarray) -> "FoldPCA":
        from sklearn.decomposition import PCA

        n = min(self.n_components, X_train.shape[0], X_train.shape[1])
        n = max(1, n)
        self._pca = PCA(n_components=n, random_state=0).fit(X_train)
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        if self._pca is None:
            raise RuntimeError("FoldPCA.transform called before fit")
        return self._pca.transform(X)


# --------------------------------------------------------------------------- #
# Feature assembly + comparison arms
# --------------------------------------------------------------------------- #
def _emb_names(dim: int) -> list[str]:
    return [f"{_EMB_PREFIX}__dim_{i}" for i in range(dim)]


_DELTA_NAMES = [f"{_EMB_PREFIX}__delta_cosine", f"{_EMB_PREFIX}__delta_norm"]


def build_feature_set(
    *,
    mode: str,
    numeric_X: np.ndarray | None = None,
    numeric_names: list[str] | None = None,
    embed_X: np.ndarray | None = None,
    embed_names: list[str] | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Assemble one comparison arm's feature matrix + names.

    ``mode``:
      - ``"numeric"`` — the shipping patent indicators only (control A).
      - ``"embed"``   — embedding-derived features only (arm B).
      - ``"concat"``  — numeric ⊕ embedding (arm C).

    All embedding columns already carry the ``patent_emb__`` prefix, so a concat
    never collides with the ``patent__`` numeric columns.
    """
    if mode == "numeric":
        if numeric_X is None or numeric_names is None:
            raise ValueError("mode='numeric' needs numeric_X and numeric_names")
        return numeric_X, list(numeric_names)
    if mode == "embed":
        if embed_X is None or embed_names is None:
            raise ValueError("mode='embed' needs embed_X and embed_names")
        return embed_X, list(embed_names)
    if mode == "concat":
        if (
            numeric_X is None
            or numeric_names is None
            or embed_X is None
            or embed_names is None
        ):
            raise ValueError("mode='concat' needs both numeric and embed inputs")
        if numeric_X.shape[0] != embed_X.shape[0]:
            raise ValueError("row counts differ; align on (stock, as_of) first")
        X = np.hstack([numeric_X, embed_X])
        return X, list(numeric_names) + list(embed_names)
    raise ValueError(f"unknown mode: {mode!r}")


def embed_feature_names(dim: int, *, include_delta: bool = True) -> list[str]:
    """Column names for the embedding arm: ``patent_emb__dim_*`` (+ delta cols)."""
    names = _emb_names(dim)
    if include_delta:
        names = names + list(_DELTA_NAMES)
    return names


def stack_embed_features(
    projected: np.ndarray, delta: np.ndarray | None
) -> np.ndarray:
    """Concat PCA-projected embedding dims with the (optional) 2 delta columns."""
    if delta is None:
        return projected
    return np.hstack([projected, delta])


# --------------------------------------------------------------------------- #
# Identity CONTROL group (should score ~0 on a within-firm target)
# --------------------------------------------------------------------------- #
def build_identity_control(
    stock_ids: np.ndarray,
    *,
    firm_mean_embeddings: dict[int, np.ndarray] | None = None,
) -> tuple[np.ndarray, list[str]]:
    """A pure-IDENTITY feature set: firm dummies (and optionally the firm mean vec).

    This is the falsification control. A model trained on ONLY identity information
    must be unable to predict a within-firm-demeaned target (its firm-constant
    inputs carry no within-firm variation) → IC ≈ 0. If the demeaned CONTENT
    features beat this control, the signal is genuinely content, not identity. If
    the control also "predicts", the evaluation itself is leaking identity and the
    result is void.

    Returns one-hot firm dummies, optionally concatenated with each row's static
    firm-mean embedding (still identity-only: constant within a firm).
    """
    uniq = sorted({int(s) for s in stock_ids})
    col_of = {sid: j for j, sid in enumerate(uniq)}
    dummies = np.zeros((len(stock_ids), len(uniq)), dtype=float)
    for i, sid in enumerate(stock_ids):
        dummies[i, col_of[int(sid)]] = 1.0
    names = [f"{_EMB_PREFIX}__firm_{sid}" for sid in uniq]
    if firm_mean_embeddings:
        dim = len(next(iter(firm_mean_embeddings.values())))
        mean_block = np.array(
            [firm_mean_embeddings.get(int(s), np.zeros(dim)) for s in stock_ids],
            dtype=float,
        )
        dummies = np.hstack([dummies, mean_block])
        names = names + [f"{_EMB_PREFIX}__firm_mean_{i}" for i in range(dim)]
    return dummies, names


__all__ = [
    "Embedder",
    "embed_texts_sync",
    "pooled_embedding",
    "EmbedObservations",
    "build_embed_observations",
    "fit_within_firm_demean",
    "apply_demean",
    "FoldPCA",
    "build_feature_set",
    "embed_feature_names",
    "stack_embed_features",
    "build_identity_control",
    "Dataset",
    "PriceSeries",
    "_as_date",
]
