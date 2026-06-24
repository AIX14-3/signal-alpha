"""Naver News as an event source for DataLab keyword refresh.

The keyword refresh drafter (``datalab_polarity_refresh``) turns recent *events*
into NAVER search keywords. Its original event source was DART disclosures
(``dart_raw_details``), but that table is empty in some environments and DART only
captures formal filings — people actually search in reaction to NEWS. This module
fetches recent news headlines for a stock from the Naver News Search API and maps
them to the SAME event-dict contract the refresh drafter already consumes
(``report_name`` / ``disclosure_type`` / ``priority_reason`` / ``created_at``), so
the rest of the pipeline (Gemini draft -> Naver search-volume gate -> apply) is
reused unchanged.

``enrich_events`` optionally deepens those headlines with the Gemini Google-Search
grounding tool: a headline + snippet omits the article body, so a single grounded
call per stock adds a concise factual brief (named entities, product names,
figures) as one extra event. It is best-effort — on any failure it returns the
plain news events unchanged.

Boundary unchanged: this is part of the keyword-generator pre-collection tool. It
reads the Naver News API in memory only; it does NOT write ``raw_documents`` or any
collector table.
"""
from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from keyword_gen_common import call_gemini_grounded, read_url

NEWS_ENDPOINT = "https://openapi.naver.com/v1/search/news.json"
DEFAULT_NEWS_LOOKBACK_DAYS = 14
DEFAULT_NEWS_MAX_ITEMS = 20
ENRICH_MAX_HEADLINES = 12

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str | None) -> str:
    """Strip Naver's <b> highlight tags and unescape HTML entities (&quot; etc.)."""
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


def _stock_names(stock: dict[str, Any]) -> list[str]:
    names = [stock.get(k) for k in ("name", "corp_name", "stock_name", "corp_name_eng")]
    seen: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.append(n)
    return seen


def _request_news(query: str, *, display: int) -> list[dict[str, Any]]:
    """Raw Naver News Search API call. Isolated so tests can stub it."""
    cid = os.getenv("NAVER_CLIENT_ID", "")
    secret = os.getenv("NAVER_CLIENT_SECRET", "")
    if not cid or not secret:
        raise RuntimeError("NAVER_CLIENT_ID/NAVER_CLIENT_SECRET are required for news fetch.")
    qs = urllib.parse.urlencode(
        {"query": query, "display": min(max(display, 1), 100), "sort": "date"}
    )
    req = urllib.request.Request(
        f"{NEWS_ENDPOINT}?{qs}",
        headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret},
    )
    payload = json.loads(read_url(req, timeout=30))
    return payload.get("items", [])


def _in_window(pub_date: str | None, *, cutoff: datetime) -> bool:
    if not pub_date:
        return True  # keep undated items; dedup handles repeats
    try:
        dt = parsedate_to_datetime(pub_date)
    except (TypeError, ValueError):
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= cutoff


async def fetch_recent_news(
    stock: dict[str, Any], *, days: int | None = None, max_items: int | None = None
) -> list[dict[str, Any]]:
    """Recent news for a stock, mapped to the refresh drafter's event-dict contract.

    Returns dicts shaped like DART disclosures so ``build_prompt`` / ``validate_draft``
    consume them unchanged: ``report_name`` (headline), ``disclosure_type='NEWS'``,
    ``priority_reason`` (snippet), ``created_at`` (publish time).
    """
    if days is None:
        days = int(os.getenv("NEWS_LOOKBACK_DAYS", DEFAULT_NEWS_LOOKBACK_DAYS))
    if max_items is None:
        max_items = int(os.getenv("NEWS_MAX_ITEMS", DEFAULT_NEWS_MAX_ITEMS))

    names = _stock_names(stock)
    query = names[0] if names else stock.get("ticker", "")
    if not query:
        return []

    items = _request_news(query, display=max_items)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not _in_window(item.get("pubDate"), cutoff=cutoff):
            continue
        title = _clean(item.get("title"))
        if not title or title in seen:
            continue
        seen.add(title)
        events.append(
            {
                "report_name": title,
                "disclosure_type": "NEWS",
                "priority_reason": _clean(item.get("description")) or None,
                "created_at": item.get("pubDate"),
            }
        )
        if len(events) >= max_items:
            break
    return events


def _enrich_prompt(name: str, headlines: list[str]) -> str:
    lines = "\n".join(f"- {h}" for h in headlines)
    return f"""
You are a research assistant for a Korean stock, {name}. Below are recent news
headlines about it. Use web search to add the CONCRETE FACTS each headline omits:
product names, model numbers, figures/amounts, dates, named people/partners, and
exactly WHAT happened. Do NOT invent anything you cannot verify by search.

Headlines:
{lines}

Write a concise Korean factual brief (bullet points, <= 250 words). Facts only —
no opinions, no investment views, no search-keyword suggestions.
""".strip()


def enrich_events(
    stock: dict[str, Any], events: list[dict[str, Any]], *, max_headlines: int = ENRICH_MAX_HEADLINES
) -> list[dict[str, Any]]:
    """Append one grounded "facts brief" event derived from the headlines.

    Best-effort: returns ``events`` unchanged if grounding is unavailable, errors,
    or yields nothing — the pipeline never loses the original news signal.
    """
    if not events:
        return events
    names = _stock_names(stock)
    primary = names[0] if names else stock.get("ticker", "")
    headlines = [e["report_name"] for e in events[:max_headlines] if e.get("report_name")]
    if not headlines:
        return events
    try:
        brief = call_gemini_grounded(_enrich_prompt(primary, headlines))
    except Exception:
        return events
    brief = (brief or "").strip()
    if not brief:
        return events
    brief_event = {
        "report_name": f"{primary} 최근 사건 상세 브리핑(웹검색 보충)",
        "disclosure_type": "BRIEF",
        "priority_reason": brief[:2000],
        "created_at": None,
    }
    return events + [brief_event]


async def gather_events(
    stock: dict[str, Any],
    *,
    days: int,
    source: str = "both",
    ground: bool = True,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Collect events from DART and/or news, dedup, then optionally enrich.

    ``days`` is the DART lookback; news uses its own ``NEWS_LOOKBACK_DAYS`` window.
    """
    events: list[dict[str, Any]] = []
    if source in ("dart", "both"):
        # Lazy import avoids a load-time cycle (refresh imports this module).
        from datalab_polarity_refresh import fetch_recent_disclosures

        events += await fetch_recent_disclosures(stock["id"], days=days)
    if source in ("news", "both"):
        events += await fetch_recent_news(stock, max_items=max_items)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for e in events:
        key = (e.get("report_name") or "").strip()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(e)
    events = deduped

    if ground and events:
        events = enrich_events(stock, events)
    return events
