"""잡코리아 회원번호(기업 고유 ID) 발굴 — `seeds/007_seed_jobkorea_company_ids.sql` 확장용.

배경
----
잡코리아 수집에는 두 경로가 있다(`app/collectors/hiring/sites/jobkorea.py`):

  (a) 회원번호 직접수집 `crawl_by_member_id()` — `/Recruit/Co_Read/Recruit/C/<회원번호>`
      서버렌더 페이지를 공용 http.py 로 GET. **Selenium 불요·DOM 개편 내성·협력사 노이즈 0.**
  (b) 키워드 검색 `crawl()` — Selenium 필요. 협력사·대리점·자회사 공고를 대거 끌어온다.

(a)는 `hiring_portal_company_ids` 에 매핑이 있는 종목만 쓴다. 매핑이 없으면 (b)로 폴백한다.
이 스크립트는 매핑 후보를 **라이브로 발굴·검증**해 시드에 넣을 값을 뽑는다.

회원번호는 어디서 나오나
--------------------
검색결과 페이지의 기업 링크(`<a href="/Recruit/Co_Read/C/<id>">`)를 긁는 방법은 **못 쓴다.**
실측(2026-07-09) 결과 그 링크에는 협력사만 걸리고 정작 본 법인이 안 나온다 — '네이버' 검색 시
링크로 잡히는 건 `NAVER I&S`, `네이버 웹툰` 뿐이고 본체(21572628)는 링크가 없다.

대신 검색결과 페이지에 **공고별 임베디드 JSON**이 실려 있고, 거기에 공고 게시 법인과 그 회원번호가
직접 들어 있다:

    "postingCompanyName":"㈜NAVER","userRefType":"C","memberSystemNo":"21572628"

이 쌍을 뽑아 `postingCompanyName` 이 종목명과 정규화 완전일치할 때만 채택한다.

왜 정확매칭이 필수인가
--------------------
잡코리아 검색은 사명이 부분 포함되기만 해도 걸린다. 실측('크래프톤'/'기아'):
    더풋샵 판교알파돔타워점(크래프톤 타워점)  ← 무관한 가맹점
    ㈜블루홀스튜디오                          ← 자회사
    ㈔한국국제 기아 대책기구 / ㈜하남 기아     ← 완전 무관
따라서 **정규화 후 완전일치**하는 법인만 채택한다.

한계 (중요)
----------
회원번호는 **검색결과에 실린 공고**에서 뽑으므로, 그 종목의 진행공고가 검색 첫 페이지에 없으면
발굴되지 않는다("MISS"). MISS 는 "회원번호가 없다"가 아니라 "지금 이 검색으로는 못 찾았다"는 뜻이다.
실제로 SK하이닉스(21493847)는 시드에 있는데도 이 방법으로는 MISS 로 나온다. 따라서
**이미 시드에 있는 매핑을 이 스크립트 결과로 지우면 안 된다.** 추가 전용 도구다.

동작
----
읽기 전용이다. **DB에 아무것도 쓰지 않는다.** 종목별로:
  1. `/Search/?stext=<검색어>` (+ `&tabType=corp`) 페이지의 임베디드 JSON에서
     `(회사명, userRefType, 회원번호)` 삼중항 수집. `userRefType='C'`(법인)만 취한다.
  2. 정규화 완전일치(= stocks.name 또는 stocks.short_name)인 후보만 채택
  3. 채택 후보의 공고 리스트를 GET 해 **크롤러의 실제 파서**(`_parse_member_list`)로 파싱
     → 진행공고 수 확인. 0건이거나 파서가 None(DOM 깨짐)이면 채택하지 않는다.
  4. 결과 표 + 시드에 붙일 SQL `VALUES` 스니펫 출력

한 종목에 회원번호가 둘 이상 나올 수 있다(카카오: 22185402·7746 — 공고 29건 집합이 완전히 동일).
그때는 검색 JSON 등장 빈도가 높은 쪽이 현행 계정이다. 스크립트는 전부 출력하고 사람이 고른다.

검색어·검증 파서·HTTP 계층을 전부 프로덕션 코드에서 재사용한다(파서 이중 구현 금지):
  - `sites.http.get`            retry / UA 로테이션 / 429·403 적응형 백오프 / 차단 신호 센서
  - `JobkoreaCrawler._parse_member_list`  순수 파서(네트워크 없음)

사용
----
    cd services/agent-worker   # http.py 가 app.core.config 를 import 하므로
    uv run python ../../scripts/discover_jobkorea_member_ids.py                 # is_target 전 종목
    uv run python ../../scripts/discover_jobkorea_member_ids.py --company 크래프톤 --company 카카오
    uv run python ../../scripts/discover_jobkorea_member_ids.py --show-rejected  # 탈락 후보까지

주의
----
외부 사이트에 종목 수 × 검색어 수만큼 요청한다. `--delay` 로 간격을 조절한다(기본 2초,
`multi_source_crawler` 의 rate_limit 과 동일).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = REPO_ROOT / "services" / "agent-worker"
sys.path.insert(0, str(AGENT_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from app.collectors.hiring.sites import http  # noqa: E402
from app.collectors.hiring.sites.jobkorea import JobkoreaCrawler  # noqa: E402

_BASE = "https://www.jobkorea.co.kr"
# 두 탭 모두 훑는다 — 임베디드 JSON 의 공고 집합이 탭마다 다르다.
_SEARCH_TABS = (f"{_BASE}/Search/?stext={{term}}", f"{_BASE}/Search/?stext={{term}}&tabType=corp")
_COMPANY_PAGE = f"{_BASE}/Recruit/Co_Read/C/{{cid}}"
_MEMBER_LIST = f"{_BASE}/Recruit/Co_Read/Recruit/C/{{cid}}"

# 검색결과 페이지에 삽입된 공고 JSON(백슬래시로 이스케이프된 상태). 게시 법인명 ↔ 회원번호 쌍.
#   "postingCompanyName":"㈜NAVER","userRefType":"C","memberSystemNo":"21572628"
# userRefType: 'C' = 법인(회원번호), 그 외(개인/헤드헌터)는 버린다.
_POSTING_TRIO = re.compile(
    r'postingCompanyName\\":\\"(.*?)\\",\\"userRefType\\":\\"([A-Z])\\",\\"memberSystemNo\\":\\"(\d+)\\"'
)

# 법인격 표기 — 매칭 전 제거. base_collector._clean_company_name 과 같은 목록 + 잡코리아 표기.
_LEGAL_FORMS = ("(주)", "주식회사", "㈜", "(유)", "(재)", "(사)", "유한회사")


def normalize(name: str) -> str:
    """매칭용 정규화: 법인격·공백 제거 + 소문자. '㈜ 카카오' == '카카오'."""
    out = (name or "").strip()
    for form in _LEGAL_FORMS:
        out = out.replace(form, "")
    return "".join(out.split()).lower()


def load_targets(database_url: str) -> list[tuple[str, str, list[str]]]:
    """[(ticker, canonical_name, [검색어...])] — is_target 종목.

    canonical 은 `stocks.name`. `_load_jobkorea_company_ids` 가 이 이름을 키로 매핑을 읽고,
    `collect()` 가 종목 그룹의 canonical 로 조회하므로 반드시 일치해야 한다.
    검색어는 name + short_name(별칭) — '네이버' 는 short_name 으로만 잡힌다.
    """
    import psycopg2

    with psycopg2.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, name, short_name FROM stocks "
            "WHERE is_target = TRUE ORDER BY ticker"
        )
        rows = cur.fetchall()

    targets = []
    for ticker, name, short_name in rows:
        terms = [t for t in (name, short_name) if t]
        targets.append((ticker, name, terms))
    return targets


def search_candidates(term: str, delay: float) -> dict[str, tuple[str, int]]:
    """검색어 → {회원번호: (게시 법인명, 등장 공고 수)}.

    등장 공고 수는 같은 법인에 회원번호가 둘 이상 있을 때 현행 계정을 고르는 힌트다
    (카카오 실측: 22185402=19회 vs 7746=1회, 둘 다 같은 공고 29건을 반환).
    """
    found: dict[str, tuple[str, int]] = {}
    for tab in _SEARCH_TABS:
        resp = http.get(tab.format(term=term))
        for label, ref_type, cid in _POSTING_TRIO.findall(resp.text):
            if ref_type != "C":       # 법인 공고만 (개인·헤드헌터 제외)
                continue
            prev_label, prev_count = found.get(cid, (label, 0))
            found[cid] = (prev_label, prev_count + 1)
        time.sleep(delay)
    return found


def count_open_jobs(cid: str, company_name: str) -> int | None:
    """회원번호의 진행공고 수. 크롤러의 실제 파서로 파싱한다.

    None = 파서가 DOM 깨짐으로 판정(호출부가 키워드 폴백하는 조건) → 시드에 넣으면 안 된다.
    """
    resp = http.get(_MEMBER_LIST.format(cid=cid), headers={"Referer": _COMPANY_PAGE.format(cid=cid)})
    jobs = JobkoreaCrawler(driver=None)._parse_member_list(resp.text, company_name)
    return None if jobs is None else len(jobs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--database-url", help="대상 DSN (기본: env DATABASE_URL)")
    parser.add_argument(
        "--company", action="append", dest="companies",
        help="이 종목만 조사(여러 번 지정 가능). 기본: is_target 전 종목",
    )
    parser.add_argument("--delay", type=float, default=2.0, help="요청 간 대기(초, 기본 2.0)")
    parser.add_argument("--show-rejected", action="store_true", help="탈락 후보도 전부 출력")
    args = parser.parse_args()

    dsn = args.database_url or os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("✗ DATABASE_URL 이 없습니다 (--database-url 또는 .env).")

    targets = load_targets(dsn)
    if args.companies:
        wanted = {normalize(c) for c in args.companies}
        targets = [t for t in targets if normalize(t[1]) in wanted]
        if not targets:
            raise SystemExit(f"✗ 일치하는 is_target 종목이 없습니다: {args.companies}")

    accepted: list[tuple[str, str, str, int]] = []   # (ticker, name, cid, open_jobs)
    unmatched: list[tuple[str, str]] = []            # (ticker, name)

    for ticker, name, terms in targets:
        print(f"\n[{ticker}] {name}  (검색어: {', '.join(terms)})")

        candidates: dict[str, tuple[str, int]] = {}
        for term in terms:
            try:
                for cid, (label, freq) in search_candidates(term, args.delay).items():
                    prev_label, prev_freq = candidates.get(cid, (label, 0))
                    candidates[cid] = (prev_label, prev_freq + freq)
            except Exception as exc:
                print(f"  ⚠️  검색 실패({term}): {exc}")

        if not candidates:
            print("  후보 0건 — 검색결과에 공고 없음(회원번호 부재와는 다름)")
            unmatched.append((ticker, name))
            continue

        wanted = {normalize(t) for t in terms}
        exact = {cid: v for cid, v in candidates.items() if normalize(v[0]) in wanted}

        if args.show_rejected:
            for cid, (label, freq) in candidates.items():
                if cid not in exact:
                    print(f"  ✗ 탈락 {cid:>10}  {label[:50]}  (공고 {freq}건)")

        if not exact:
            print(f"  정확매칭 0건 (후보 {len(candidates)}건은 전부 협력사·자회사·가맹점)")
            unmatched.append((ticker, name))
            continue

        # 등장 빈도 높은 순 — 회원번호가 둘 이상이면 현행 계정이 먼저 온다.
        picked = False
        for cid, (label, freq) in sorted(exact.items(), key=lambda kv: -kv[1][1]):
            try:
                open_jobs = count_open_jobs(cid, name)
            except Exception as exc:
                print(f"  ⚠️  {cid} 공고 조회 실패: {exc}")
                continue
            finally:
                time.sleep(args.delay)

            if open_jobs is None:
                print(f"  ✗ {cid:>10}  {label}  — 파서가 DOM 깨짐 판정(채택 불가)")
            elif open_jobs == 0:
                print(f"  ✗ {cid:>10}  {label}  — 진행공고 0건(채택 불가)")
            else:
                mark = "✓" if not picked else "·"   # 2번째부터는 중복 계정(참고용)
                print(f"  {mark} {cid:>10}  {label}  — 진행공고 {open_jobs}건, 검색 등장 {freq}회")
                if not picked:
                    accepted.append((ticker, name, cid, open_jobs))
                    picked = True

        if not picked:
            unmatched.append((ticker, name))

    # ── 요약 ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"채택 {len(accepted)}건 / 미매핑 {len(unmatched)}건")

    if unmatched:
        print("\n미매핑(키워드 검색 폴백 유지):")
        for ticker, name in unmatched:
            print(f"  {ticker}  {name}")
        print("  ※ 미매핑 = '이 검색으로 못 찾음'. 회원번호 부재를 뜻하지 않는다")
        print("     (SK하이닉스는 시드에 있는데도 MISS 로 나온다). 기존 시드를 지우지 말 것.")

    if accepted:
        print("\nseeds/007_seed_jobkorea_company_ids.sql 의 VALUES 에 붙일 행:\n")
        for ticker, name, cid, open_jobs in accepted:
            print(f"    ('{ticker}', 'JOBKOREA', '{cid}'),   -- {name} (진행공고 {open_jobs}건)")

    signals = http.block_signal_snapshot()
    if any(signals.values()):
        print(f"\n🚧 차단 신호: 403={signals['403']} 429={signals['429']} — 결과 신뢰도 확인 필요")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
