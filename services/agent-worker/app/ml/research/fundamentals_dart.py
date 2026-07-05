"""연구 전용 OpenDART 분기 매출 리더 (자급자족).

채용→사업확장→**매출** 나우캐스팅 검정의 *타깃*(매출)을 OpenDART에서 직접 인출한다. 팀
DART 수집기(`app/collectors/dart/`)·prod 공유 테이블에 **의존·기록하지 않고**, 결과를 연구
CSV 캐시(`revenue_dart.csv`)로만 남긴다(사용자 명시 승인된 연구 스코프 예외).

세 가지 load-bearing 처리:
1. **분기 누적 차분**: 분기보고서 금액은 누적(YTD)이라 단일분기로 차분한다
   (Q1=Q1cum, Q2=H1cum−Q1cum, Q3=Q3cum−H1cum, Q4=FYcum−Q3cum).
2. **PIT(누수 차단)**: 공시일 ``known_at = rcept_no[:8]`` 을 복원·저장 — 라벨은 known_at 이후만.
3. **CFS→OFS 폴백 / 표준계정 우선**: 연결 우선, 매출은 account_id 우선·계정명 폴백.

네트워크 계층(urllib)과 순수 로직(차분·추출·known_at)을 분리해 후자는 DB/네트워크 없이 단위
테스트한다. 팀 모듈을 import 하지 않는다(호출 패턴만 참고).
"""

from __future__ import annotations

import csv
import io
import json
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode
from urllib.request import Request, urlopen

OPENDART = "https://opendart.fss.or.kr/api"

# reprt_code → 누적 분기 끝 (그 보고서가 '누적으로' 담는 마지막 분기).
_REPRT_ORDER = [("11013", 1), ("11012", 2), ("11014", 3), ("11011", 4)]
_REPRT_LABEL = {1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4"}

# 매출 계정 식별 — 표준 account_id 우선, 계정명 폴백(라벨만 다른 동일 항목 흡수).
_REVENUE_IDS = frozenset({"ifrs-full_Revenue", "ifrs_Revenue"})
_REVENUE_NAMES = frozenset({"매출액", "수익(매출액)", "영업수익", "매출"})
_INCOME_STATEMENTS = frozenset({"IS", "CIS"})


@dataclass(frozen=True)
class QuarterRevenue:
    ticker: str
    corp_code: str
    year: int
    quarter: int            # 1..4
    single_revenue_krw: int  # 단일분기 매출(누적 차분 후)
    known_at: str            # 공시일 'YYYY-MM-DD' (rcept_no[:8])
    fs_div: str              # CFS | OFS

    @property
    def period_label(self) -> str:
        return f"{self.year}{_REPRT_LABEL[self.quarter]}"


# --- 순수 로직 (네트워크 없음, 단위 테스트 대상) ------------------------------

def known_at_from_rcept(rcept_no: str) -> str | None:
    """접수번호(14자리, 앞 8자리=YYYYMMDD)에서 공시일을 복원."""
    s = (rcept_no or "").strip()
    if len(s) < 8 or not s[:8].isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8])).isoformat()
    except ValueError:
        return None


def extract_revenue(items: list[dict]) -> tuple[int, str] | None:
    """재무제표 계정 리스트에서 (누적매출, rcept_no). 손익계산서 매출 계정만.

    ⚠️ 중간보고서의 ``thstrm_amount`` 는 3개월(단일분기) 값이고, 누적(YTD)은
    ``thstrm_add_amount`` 에 들어있다(연간보고서는 add가 비어 thstrm_amount=연간누적).
    따라서 누적 차분의 일관성을 위해 **add 우선, 없으면 thstrm** 로 누적값을 잡는다.
    """
    by_id = None
    by_name = None
    for it in items:
        if str(it.get("sj_div") or "").strip() not in _INCOME_STATEMENTS:
            continue
        amount = _cumulative_amount(it)
        if amount is None:
            continue
        rcept = str(it.get("rcept_no") or "").strip()
        acc_id = (it.get("account_id") or "").strip()
        acc_nm = (it.get("account_nm") or "").strip()
        if acc_id in _REVENUE_IDS and by_id is None:
            by_id = (amount, rcept)
        elif acc_nm in _REVENUE_NAMES and by_name is None:
            by_name = (amount, rcept)
    return by_id or by_name


def _cumulative_amount(item: dict) -> int | None:
    """누적(YTD) 금액: ``thstrm_add_amount`` 우선, 비면 ``thstrm_amount`` 폴백."""
    add = _to_int(item.get("thstrm_add_amount"))
    return add if add is not None else _to_int(item.get("thstrm_amount"))


def single_quarter_rows(
    ticker: str,
    corp_code: str,
    year: int,
    cumulative: dict[str, tuple[int, str]],
    *,
    fs_div: str,
) -> list[QuarterRevenue]:
    """한 (종목,연도)의 reprt별 누적매출 dict → 단일분기 매출 행들.

    ``cumulative``: reprt_code → (누적매출, rcept_no). 인접 보고서가 모두 있어야 차분
    가능하므로, 비는 분기는 건너뛴다(부분 적재 허용). 각 단일분기의 known_at 은 그 분기를
    드러낸 *해당* 보고서의 공시일.
    """
    rows: list[QuarterRevenue] = []
    prev_cum = 0
    prev_ok = True  # Q1은 직전 누적=0(연초)에서 시작
    for reprt_code, q in _REPRT_ORDER:
        entry = cumulative.get(reprt_code)
        if entry is None:
            prev_ok = False  # 이 분기 누적 없음 → 다음 분기 차분 불가
            continue
        cum, rcept = entry
        known = known_at_from_rcept(rcept)
        if prev_ok and known is not None:
            rows.append(
                QuarterRevenue(
                    ticker=ticker, corp_code=corp_code, year=year, quarter=q,
                    single_revenue_krw=cum - prev_cum, known_at=known, fs_div=fs_div,
                )
            )
        prev_cum = cum
        prev_ok = True
    return rows


