"""3사 API 키 연동 스모크 — DART · 키움 REST · 토스 Open API.

각 제공자의 키(.env)로 가장 싼 엔드포인트를 한 번씩 호출해 "데이터가 실제로 넘어오는지"만
확인한다. 시나리오는 서로 격리되어, 한 곳이 실패해도 나머지 결과를 가린다.
DB·Docker 불필요 — httpx만 있으면 단독 실행된다.

사용법 (repo 루트 .env 에 키를 넣고):
    uv run --group dev python spikes/key-connectivity/check_keys.py
    # 또는
    python spikes/key-connectivity/check_keys.py

검증 항목:
    DART    D1 list.json 연결 / D2 fnlttSinglAcntAll 매출액(PSR 분모) 조회
    KIWOOM  K1 토큰 / K2 ka10001 현재가·PER·PBR·시총
    TOSS    T1 토큰 / T2 prices 현재가 / T3 환율(USD→KRW)
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import date, datetime, timedelta
from os import getenv
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
FINDINGS_PATH = HERE / "FINDINGS.md"

# 검증용 샘플 종목 (기본 삼성전자). argv 또는 env로 교체 가능.
#   python check_keys.py <ticker> <corp_code>
#   예) 하이닉스: python check_keys.py 000660 00164779
KR_TICKER = getenv("SMOKE_TICKER") or (sys.argv[1] if len(sys.argv) > 1 else "005930")
CORP_CODE = getenv("SMOKE_CORP_CODE") or (sys.argv[2] if len(sys.argv) > 2 else "00126380")


def _load_env() -> None:
    """`.env`를 직접 파싱해 os.environ에 채운다 (python-dotenv 불필요).

    이미 환경에 있는 값은 덮어쓰지 않는다(env > .env, migrate.py와 동일 규칙).
    """
    import os

    for candidate in (REPO_ROOT / ".env", HERE / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _sample(obj: object, limit: int = 800) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        text = str(obj)
    return text if len(text) <= limit else text[:limit] + "\n… (truncated)"


class Check:
    def __init__(self, code: str, title: str) -> None:
        self.code = code
        self.title = title
        self.status = "SKIP"
        self.detail = ""
        self.sample = ""
        self.elapsed_ms = 0.0

    def ok(self, detail: str, sample: object = None) -> "Check":
        self.status = "PASS"
        self.detail = detail
        if sample is not None:
            self.sample = _sample(sample)
        return self

    def fail(self, detail: str, sample: object = None) -> "Check":
        self.status = "FAIL"
        self.detail = detail
        if sample is not None:
            self.sample = _sample(sample)
        return self


# ── DART ─────────────────────────────────────────────────────────────────────
async def check_dart(http: httpx.AsyncClient) -> list[Check]:
    base = getenv("DART_BASE_URL", "https://opendart.fss.or.kr/api").rstrip("/")
    key = getenv("DART_API_KEY", "")
    results: list[Check] = []

    d1 = Check("D1", "DART list.json 연결")
    if not key:
        results.append(d1.fail("DART_API_KEY 비어 있음"))
        return results
    t0 = time.monotonic()
    try:
        end = date.today()
        bgn = end - timedelta(days=7)
        resp = await http.get(
            f"{base}/list.json",
            params={
                "crtfc_key": key,
                "bgn_de": bgn.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "page_count": "5",
            },
        )
        d1.elapsed_ms = (time.monotonic() - t0) * 1000
        payload = resp.json()
        status = payload.get("status")
        # 000=정상, 013=조회데이터없음(키는 정상) → 둘 다 키 유효로 본다.
        if status in ("000", "013"):
            d1.ok(f"status={status} ({payload.get('message')})", payload.get("list", [])[:1] or payload)
        else:
            d1.fail(f"status={status} ({payload.get('message')})", payload)
    except Exception as exc:  # noqa: BLE001
        d1.elapsed_ms = (time.monotonic() - t0) * 1000
        d1.fail(f"{type(exc).__name__}: {exc}")
    results.append(d1)

    d2 = Check("D2", "DART 매출액(PSR 분모) 조회")
    t0 = time.monotonic()
    try:
        resp = await http.get(
            f"{base}/fnlttSinglAcntAll.json",
            params={
                "crtfc_key": key,
                "corp_code": CORP_CODE,
                "bsns_year": "2025",
                "reprt_code": "11011",  # 사업보고서(연간)
                "fs_div": "CFS",        # 연결재무제표
            },
        )
        d2.elapsed_ms = (time.monotonic() - t0) * 1000
        payload = resp.json()
        status = payload.get("status")
        rows = payload.get("list", []) if status == "000" else []
        revenue = next(
            (r for r in rows if "매출액" in (r.get("account_nm") or "")),
            None,
        )
        if revenue is not None:
            d2.ok(
                f"매출액 thstrm={revenue.get('thstrm_amount')} (원 단위)",
                {k: revenue.get(k) for k in ("account_nm", "thstrm_amount", "frmtrm_amount")},
            )
        elif status == "000":
            d2.fail("응답은 정상이나 매출액 계정 미발견", rows[:2])
        else:
            d2.fail(f"status={status} ({payload.get('message')})", payload)
    except Exception as exc:  # noqa: BLE001
        d2.elapsed_ms = (time.monotonic() - t0) * 1000
        d2.fail(f"{type(exc).__name__}: {exc}")
    results.append(d2)
    return results


# ── KIWOOM ───────────────────────────────────────────────────────────────────
async def check_kiwoom(http: httpx.AsyncClient) -> list[Check]:
    base = getenv("KIWOOM_API_BASE", "https://mockapi.kiwoom.com").rstrip("/")
    app_key = getenv("KIWOOM_APP_KEY", "")
    app_secret = getenv("KIWOOM_APP_SECRET", "")
    results: list[Check] = []

    k1 = Check("K1", "키움 토큰 발급")
    if not app_key or not app_secret:
        results.append(k1.fail("KIWOOM_APP_KEY / KIWOOM_APP_SECRET 비어 있음"))
        return results
    token = None
    t0 = time.monotonic()
    try:
        resp = await http.post(
            f"{base}/oauth2/token",
            json={"grant_type": "client_credentials", "appkey": app_key, "secretkey": app_secret},
        )
        k1.elapsed_ms = (time.monotonic() - t0) * 1000
        payload = resp.json()
        token = payload.get("token") or payload.get("access_token")
        if token:
            k1.ok(f"토큰 발급 성공 (len={len(token)}, base={base})", {"expires_dt": payload.get("expires_dt")})
        else:
            k1.fail(f"토큰 없음: {payload}", payload)
    except Exception as exc:  # noqa: BLE001
        k1.elapsed_ms = (time.monotonic() - t0) * 1000
        k1.fail(f"{type(exc).__name__}: {exc}")
    results.append(k1)
    if not token:
        return results

    k2 = Check("K2", "키움 ka10001 현재가·PER·PBR·시총")
    t0 = time.monotonic()
    try:
        resp = await http.post(
            f"{base}/api/dostk/stkinfo",
            json={"stk_cd": KR_TICKER},
            headers={"authorization": f"Bearer {token}", "api-id": "ka10001"},
        )
        k2.elapsed_ms = (time.monotonic() - t0) * 1000
        payload = resp.json()
        rc = payload.get("return_code", 0)
        if rc in (0, "0", None):
            k2.ok(
                f"cur_prc={payload.get('cur_prc')} per={payload.get('per')} pbr={payload.get('pbr')} mac={payload.get('mac')}",
                {k: payload.get(k) for k in ("cur_prc", "per", "pbr", "eps", "bps", "mac")},
            )
        else:
            k2.fail(f"return_code={rc} ({payload.get('return_msg')})", payload)
    except Exception as exc:  # noqa: BLE001
        k2.elapsed_ms = (time.monotonic() - t0) * 1000
        k2.fail(f"{type(exc).__name__}: {exc}")
    results.append(k2)
    return results


# ── TOSS ─────────────────────────────────────────────────────────────────────
async def check_toss(http: httpx.AsyncClient) -> list[Check]:
    base = getenv("TOSS_API_BASE", "https://openapi.tossinvest.com").rstrip("/")
    client_id = getenv("TOSS_CLIENT_ID", "")
    client_secret = getenv("TOSS_CLIENT_SECRET", "")
    results: list[Check] = []

    t1 = Check("T1", "토스 토큰 발급")
    if not client_id or not client_secret:
        results.append(t1.fail("TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 비어 있음"))
        return results
    token = None
    t0 = time.monotonic()
    try:
        resp = await http.post(
            f"{base}/oauth2/token",
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        t1.elapsed_ms = (time.monotonic() - t0) * 1000
        payload = resp.json()
        token = payload.get("access_token") or payload.get("token")
        t1.ok(f"토큰 발급 성공 (len={len(token)})") if token else t1.fail(f"토큰 없음: {payload}", payload)
    except Exception as exc:  # noqa: BLE001
        t1.elapsed_ms = (time.monotonic() - t0) * 1000
        t1.fail(f"{type(exc).__name__}: {exc}")
    results.append(t1)
    if not token:
        return results

    auth = {"Authorization": f"Bearer {token}"}

    t2 = Check("T2", "토스 prices 현재가")
    t0 = time.monotonic()
    try:
        resp = await http.get(f"{base}/api/v1/prices", params={"symbols": KR_TICKER}, headers=auth)
        t2.elapsed_ms = (time.monotonic() - t0) * 1000
        if resp.status_code < 400:
            t2.ok("시세 응답 수신", resp.json())
        else:
            t2.fail(f"HTTP {resp.status_code}", resp.text[:300])
    except Exception as exc:  # noqa: BLE001
        t2.elapsed_ms = (time.monotonic() - t0) * 1000
        t2.fail(f"{type(exc).__name__}: {exc}")
    results.append(t2)

    t3 = Check("T3", "토스 환율 USD→KRW")
    t0 = time.monotonic()
    try:
        resp = await http.get(
            f"{base}/api/v1/exchange-rate",
            params={"baseCurrency": "USD", "quoteCurrency": "KRW"},
            headers=auth,
        )
        t3.elapsed_ms = (time.monotonic() - t0) * 1000
        if resp.status_code < 400:
            t3.ok("환율 응답 수신", resp.json())
        else:
            t3.fail(f"HTTP {resp.status_code}", resp.text[:300])
    except Exception as exc:  # noqa: BLE001
        t3.elapsed_ms = (time.monotonic() - t0) * 1000
        t3.fail(f"{type(exc).__name__}: {exc}")
    results.append(t3)
    return results


def _write_findings(groups: dict[str, list[Check]]) -> None:
    lines = [
        "# 3사 API 키 연동 스모크 — FINDINGS",
        "",
        f"- 실행 시각: {datetime.now().isoformat(timespec='seconds')}",
        f"- 샘플 종목: KR={KR_TICKER} (corp_code={CORP_CODE})",
        "",
        "| 제공자 | # | 항목 | 결과 | 소요(ms) | 비고 |",
        "|---|---|---|---|---|---|",
    ]
    for provider, checks in groups.items():
        for c in checks:
            mark = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "SKIP": "⏭ SKIP"}[c.status]
            detail = c.detail.replace("|", "\\|")
            lines.append(f"| {provider} | {c.code} | {c.title} | {mark} | {c.elapsed_ms:.0f} | {detail} |")
    lines.append("")
    for provider, checks in groups.items():
        for c in checks:
            if c.sample:
                lines += [f"### {provider} {c.code}. {c.title} — {c.status}", "", "```json", c.sample, "```", ""]
    FINDINGS_PATH.write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    _load_env()
    async with httpx.AsyncClient(timeout=15) as http:
        dart, kiwoom, toss = await asyncio.gather(
            check_dart(http), check_kiwoom(http), check_toss(http)
        )
    groups = {"DART": dart, "KIWOOM": kiwoom, "TOSS": toss}

    print(f"\n{'제공자':6} {'#':3} {'항목':36} 결과   소요")
    print("-" * 70)
    all_checks: list[Check] = []
    for provider, checks in groups.items():
        for c in checks:
            all_checks.append(c)
            print(f"{provider:6} {c.code:3} {c.title[:36]:36} {c.status:5} {c.elapsed_ms:6.0f}ms")
            if c.detail:
                print(f"       └─ {c.detail}")
    _write_findings(groups)
    print(f"\nFINDINGS → {FINDINGS_PATH}")

    failed = [c for c in all_checks if c.status == "FAIL"]
    passed = [c for c in all_checks if c.status == "PASS"]
    print(f"\n요약: PASS {len(passed)} / FAIL {len(failed)} / 전체 {len(all_checks)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
