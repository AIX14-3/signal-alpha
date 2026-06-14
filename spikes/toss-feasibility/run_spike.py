"""Toss Securities Open API feasibility spike.

Runs scenarios A-F against the LIVE Toss API using credentials from the
environment, prints a summary, and writes FINDINGS.md. Every scenario is
isolated: a failure is recorded and the run continues, so one broken
endpoint never hides results from the others.

Usage:
    # from this directory, with TOSS_CLIENT_ID / TOSS_CLIENT_SECRET in env or ../../.env
    python run_spike.py
    # or, inside the uv workspace:
    uv run python run_spike.py

Exact query-parameter shapes are not fully documented, so scenarios that
take params probe a few likely shapes and record whichever the server
accepts. Read FINDINGS.md to see the real response shapes and tune from there.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is a workspace dep; degrade gracefully
    load_dotenv = None

from config import get_settings
from toss_client import TossApiError, TossAuthError, TossClient, TossTokenManager

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
FINDINGS_PATH = HERE / "FINDINGS.md"


def _sample(obj: object, limit: int = 1500) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        text = str(obj)
    return text if len(text) <= limit else text[:limit] + "\n… (truncated)"


class Scenario:
    def __init__(self, code: str, title: str) -> None:
        self.code = code
        self.title = title
        self.status = "SKIP"
        self.detail = ""
        self.sample = ""
        self.elapsed_ms = 0.0

    def passed(self, detail: str, sample: object = None) -> None:
        self.status = "PASS"
        self.detail = detail
        if sample is not None:
            self.sample = _sample(sample)

    def failed(self, detail: str, sample: object = None) -> None:
        self.status = "FAIL"
        self.detail = detail
        if sample is not None:
            self.sample = _sample(sample)


async def _try_get(client: TossClient, path: str, param_variants: list[dict | None]):
    """Return (params_used, response) for the first variant the server accepts."""
    last_err: Exception | None = None
    for params in param_variants:
        try:
            resp = await client.get(path, params)
            return params, resp
        except TossApiError as exc:
            last_err = exc
            continue
    if last_err:
        raise last_err
    raise RuntimeError(f"no variants tried for {path}")


async def scenario_a_auth(tokens: TossTokenManager) -> Scenario:
    s = Scenario("A", "인증 (POST /oauth2/token)")
    t0 = time.monotonic()
    try:
        token = await tokens.get_token()
        s.elapsed_ms = (time.monotonic() - t0) * 1000
        s.passed(
            f"토큰 발급 성공 (len={len(token)}), 만료 추정 {tokens.expires_at}",
            {"token_prefix": token[:8] + "…", "expires_at": str(tokens.expires_at)},
        )
    except (TossAuthError, httpx.HTTPError) as exc:
        s.failed(f"토큰 발급 실패: {exc}")
    return s


async def scenario_b_kr_price(client: TossClient, symbol: str) -> Scenario:
    s = Scenario("B", "국내 실시간 시세 (GET /api/v1/prices)")
    t0 = time.monotonic()
    try:
        params, resp = await _try_get(
            client,
            "/api/v1/prices",
            [{"symbols": symbol}, {"symbol": symbol}, {"code": symbol}],
        )
        s.elapsed_ms = (time.monotonic() - t0) * 1000
        s.passed(f"params={params}, 응답 키={list(resp)[:10]}", resp)
    except TossApiError as exc:
        s.failed(str(exc))
    return s


def _extract_candles(resp: object) -> list:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for key in ("candles", "data", "items", "result"):
            val = resp.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):  # e.g. {"result": {"candles": [...]}}
                for k2 in ("candles", "data", "items"):
                    if isinstance(val.get(k2), list):
                        return val[k2]
    return []


def _candle_timestamp(candle: dict) -> str | None:
    if not isinstance(candle, dict):
        return None
    for k in ("dateTime", "timestamp", "date", "time", "dt", "baseDateTime", "tradeDate"):
        if candle.get(k):
            return str(candle[k])
    return None


async def scenario_c_candle_depth(client: TossClient, symbol: str) -> Scenario:
    s = Scenario("C", "10년 일봉 깊이 (GET /api/v1/candles, count<=200 → 페이지네이션) ⚠️ 핵심")
    t0 = time.monotonic()
    ten_years_ago = date.today() - timedelta(days=365 * 10)
    total = 0
    earliest_ts: str | None = None
    pages = 0
    first_sample = None
    before: str | None = None
    try:
        # API caps count at 200/call, so walk backwards with `before` until we
        # reach ~10y, run out of data, or hit a safety cap (15 pages ≈ 3000 bars).
        for _ in range(15):
            params = {"symbol": symbol, "interval": "1d", "count": 200}
            if before:
                params["before"] = before
            resp = await client.get("/api/v1/candles", params)
            if first_sample is None:
                first_sample = resp
            candles = _extract_candles(resp)
            if not candles:
                break
            pages += 1
            total += len(candles)
            last_ts = _candle_timestamp(candles[-1]) or _candle_timestamp(candles[0])
            if last_ts:
                earliest_ts = last_ts
                before = last_ts
                if earliest_ts[:10] <= ten_years_ago.isoformat():
                    break
            else:
                break  # cannot paginate without a timestamp field
        s.elapsed_ms = (time.monotonic() - t0) * 1000
        reached = earliest_ts is not None and earliest_ts[:10] <= ten_years_ago.isoformat()
        verdict = "10년 도달 ✅" if reached else "10년 미도달(데이터 한계 가능)"
        (s.passed if total else s.failed)(
            f"총 {total}봉 / {pages}페이지, 가장 과거={earliest_ts}, "
            f"목표={ten_years_ago.isoformat()} → {verdict}",
            {"total_bars": total, "pages": pages, "earliest": earliest_ts,
             "first_candle_sample": _extract_candles(first_sample)[:1]},
        )
    except TossApiError as exc:
        s.failed(str(exc))
    return s


async def scenario_d_us(client: TossClient, symbol: str) -> Scenario:
    s = Scenario("D", "미국 시세 (GET /api/v1/prices + /market-calendar/US)")
    t0 = time.monotonic()
    try:
        params, price = await _try_get(
            client,
            "/api/v1/prices",
            [{"symbols": symbol}, {"symbol": symbol}, {"code": symbol}],
        )
        cal = None
        try:
            _, cal = await _try_get(client, "/api/v1/market-calendar/US", [None, {}])
        except TossApiError:
            cal = "calendar 호출 실패(별도 확인)"
        s.elapsed_ms = (time.monotonic() - t0) * 1000
        s.passed(
            f"미국 종목({symbol}) 시세 조회 성공 params={params}",
            {"price": price, "us_calendar": cal},
        )
    except TossApiError as exc:
        s.failed(str(exc))
    return s


async def scenario_e_fx(client: TossClient) -> Scenario:
    s = Scenario("E", "환율 (GET /api/v1/exchange-rate)")
    t0 = time.monotonic()
    try:
        resp = await client.get(
            "/api/v1/exchange-rate",
            {"baseCurrency": "USD", "quoteCurrency": "KRW"},
        )
        s.elapsed_ms = (time.monotonic() - t0) * 1000
        s.passed(
            f"USD→KRW 환율 응답 키={list(resp)[:10] if isinstance(resp, dict) else type(resp)}",
            resp,
        )
    except TossApiError as exc:
        s.failed(str(exc))
    return s


async def scenario_f_ratelimit(client: TossClient, symbol: str) -> Scenario:
    s = Scenario("F", "레이트리밋 실측 (prices 연속 10회)")
    t0 = time.monotonic()
    ok = 0
    throttled = 0
    errors: list[str] = []
    for _ in range(10):
        try:
            await client.get("/api/v1/prices", {"symbols": symbol})
            ok += 1
        except TossApiError as exc:
            if exc.status == 429:
                throttled += 1
            else:
                errors.append(f"HTTP {exc.status}")
    s.elapsed_ms = (time.monotonic() - t0) * 1000
    detail = f"성공 {ok}/10, 429(throttle) {throttled}, 기타오류 {len(errors)}"
    (s.passed if ok else s.failed)(detail, {"errors": errors[:5]})
    return s


def write_findings(scenarios: list[Scenario], settings) -> None:
    lines: list[str] = []
    lines.append("# 토스증권 API 타당성 검증 — FINDINGS")
    lines.append("")
    lines.append(f"- 실행 시각: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- API base: `{settings.toss_api_base}`")
    lines.append(f"- 샘플 종목: KR=`{settings.kr_symbol}`, US=`{settings.us_symbol}`")
    lines.append("")
    lines.append("## 요약")
    lines.append("")
    lines.append("| # | 검증 항목 | 결과 | 소요(ms) | 비고 |")
    lines.append("|---|----------|------|----------|------|")
    for s in scenarios:
        note = s.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {s.code} | {s.title} | **{s.status}** | {s.elapsed_ms:.0f} | {note} |")
    lines.append("")
    lines.append("## 상세 응답 샘플")
    for s in scenarios:
        lines.append("")
        lines.append(f"### {s.code}. {s.title} — {s.status}")
        lines.append("")
        lines.append(s.detail)
        if s.sample:
            lines.append("")
            lines.append("```json")
            lines.append(s.sample)
            lines.append("```")
    lines.append("")
    lines.append("## go / no-go 판단")
    lines.append("")
    lines.append("> 아래는 결과를 보고 사람이 채운다.")
    lines.append("")
    lines.append("- [ ] **C(10년 일봉 깊이)** 가 10년 또는 페이징으로 도달 가능한가? → 백필 실현성")
    lines.append("- [ ] B/D 응답 필드가 기존 `StockSnapshot`(price-collector)에 매핑되는가?")
    lines.append("- [ ] D 미국 심볼 체계·통화가 스키마 확장(exchange/currency/country)과 맞는가?")
    lines.append("- [ ] F 레이트리밋이 전체 유니버스 폴링에 충분한가?")
    lines.append("")
    lines.append("**결론(go/no-go):** _____")
    lines.append("")
    lines.append("### go일 경우 후속 본구현 범위 (별도 작업)")
    lines.append("- 브로커 추상화 계층(`DataSourceCollector` 프로토콜) → Kiwoom/Toss 교체")
    lines.append("- 스키마 확장: `stocks`에 exchange/currency/country, `market` CHECK에 US 거래소")
    lines.append("- FX 테이블, 미국 종목 수집기, 10년 백필 잡, LLM 프롬프트 다중시장 변형")
    FINDINGS_PATH.write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    # Windows consoles default to cp949, which cannot encode ⚠ / box chars.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    # Load .env from repo root and from this dir (local overrides root).
    if load_dotenv:
        load_dotenv(REPO_ROOT / ".env")
        load_dotenv(HERE / ".env", override=True)

    settings = get_settings()
    if not settings.toss_client_id or not settings.toss_client_secret:
        print(
            "✗ TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 가 설정되지 않았습니다.\n"
            f"  {REPO_ROOT / '.env'} 또는 환경변수에 주입하세요.",
            file=sys.stderr,
        )
        return 2

    async with httpx.AsyncClient(timeout=settings.timeout_seconds) as http:
        tokens = TossTokenManager(
            http=http,
            api_base=settings.toss_api_base,
            client_id=settings.toss_client_id,
            client_secret=settings.toss_client_secret,
        )
        client = TossClient(
            http=http,
            api_base=settings.toss_api_base,
            token_manager=tokens,
            min_request_interval_sec=settings.min_request_interval_sec,
        )

        scenarios: list[Scenario] = []
        a = await scenario_a_auth(tokens)
        scenarios.append(a)

        if a.status == "PASS":
            scenarios.append(await scenario_b_kr_price(client, settings.kr_symbol))
            scenarios.append(await scenario_c_candle_depth(client, settings.kr_symbol))
            scenarios.append(await scenario_d_us(client, settings.us_symbol))
            scenarios.append(await scenario_e_fx(client))
            scenarios.append(await scenario_f_ratelimit(client, settings.kr_symbol))
        else:
            print("인증 실패로 이후 시나리오를 건너뜁니다.", file=sys.stderr)

    print("\n=== 토스 API 스파이크 결과 ===")
    for s in scenarios:
        print(f"  [{s.status:4}] {s.code}. {s.title}  ({s.elapsed_ms:.0f}ms)")
        if s.detail:
            print(f"         {s.detail}")
    write_findings(scenarios, settings)
    print(f"\nFINDINGS 작성됨 → {FINDINGS_PATH}")
    return 0 if all(s.status == "PASS" for s in scenarios) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
