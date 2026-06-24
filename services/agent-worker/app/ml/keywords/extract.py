"""Period-trending keyword extraction — frequency surge, no LLM, no hindsight.

For each time bucket (default half-year), find the terms whose share of that
period's text SURGED versus all STRICTLY-PRIOR periods. Those are "what was
trending then". The prior comparison only ever looks backward, so a keyword
marked available at period P is point-in-time-honest for predictions made after P.

Korean has no morphological analyzer installed, so tokenization is a pragmatic
regex over Hangul/alnum runs + adjacent-token bigrams (for compound tech terms),
with generic patent-boilerplate words filtered out. (Swappable for sklearn
TfidfVectorizer later if weighting needs tuning.)
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from .sources import TextRecord

_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")

# Generic patent-title words that carry no company/tech signal.
_STOPWORDS = frozenset(
    {
        "방법", "장치", "시스템", "제조", "제어", "동작", "구조", "소자", "처리", "제공",
        "이용", "포함", "구성", "기반", "위한", "이를", "또는", "따른", "갖는", "구비",
        "모듈", "유닛", "그리고", "관한", "대한", "수행", "형성", "사용", "방식", "기술",
        "기록", "매체", "프로그램", "단말", "단말기", "전자", "기록매체", "제어방법",
        "제조방법", "그것", "위치", "수단", "기능",
        # high-frequency inflected boilerplate seen in real patent titles
        "포함하는", "포함한", "그것의", "그의", "그들의", "그를", "그에", "이의",
        "방법과", "동작방법", "시스템에서", "장치의", "이용한", "이용하는", "제공하는",
        "사용하여", "의한", "위치한", "수행하는", "수행하기", "구비한", "구비하는",
    }
)


def period_key(d: date, *, months: int) -> str:
    """Bucket label: months=6 -> '2017H1', months=3 -> '2017Q2', months=12 -> '2017'."""
    if months == 12:
        return f"{d.year}"
    if months == 6:
        return f"{d.year}H{1 if d.month <= 6 else 2}"
    if months == 3:
        return f"{d.year}Q{(d.month - 1) // 3 + 1}"
    raise ValueError("months must be 3, 6, or 12")


def _terms(text: str) -> list[str]:
    """Unigrams + bigrams of non-stopword tokens (len>=2). Bigrams skip stopwords."""
    toks = [t for t in _TOKEN_RE.findall(text) if len(t) >= 2 and t not in _STOPWORDS]
    terms = list(toks)
    for a, b in zip(toks, toks[1:]):
        if a != b:  # skip degenerate repeats (e.g. "컴퓨터 컴퓨터")
            terms.append(f"{a} {b}")
    return terms


@dataclass(frozen=True)
class PeriodKeyword:
    ticker: str
    period: str
    keyword: str
    first_avail_date: str  # ISO; earliest publication date the term appears this period
    count: int             # documents (titles) in the period containing the term
    score: float           # surge lift vs all prior periods (higher = more emergent)


def extract_period_keywords(
    records: Iterable[TextRecord],
    *,
    months: int = 6,
    top_k: int = 15,
    min_count: int = 3,
) -> list[PeriodKeyword]:
    """Per period, the top-``top_k`` terms (count>=``min_count``) by surge score.

    Surge score = (term's share this period / its share in all prior periods)
    * log1p(count). First period has no prior, so its frequent terms all rank by
    frequency (everything is "new" at t0) — expected and harmless downstream.
    """
    recs = list(records)
    ticker = recs[0].ticker if recs else ""

    by_period: dict[str, list[TextRecord]] = defaultdict(list)
    for r in recs:
        by_period[period_key(r.avail_date, months=months)].append(r)
    periods = sorted(by_period)

    # Per period: document frequency of each term + earliest date it appears.
    df_by_period: dict[str, Counter] = {}
    first_seen: dict[tuple[str, str], date] = {}
    for p in periods:
        df: Counter = Counter()
        for r in by_period[p]:
            for term in set(_terms(r.text)):  # document frequency (per-title presence)
                df[term] += 1
                key = (p, term)
                if key not in first_seen or r.avail_date < first_seen[key]:
                    first_seen[key] = r.avail_date
        df_by_period[p] = df

    out: list[PeriodKeyword] = []
    prior: Counter = Counter()
    prior_total = 0
    for p in periods:
        cur = df_by_period[p]
        cur_total = sum(cur.values()) or 1
        scored: list[tuple[float, str, int]] = []
        for term, cnt in cur.items():
            if cnt < min_count:
                continue
            share_cur = cnt / cur_total
            share_prior = prior[term] / prior_total if prior_total else 0.0
            lift = (share_cur + 1e-9) / (share_prior + 1e-9)
            scored.append((lift * math.log1p(cnt), term, cnt))
        scored.sort(reverse=True)
        for score, term, cnt in scored[:top_k]:
            out.append(
                PeriodKeyword(
                    ticker=ticker,
                    period=p,
                    keyword=term,
                    first_avail_date=first_seen[(p, term)].isoformat(),
                    count=cnt,
                    score=round(score, 3),
                )
            )
        # Roll prior forward AFTER scoring p, so p is never in its own baseline.
        prior.update(cur)
        prior_total += cur_total
    return out
