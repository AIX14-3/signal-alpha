"""Optional LLM judgment layer, evaluated head-to-head with the rule model.

The LLM only ever sees an as-of indicator snapshot (values at row t) — never
future prices — so it is held to the same no-look-ahead standard. We sample a
limited number of OOS points per stock (LLM calls are slow/costly), ask for a
direction, and compare its hit-rate to the rule model on the SAME points.

Follows the repo's provider-agnostic convention (OpenAI / Gemini, selected by
env). If no API key is configured the layer is skipped and the report says so.
"""

from __future__ import annotations

import json

import httpx
import numpy as np
import pandas as pd

from config import AI_ERA_START, HORIZONS, get_settings
from indicators import FEATURE_COLS, build_features
from ingest import load_ohlcv
from labeling import add_labels
import metrics
from signals import rule_predict
from universe import UNIVERSE

PROMPT = (
    "You are a disciplined technical analyst. Given ONLY the technical-indicator "
    "snapshot of a stock as of a date (no future information), judge the most "
    "likely direction over the next {horizon}. Indicators include RSI, MACD, "
    "Stochastic, Stochastic RSI, OBV slope and candlestick geometry.\n"
    "Respond with STRICT JSON only: {{\"direction\": \"up\"|\"down\", "
    "\"confidence\": 0-100}}. No prose.\n\nSNAPSHOT:\n{snapshot}"
)


def _snapshot(row: pd.Series) -> str:
    fields = {c: (round(float(row[c]), 4) if pd.notna(row[c]) else None) for c in FEATURE_COLS}
    return json.dumps(fields, ensure_ascii=False)


async def _call_llm(http: httpx.AsyncClient, s, prompt: str) -> str:
    if s.llm_provider == "openai":
        r = await http.post(
            f"{s.openai_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {s.openai_api_key}"},
            json={"model": s.llm_model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0},
            timeout=s.llm_timeout_seconds,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    # gemini
    r = await http.post(
        f"{s.gemini_base_url}/models/{s.llm_model}:generateContent?key={s.gemini_api_key}",
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": 0}},
        timeout=s.llm_timeout_seconds,
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _parse_direction(text: str) -> int | None:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        obj = json.loads(text[start:end])
        d = str(obj.get("direction", "")).lower()
        return 1 if d == "up" else -1 if d == "down" else None
    except (ValueError, json.JSONDecodeError):
        return None


def _has_key(s) -> bool:
    return bool(s.openai_api_key if s.llm_provider == "openai" else s.gemini_api_key)


async def run_llm_judge() -> dict | None:
    s = get_settings()
    if not _has_key(s):
        return None

    per_h = {h: {"llm_pred": [], "rule_pred": [], "label": [], "ret": []} for h in HORIZONS}
    async with httpx.AsyncClient() as http:
        for inst in UNIVERSE:
            try:
                feat = add_labels(build_features(load_ohlcv(inst.symbol)))
            except FileNotFoundError:
                continue
            ai = feat[feat["date"] >= pd.Timestamp(AI_ERA_START)]
            sample = ai.dropna(subset=FEATURE_COLS).tail(s.llm_sample_per_stock)
            if sample.empty:
                continue
            rule_p = rule_predict(sample)
            for (_, row), rp in zip(sample.iterrows(), rule_p):
                prompt = PROMPT.format(horizon="1 week", snapshot=_snapshot(row))
                try:
                    text = await _call_llm(http, s, prompt)
                except Exception:  # noqa: BLE001
                    continue
                d = _parse_direction(text)
                if d is None:
                    continue
                for h in HORIZONS:  # one LLM call, scored across horizons
                    per_h[h]["llm_pred"].append(d)
                    per_h[h]["rule_pred"].append(int(rp))
                    per_h[h]["label"].append(row[f"label_{h}"])
                    per_h[h]["ret"].append(row[f"ret_{h}"])
            print(f"  [llm] {inst.symbol} done")

    result = {}
    for h in HORIZONS:
        b = {k: np.array(v, dtype=float) for k, v in per_h[h].items()}
        if not len(b["label"]):
            continue
        result[h] = {
            "llm": metrics.directional(b["llm_pred"], b["label"], b["ret"]),
            "rule_same": metrics.directional(b["rule_pred"], b["label"], b["ret"]),
        }
    return result
