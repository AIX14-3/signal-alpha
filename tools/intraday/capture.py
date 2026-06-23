"""Intraday hybrid capture for backtesting — 삼성전자(005930)·SK하이닉스(000660).

Sources (hybrid, decided 2026-06-18):
  Toss   : price, volume(trades), order-book residual quantity (slippage)
  Kiwoom : investor trend foreign/institution (ka10059), program trading

Storage: raw JSONL — one line per API call, full response preserved so the
fields can be normalised later. Files rotate into 30-minute buckets:
  data/intraday/<YYYYMMDD>/<source>_<kind>_<HHMM>.jsonl   (HHMM = 0900,0930,…)

Sampling: 1-minute cadence (configurable). Market gate 09:00–15:40 KST.

Usage:
  python tools/intraday/capture.py --once        # dry-run: one cycle + endpoint probe
  python tools/intraday/capture.py               # live loop during market hours
  python tools/intraday/capture.py --ignore-market   # live loop now, skip the time gate

Some endpoints/TRs need live confirmation (Toss orderbook/trades param key,
Kiwoom program-trading api-id). `--once` issues each call once and prints a
PASS/FAIL diagnostic so the exact names can be locked before the live run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kiwoom_client import KiwoomRestClient, RateLimiter, TokenManager  # noqa: E402
from toss_client import TossClient, TossTokenManager  # noqa: E402

KST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SYMBOLS = ["005930", "000660"]  # 삼성전자, SK하이닉스

# --- endpoint constants (verify with --once, then adjust here if needed) ---
TOSS_PRICES = "/api/v1/prices"
TOSS_ORDERBOOK = "/api/v1/orderbook"
TOSS_TRADES = "/api/v1/trades"
# /prices takes a comma list 'symbols'; /orderbook and /trades take single 'symbol'
# (confirmed by --once: they 400 with field=symbol when given 'symbols').
TOSS_PRICES_PARAM = "symbols"
TOSS_SINGLE_PARAM = "symbol"

KIWOOM_INVESTOR_API_ID = "ka10059"  # 종목별투자자기관별 (repo-verified)
KIWOOM_STKINFO_PATH = "/api/dostk/stkinfo"

# Program-trading TR is not in the repo spec — candidates probed by --once.
# ka90004 (종목별프로그램매매현황) exists on stkinfo and requires mrkt_tp; probe
# a few market-type codes. Lock the winner via --program-api-id/--program-body.
PROGRAM_CANDIDATES = [
    ("ka90004", KIWOOM_STKINFO_PATH, {"mrkt_tp": "P00101", "amt_qty_tp": "1", "stex_tp": "1"}),
    ("ka90004", KIWOOM_STKINFO_PATH, {"mrkt_tp": "0", "amt_qty_tp": "1", "stex_tp": "1"}),
    ("ka90004", KIWOOM_STKINFO_PATH, {"mrkt_tp": "000", "amt_qty_tp": "1", "stex_tp": "1"}),
]


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def load_env() -> None:
    env_path = REPO_ROOT / ".env"
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
        return
    except Exception:
        pass
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


class Config:
    def __init__(self) -> None:
        self.toss_base = os.getenv("TOSS_API_BASE", "https://openapi.tossinvest.com").rstrip("/")
        self.toss_id = os.getenv("TOSS_CLIENT_ID", "")
        self.toss_secret = os.getenv("TOSS_CLIENT_SECRET", "")
        self.toss_min_interval = float(os.getenv("TOSS_MIN_REQUEST_INTERVAL_SEC", "0.25"))

        self.kiwoom_app_key = os.getenv("KIWOOM_APP_KEY", "")
        self.kiwoom_secret = os.getenv("KIWOOM_SECRET_KEY", "")
        use_mock = _env_bool("KIWOOM_USE_MOCK", default=False)
        base = os.getenv("KIWOOM_REST_BASE_URL", "").strip()
        if not base:
            base = "https://mockapi.kiwoom.com" if use_mock else "https://api.kiwoom.com"
        self.kiwoom_base = base.rstrip("/")
        self.kiwoom_min_interval = float(os.getenv("KIWOOM_TR_DELAY_SEC", "0.25"))
        self.timeout = float(os.getenv("CAPTURE_TIMEOUT_SECONDS", "10"))


def build_clients(http: httpx.AsyncClient, cfg: Config):
    toss = TossClient(
        http=http,
        api_base=cfg.toss_base,
        token_manager=TossTokenManager(
            http=http, api_base=cfg.toss_base,
            client_id=cfg.toss_id, client_secret=cfg.toss_secret,
        ),
        min_request_interval_sec=cfg.toss_min_interval,
    )
    kiwoom = KiwoomRestClient(
        http=http,
        api_base=cfg.kiwoom_base,
        token_manager=TokenManager(
            http=http, api_base=cfg.kiwoom_base,
            app_key=cfg.kiwoom_app_key, app_secret=cfg.kiwoom_secret,
        ),
        rate_limiter=RateLimiter(cfg.kiwoom_min_interval),
    )
    return toss, kiwoom


class JsonlWriter:
    """Append raw records to 30-minute-bucketed JSONL files."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir

    def _path(self, ts: datetime, source: str, kind: str) -> Path:
        bucket_min = (ts.minute // 30) * 30
        hhmm = ts.replace(minute=bucket_min, second=0, microsecond=0).strftime("%H%M")
        day_dir = self.out_dir / ts.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        return day_dir / f"{source}_{kind}_{hhmm}.jsonl"

    def write(self, ts: datetime, source: str, kind: str, symbol: str, raw: object) -> Path:
        record = {
            "captured_at": ts.isoformat(),
            "source": source,
            "kind": kind,
            "symbol": symbol,
            "raw": raw,
        }
        path = self._path(ts, source, kind)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path


# ----------------------------- fetchers -----------------------------------

async def toss_prices(toss: TossClient, symbols: list[str]) -> dict:
    return await toss.get(TOSS_PRICES, {TOSS_PRICES_PARAM: ",".join(symbols)})


async def toss_orderbook(toss: TossClient, symbol: str) -> dict:
    return await toss.get(TOSS_ORDERBOOK, {TOSS_SINGLE_PARAM: symbol})


async def toss_trades(toss: TossClient, symbol: str) -> dict:
    return await toss.get(TOSS_TRADES, {TOSS_SINGLE_PARAM: symbol})


async def kiwoom_investor(kiwoom: KiwoomRestClient, symbol: str, trade_date: str) -> dict:
    return await kiwoom.request(
        KIWOOM_INVESTOR_API_ID,
        {
            "dt": trade_date,
            "stk_cd": symbol,
            "amt_qty_tp": "1",  # 1: 수량
            "trde_tp": "0",  # 0: 순매수
            "unit_tp": "1",
        },
        path=KIWOOM_STKINFO_PATH,
    )


async def kiwoom_program(
    kiwoom: KiwoomRestClient, symbol: str, trade_date: str,
    api_id: str, path: str, extra: dict | None = None,
) -> dict:
    body = {"stk_cd": symbol, "dt": trade_date}
    if extra:
        body.update(extra)
    return await kiwoom.request(api_id, body, path=path)


# ----------------------------- cycle --------------------------------------

async def run_cycle(toss, kiwoom, cfg, args, writer: JsonlWriter, *, diag: bool) -> None:
    ts = now_kst()
    trade_date = ts.strftime("%Y%m%d")
    symbols = args.symbols

    async def do(source, kind, symbol, coro):
        try:
            raw = await coro
            path = writer.write(ts, source, kind, symbol, raw)
            if diag:
                keys = list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__
                rc = raw.get("return_code") if isinstance(raw, dict) else None
                print(f"  PASS  {source:6} {kind:12} {symbol:10} keys={keys} rc={rc} -> {path.name}")
        except Exception as exc:  # one call must never abort the sweep
            if diag:
                print(f"  FAIL  {source:6} {kind:12} {symbol:10} {type(exc).__name__}: {str(exc)[:160]}")

    # Toss: price (one multi-symbol call), order book + trades per symbol
    await do("toss", "price", ",".join(symbols), toss_prices(toss, symbols))
    for sym in symbols:
        await do("toss", "orderbook", sym, toss_orderbook(toss, sym))
        await do("toss", "trades", sym, toss_trades(toss, sym))

    # Kiwoom: investor trend per symbol
    for sym in symbols:
        await do("kiwoom", "investor", sym, kiwoom_investor(kiwoom, sym, trade_date))

    # Kiwoom: program trading
    if diag and not args.program_api_id:
        # probe candidates once to discover the right TR + params
        for idx, (api_id, path, extra) in enumerate(PROGRAM_CANDIDATES):
            await do("kiwoom", f"program[{api_id}:{extra.get('mrkt_tp','')}]", symbols[0],
                     kiwoom_program(kiwoom, symbols[0], trade_date, api_id, path, extra))
    elif args.program_api_id:
        # ka90004 returns the market-wide program-trading top-50 (not per-stock);
        # one call captures both targets — filter by stk_cd in preprocessing.
        extra = json.loads(args.program_body) if args.program_body else None
        await do("kiwoom", "program", "market",
                 kiwoom_program(kiwoom, symbols[0], trade_date, args.program_api_id, args.program_path, extra))


# ----------------------------- market gate --------------------------------

def parse_hhmm(value: str) -> dtime:
    hour, minute = value.split(":")
    return dtime(int(hour), int(minute))


def is_market_open(moment: datetime, open_t: dtime, close_t: dtime) -> bool:
    if moment.weekday() >= 5:  # weekend
        return False
    return open_t <= moment.time() <= close_t


async def run_live(toss, kiwoom, cfg, args, writer: JsonlWriter) -> None:
    open_t = parse_hhmm(args.open)
    close_t = parse_hhmm(args.close)
    print(f"[live] gate {args.open}-{args.close} KST, every {args.interval}s, symbols={args.symbols}")
    while True:
        now = now_kst()
        if args.ignore_market or is_market_open(now, open_t, close_t):
            await run_cycle(toss, kiwoom, cfg, args, writer, diag=False)
            print(f"[live] cycle {now.strftime('%H:%M:%S')} done")
            await asyncio.sleep(args.interval)
            continue
        # before open: wait; after close: stop
        if now.time() > close_t or now.weekday() >= 5:
            print(f"[live] market closed at {now.strftime('%H:%M:%S')} - stopping")
            return
        wait = (datetime.combine(now.date(), open_t, tzinfo=KST) - now).total_seconds()
        print(f"[live] before open - sleeping {int(max(wait, 1))}s")
        await asyncio.sleep(min(max(wait, 1), 60))


async def main_async(args) -> None:
    load_env()
    cfg = Config()
    out_dir = Path(args.out_dir) if args.out_dir else REPO_ROOT / "data" / "intraday"
    writer = JsonlWriter(out_dir)
    print(f"toss_base={cfg.toss_base}  kiwoom_base={cfg.kiwoom_base}  out={out_dir}")
    async with httpx.AsyncClient(timeout=cfg.timeout) as http:
        toss, kiwoom = build_clients(http, cfg)
        if args.once:
            print("[once] single cycle + endpoint probe:")
            await run_cycle(toss, kiwoom, cfg, args, writer, diag=True)
        else:
            await run_live(toss, kiwoom, cfg, args, writer)


def main() -> None:
    p = argparse.ArgumentParser(description="Intraday hybrid capture (Toss + Kiwoom)")
    p.add_argument("--once", action="store_true", help="one cycle + diagnostics, then exit")
    p.add_argument("--ignore-market", action="store_true", help="live loop now, skip time gate")
    p.add_argument("--interval", type=float, default=60.0, help="seconds between cycles (default 60 = 1min)")
    p.add_argument("--open", default="09:00", help="market open HH:MM KST")
    p.add_argument("--close", default="15:40", help="market close HH:MM KST")
    p.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="ticker codes")
    p.add_argument("--out-dir", default=None, help="output dir (default <repo>/data/intraday)")
    p.add_argument("--program-api-id", default=None, help="locked Kiwoom program-trading api-id for live")
    p.add_argument("--program-path", default=KIWOOM_STKINFO_PATH, help="path for program-trading TR")
    p.add_argument("--program-body", default=None, help="extra body params for program TR as JSON")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