def _to_int(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "-"):
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            return None


# --- 네트워크 계층 (urllib; 자급자족) ----------------------------------------

def _get_bytes(url: str, *, timeout: int = 20) -> bytes:
    req = Request(url, headers={"Accept": "*/*"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _get_json(url: str, *, timeout: int = 20, retries: int = 3) -> dict:
    """OpenDART JSON GET — 일시적 네트워크 오류(타임아웃 등)는 백오프 재시도."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return json.loads(_get_bytes(url, timeout=timeout).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 네트워크/파싱 모두 재시도 후 포기
            last = exc
            time.sleep(0.5 * (2 ** attempt))
    raise last or RuntimeError("OpenDART request failed")


def fetch_corp_code_map(api_key: str) -> dict[str, str]:
    """OpenDART corpCode.xml(zip) → {6자리 종목코드: 8자리 corp_code}."""
    url = f"{OPENDART}/corpCode.xml?{urlencode({'crtfc_key': api_key})}"
    raw = _get_bytes(url)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    xml_bytes = zf.read(zf.namelist()[0])
    root = ET.fromstring(xml_bytes)
    out: dict[str, str] = {}
    for el in root.iter("list"):
        stock = (el.findtext("stock_code") or "").strip()
        corp = (el.findtext("corp_code") or "").strip()
        if stock and corp and stock != " ":
            out[stock] = corp
    return out


def fetch_cumulative_revenue(
    api_key: str, corp_code: str, year: int, reprt_code: str,
    *, fs_priority: tuple[str, ...] = ("CFS", "OFS"), min_interval_sec: float = 0.2,
) -> tuple[int, str, str] | None:
    """(누적매출, rcept_no, fs_div) — 연결 우선, 무자료면 별도 폴백. 없으면 None."""
    for fs_div in fs_priority:
        url = f"{OPENDART}/fnlttSinglAcntAll.json?" + urlencode({
            "crtfc_key": api_key, "corp_code": corp_code,
            "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": fs_div,
        })
        time.sleep(min_interval_sec)  # OpenDART fair-access
        resp = _get_json(url)
        status = resp.get("status")
        if status == "013":   # 조회된 데이터 없음
            continue
        if status != "000":
            continue          # 키오류/장애 등은 해당 (corp,year,reprt,fs) 스킵
        rev = extract_revenue(list(resp.get("list", [])))
        if rev is not None:
            return rev[0], rev[1], fs_div
    return None


def build_revenue_csv(
    api_key: str, tickers: list[str], years: list[int], out_path: str,
    *, corp_map: dict[str, str] | None = None, min_interval_sec: float = 0.2,
) -> dict[str, int]:
    """유니버스×연도 분기 매출을 인출해 ``out_path`` CSV로. 요약 카운트 반환."""
    corp_map = corp_map or fetch_corp_code_map(api_key)
    rows: list[QuarterRevenue] = []
    stats = {"tickers": 0, "no_corp": 0, "quarter_rows": 0}
    for ticker in tickers:
        corp = corp_map.get(ticker)
        if not corp:
            stats["no_corp"] += 1
            continue
        stats["tickers"] += 1
        for year in years:
            cumulative: dict[str, tuple[int, str]] = {}
            fs_used = "CFS"
            for reprt_code, _q in _REPRT_ORDER:
                try:
                    got = fetch_cumulative_revenue(
                        api_key, corp, year, reprt_code, min_interval_sec=min_interval_sec
                    )
                except Exception:  # noqa: BLE001 - 재시도 후에도 실패한 단건은 건너뛴다
                    stats["fetch_errors"] = stats.get("fetch_errors", 0) + 1
                    continue
                if got is not None:
                    cumulative[reprt_code] = (got[0], got[1])
                    fs_used = got[2]
            rows.extend(single_quarter_rows(ticker, corp, year, cumulative, fs_div=fs_used))
    rows.sort(key=lambda r: (r.ticker, r.year, r.quarter))
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "corp_code", "year", "quarter", "period_label",
                    "single_revenue_krw", "known_at", "fs_div"])
        for r in rows:
            w.writerow([r.ticker, r.corp_code, r.year, r.quarter, r.period_label,
                        r.single_revenue_krw, r.known_at, r.fs_div])
    stats["quarter_rows"] = len(rows)
    return stats


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import os

    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description="연구용 OpenDART 분기 매출 인출")
    ap.add_argument("--tickers", required=True, help="콤마구분 6자리 종목코드")
    ap.add_argument("--start-year", type=int, default=2016)
    ap.add_argument("--end-year", type=int, default=2024)
    ap.add_argument("--out", required=True, help="출력 CSV 경로")
    ap.add_argument("--api-key", default=os.environ.get("DART_API_KEY", ""))
    args = ap.parse_args(argv)
    if not args.api_key:
        raise SystemExit("DART_API_KEY 필요(.env 또는 --api-key). opendart.fss.or.kr 무료 발급.")
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    years = list(range(args.start_year, args.end_year + 1))
    stats = build_revenue_csv(args.api_key, tickers, years, args.out)
    print(f"[revenue-dart] {stats} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
