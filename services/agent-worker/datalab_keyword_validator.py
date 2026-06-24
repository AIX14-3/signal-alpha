"""Search-volume validation gate for drafted DataLab keywords.

This is part of the keyword-generator boundary (NOT a collector, NOT an LLM caller).
It READS Naver DataLab search trends to check whether a drafted keyword is actually
searched by real people, then assigns a tier:

  - "auto"   : enough active search days  -> safe to auto-apply (review_status='approved')
  - "review" : borderline                 -> hold for a human   (review_status='pending')
  - "reject" : (near) zero search activity-> likely hallucinated; drop the keyword

Why "active days" is the signal: Naver DataLab returns a data point only for periods
whose search ratio is non-zero, and a single-keyword query is normalized to its OWN
max (so absolute volume is not comparable across keywords). What IS comparable and
model-free is *how many days in the window the phrase was searched at all* — an invented
phrase nobody types returns (almost) no points, a real one returns many.

The gate is generation-agnostic: it raises reliability whether keywords came from an
LLM, an ML extractor, or a human. It writes nothing to collector tables — it only reads
search trends and annotates the in-memory draft.

Reused by the bootstrap drafter, the refresh drafter, and the daily pipeline so every
keyword-creation flow shares one validation contract.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

from app.clients.naver_datalab_client import NaverDataLabClient, NaverDataLabError

# Tier thresholds, measured as active search DAYS within the window (env-overridable).
WINDOW_DAYS = int(os.getenv("DATALAB_KW_VALIDATION_WINDOW_DAYS", "30"))
AUTO_MIN_ACTIVE_DAYS = int(os.getenv("DATALAB_KW_AUTO_MIN_ACTIVE_DAYS", "10"))
REVIEW_MIN_ACTIVE_DAYS = int(os.getenv("DATALAB_KW_REVIEW_MIN_ACTIVE_DAYS", "3"))
# Safety cap so a large batch cannot exhaust the Naver daily call quota in one run.
MAX_VALIDATION_CALLS = int(os.getenv("DATALAB_KW_VALIDATION_MAX_CALLS", "500"))

# Spike fast-track: a freshly-broken event keyword has few total active days but its
# search interest is concentrated in the last few days. The active-days gate alone is
# biased toward evergreen phrases and lags breaking events; this lets a rising keyword
# auto-approve once its recency is strong, WITHOUT an extra API call (computed from the
# same daily ratio series). All env-overridable.
SPIKE_RECENT_DAYS = int(os.getenv("DATALAB_KW_SPIKE_RECENT_DAYS", "7"))
SPIKE_MIN_RECENT_ACTIVE = int(os.getenv("DATALAB_KW_SPIKE_MIN_RECENT_ACTIVE", "2"))
SPIKE_MIN_RECENT_SHARE = float(os.getenv("DATALAB_KW_SPIKE_MIN_RECENT_SHARE", "0.6"))

TIER_AUTO = "auto"
TIER_REVIEW = "review"
TIER_REJECT = "reject"

# review_status persisted per tier (see migration 024).
TIER_TO_STATUS = {TIER_AUTO: "approved", TIER_REVIEW: "pending", TIER_REJECT: "rejected"}


def build_client() -> NaverDataLabClient:
    """Construct a DataLab client from env (NAVER_CLIENT_ID / NAVER_CLIENT_SECRET)."""
    return NaverDataLabClient(
        client_id=os.getenv("NAVER_CLIENT_ID", ""),
        client_secret=os.getenv("NAVER_CLIENT_SECRET", ""),
    )


def _date_window(window_days: int) -> tuple[str, str]:
    # End at yesterday: Naver does not have a complete same-day series.
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=max(1, window_days) - 1)
    return start.isoformat(), end.isoformat()


def tier_for_active_days(
    active_days: int,
    *,
    auto_min: int = AUTO_MIN_ACTIVE_DAYS,
    review_min: int = REVIEW_MIN_ACTIVE_DAYS,
) -> str:
    """Map an active-day count to a tier. Pure function — unit-test friendly."""
    if active_days >= auto_min:
        return TIER_AUTO
    if active_days >= review_min:
        return TIER_REVIEW
    return TIER_REJECT


def is_spike(
    recent_active_days: int,
    recent_share: float,
    *,
    min_recent_active: int = SPIKE_MIN_RECENT_ACTIVE,
    min_recent_share: float = SPIKE_MIN_RECENT_SHARE,
) -> bool:
    """True when search interest is recent AND concentrated (rising), not stale.

    ``recent_share`` is the fraction of the window's total search ratio that fell in
    the recent sub-window; a high share means a fresh/rising event rather than a long
    flat history or an old one-off blip.
    """
    return recent_active_days >= min_recent_active and recent_share >= min_recent_share


def _recent_metrics(records: list, *, end_date: str, recent_days: int) -> tuple[int, float]:
    """(recent_active_days, recent_share) from a daily ratio series — no extra API call."""
    if not records:
        return 0, 0.0
    cutoff = (date.fromisoformat(end_date) - timedelta(days=max(1, recent_days) - 1)).isoformat()
    total = sum(r.ratio for r in records)
    recent = [r for r in records if r.period >= cutoff and r.ratio > 0]
    recent_ratio = sum(r.ratio for r in recent)
    share = round(recent_ratio / total, 3) if total else 0.0
    return len(recent), share


async def validate_keyword(
    client: NaverDataLabClient,
    keyword: str,
    *,
    start_date: str,
    end_date: str,
    window_days: int,
    auto_min: int = AUTO_MIN_ACTIVE_DAYS,
    review_min: int = REVIEW_MIN_ACTIVE_DAYS,
    recent_days: int = SPIKE_RECENT_DAYS,
) -> dict[str, Any]:
    """Query one keyword's search trend and return its validation verdict.

    A Naver API failure is treated as "unknown" and routed to 'review' (never silently
    auto-approved on missing data, never dropped) so a transient outage cannot poison
    the active keyword set. A keyword below the active-days bar is still fast-tracked to
    'auto' when its search interest is a recent spike (see ``is_spike``).
    """
    try:
        records = await client.search(
            keyword_group=keyword,
            keywords=[keyword],
            start_date=start_date,
            end_date=end_date,
            time_unit="date",
        )
    except NaverDataLabError:
        return {
            "active_days": None,
            "coverage": None,
            "window_days": window_days,
            "tier": TIER_REVIEW,
            "error": True,
        }
    active_days = sum(1 for r in records if r.ratio > 0)
    coverage = round(active_days / window_days, 3) if window_days else None
    recent_active, recent_share = _recent_metrics(records, end_date=end_date, recent_days=recent_days)
    base_tier = tier_for_active_days(active_days, auto_min=auto_min, review_min=review_min)
    spiked = base_tier != TIER_AUTO and is_spike(recent_active, recent_share)
    return {
        "active_days": active_days,
        "coverage": coverage,
        "window_days": window_days,
        "recent_active_days": recent_active,
        "recent_share": recent_share,
        "spike": spiked,
        "tier": TIER_AUTO if spiked else base_tier,
        "error": False,
    }


async def validate_draft_volume(
    draft: dict[str, Any],
    client: NaverDataLabClient,
    *,
    window_days: int = WINDOW_DAYS,
    auto_min: int = AUTO_MIN_ACTIVE_DAYS,
    review_min: int = REVIEW_MIN_ACTIVE_DAYS,
    drop_rejected: bool = True,
    max_calls: int = MAX_VALIDATION_CALLS,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Annotate every keyword in ``draft`` with a ``validation`` verdict.

    Each kept keyword gains ``kw["validation"]`` = {active_days, coverage, window_days,
    tier, ...}. Reject-tier keywords are removed when ``drop_rejected`` (default), and
    any category left empty is pruned. Returns (draft, summary-counts).

    ``max_calls`` caps Naver calls per run; once hit, remaining keywords are routed to
    'review' WITHOUT an API call (capped, never auto-approved unseen).
    """
    start_date, end_date = _date_window(window_days)
    summary = {TIER_AUTO: 0, TIER_REVIEW: 0, TIER_REJECT: 0, "capped": 0, "calls": 0, "spike": 0}
    for category in draft.get("categories", []):
        kept: list[dict[str, Any]] = []
        for kw in category.get("keywords", []):
            if summary["calls"] >= max_calls:
                kw["validation"] = {
                    "active_days": None,
                    "coverage": None,
                    "window_days": window_days,
                    "tier": TIER_REVIEW,
                    "capped": True,
                }
                summary["capped"] += 1
            else:
                kw["validation"] = await validate_keyword(
                    client,
                    kw["text"],
                    start_date=start_date,
                    end_date=end_date,
                    window_days=window_days,
                    auto_min=auto_min,
                    review_min=review_min,
                )
                summary["calls"] += 1
                summary[kw["validation"]["tier"]] += 1
                if kw["validation"].get("spike"):
                    summary["spike"] += 1
            if kw["validation"]["tier"] == TIER_REJECT and drop_rejected:
                continue
            kept.append(kw)
        category["keywords"] = kept
    draft["categories"] = [c for c in draft.get("categories", []) if c.get("keywords")]
    return draft, summary
