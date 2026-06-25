"""
jasoseol.py
자소설닷컴(앵커리어) 채용공고 크롤러 — 공개 JSON API 기반(Selenium 불필요)

엔드포인트(공개·무인증, robots Allow 경로):
    GET https://jasoseol.com/api/v1/employment_companies?page=N   (페이지당 30건)
    └ 현재 열려 있는 채용공고 목록. 실명 회사명·제목·시작/마감일·직군·지원자수 제공.

수집 성격(중요):
    이 API는 사람인/잡코리아와 마찬가지로 **현재 열린 공고 스냅샷**만 노출한다
    (page≈5에서 소진, 과거/마감 공고 backfill 엔드포인트는 공개 API에 없음).
    → 따라서 본 크롤러는 "지금 열린 공고"를 수집한다. 주기 실행으로 시계열을 **누적**하는
      용도이며, 과거치 일괄 수집은 불가(자소설닷컴 과거 캘린더는 앱/큐레이션 기능).

개인정보 비수집: 자소서/이력서/지원자 식별정보는 절대 수집하지 않는다. 공개된 공고 메타
(회사명·제목·일정·직군)만 사용한다.

표준 레코드 키는 base_site.BaseSiteCrawler 규약을 따른다.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
_API = "https://jasoseol.com/api/v1/employment_companies"
_HEADERS = {
    "Accept": "application/json",
    "Referer": "https://jasoseol.com/recruit",
}
_PAGE_CAP = 30          # 안전 상한(현재 ~5페이지면 소진). 무한루프 방지.
_PAGE_PAUSE_SEC = 0.5   # 페이지 간 예의 대기(rate-limit)
_FETCH_PAUSE_SEC = 0.2  # 일일 crawl 에서 매칭 공고 상세 fetch 사이 예의 대기
_CACHE_TTL = timedelta(minutes=10)

# ── 런 단위 모듈 캐시 ────────────────────────────────────────────────────────
# multi_source_crawler 는 포털 크롤러를 기업마다 재생성하므로, 인스턴스 캐시는
# 매 기업 fetch 를 유발한다. 모듈 레벨 캐시로 "런당 1회 fetch + 기업별 필터"를 보장한다.
_cache: list[dict] | None = None
_cache_at: datetime | None = None


def _fetch_all_postings() -> list[dict]:
    """employment_companies 전 페이지를 모아 반환(현재 열린 공고). http.get 재사용."""
    try:
        from ..http import get as http_get
    except ImportError:
        from app.collectors.hiring.sites.http import get as http_get  # type: ignore

    postings: list[dict] = []
    for page in range(1, _PAGE_CAP + 1):
        try:
            resp = http_get(_API, headers=_HEADERS, params={"page": page})
            batch = resp.json()
        except Exception as exc:
            logger.warning("자소설닷컴 fetch 실패(page=%d): %s", page, exc)
            break
        if not isinstance(batch, list) or not batch:
            break
        postings.extend(batch)
        if len(batch) < 30:   # 마지막 페이지
            break
        time.sleep(_PAGE_PAUSE_SEC)
    logger.info("자소설닷컴 현재 공고 %d건 수집", len(postings))
    return postings


def _get_postings(force: bool = False) -> list[dict]:
    """TTL 캐시된 전체 공고. 런 내 반복 호출 시 1회만 실제 fetch."""
    global _cache, _cache_at
    now = datetime.now(_KST)
    if (
        not force
        and _cache is not None
        and _cache_at is not None
        and now - _cache_at < _CACHE_TTL
    ):
        return _cache
    _cache = _fetch_all_postings()
    _cache_at = now
    return _cache


def reset_cache() -> None:
    """테스트/새 런에서 캐시 강제 무효화(현재공고 + 기업 디렉터리 + 직군 분류)."""
    global _cache, _cache_at, _directory, _directory_at, _duty_map, _duty_at
    _cache = None
    _cache_at = None
    _directory = None
    _directory_at = None
    _duty_map = None
    _duty_at = None


# ── 직군 분류(duty-groups) ───────────────────────────────────────────────────
# 자격요건 본문은 대개 이미지(<img>)라 NLP 불가. 대신 자소설은 공고를 174개 직군
# 분류로 태깅한다(employments[].duty_group_ids). 이를 직군명으로 해석해 "직군 수요"
# 신호로 쓴다(세부 기술 추출보다 정확·일관). 세부 기술은 이미지 OCR/회사사이트=후속.
_DUTY_API = "https://jasoseol.com/api/v1/duty-groups"
_duty_map: dict[int, str] | None = None
_duty_at: datetime | None = None


def _fetch_duty_taxonomy() -> dict[int, str]:
    """duty-groups → {id: 직군명}. 런당 1회(TTL 캐시)."""
    global _duty_map, _duty_at
    now = datetime.now(_KST)
    if _duty_map is not None and _duty_at is not None and now - _duty_at < _CACHE_TTL:
        return _duty_map
    mapping: dict[int, str] = {}
    try:
        rows = _http_get_json(_DUTY_API)
    except Exception as exc:
        logger.warning("자소설닷컴 직군 분류 로드 실패: %s", exc)
        rows = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and isinstance(r.get("id"), int) and r.get("name"):
            mapping[r["id"]] = r["name"]
    _duty_map, _duty_at = mapping, now
    return _duty_map


def _duty_info(posting: dict) -> tuple[list[int], list[str]]:
    """공고 employments[].duty_group_ids → (id목록, 직군명목록) 중복제거·순서유지."""
    ids: list[int] = []
    for e in posting.get("employments") or []:
        if isinstance(e, dict):
            ids.extend(g for g in (e.get("duty_group_ids") or []) if isinstance(g, int))
    ids = list(dict.fromkeys(ids))
    if not ids:
        return [], []
    tax = _fetch_duty_taxonomy()
    names = [tax[g] for g in ids if g in tax]
    return ids, names


# ── 과거 이력 수집(채용 달력) ────────────────────────────────────────────────
# 목록 API는 현재 공고만 주지만, 회사별 이력 엔드포인트는 과거(마감) 공고까지 준다:
#   GET /api/v1/company_groups?all=true                      → 전체 기업 디렉터리(name→cg_id)
#   GET /api/v1/company_groups/{cg_id}/employment_companies  → 그 회사의 과거~현재 공고
# 무차별 id 순회 없이 "디렉터리 1회 + 회사당 1요청"으로 가볍게 과거를 수집한다.
_GROUPS_API = "https://jasoseol.com/api/v1/company_groups"
_directory: dict[str, int] | None = None      # {정규화이름/별칭: cg_id}
_directory_at: datetime | None = None


def _http_get_json(url: str, params: dict | None = None):
    """http.get(재시도/UA로테이션) 으로 JSON 반환."""
    try:
        from ..http import get as http_get
    except ImportError:
        from app.collectors.hiring.sites.http import get as http_get  # type: ignore
    return http_get(url, headers=_HEADERS, params=params).json()


def _fetch_directory() -> dict[str, int]:
    """company_groups?all=true → {정규화이름/별칭: cg_id}. 런당 1회(TTL 캐시)."""
    global _directory, _directory_at
    now = datetime.now(_KST)
    if (
        _directory is not None
        and _directory_at is not None
        and now - _directory_at < _CACHE_TTL
    ):
        return _directory
    mapping: dict[str, int] = {}
    try:
        rows = _http_get_json(_GROUPS_API, {"all": "true"})
    except Exception as exc:
        logger.warning("자소설닷컴 기업 디렉터리 로드 실패: %s", exc)
        rows = []
    for row in rows if isinstance(rows, list) else []:
        cg_id = row.get("id")
        if cg_id is None:
            continue
        for nm in [row.get("name"), *(row.get("alternate_names") or [])]:
            key = _norm(nm or "")
            if key:
                mapping.setdefault(key, cg_id)   # 첫 매칭 우선(별칭 충돌 시 본명 우선되도록 name 먼저)
    logger.info("자소설닷컴 기업 디렉터리 %d개 로드", len(mapping))
    _directory, _directory_at = mapping, now
    return _directory


def _resolve_cg_id(company_name: str) -> int | None:
    """회사명 → company_group_id (정규화 정확일치). 미발견 시 None."""
    return _fetch_directory().get(_norm(company_name))


def _fetch_company_history(cg_id: int) -> list[dict]:
    """company_groups/{cg}/employment_companies → 과거~현재 공고 목록(최근 ~2년 캡)."""
    url = f"{_GROUPS_API}/{cg_id}/employment_companies"
    try:
        data = _http_get_json(url)
    except Exception as exc:
        logger.warning("자소설닷컴 이력 fetch 실패(cg=%s): %s", cg_id, exc)
        return []
    return data if isinstance(data, list) else []


# ── 깊은 backfill (id 범위 스캔) ──────────────────────────────────────────────
# 회사별 엔드포인트는 최근 ~2년만 준다(하드캡). 더 과거(예: 2020)는 개별 상세
#   GET /api/v1/employment_companies/{id}
# 로만 접근된다. id 는 시간순 단조정수(예: ~2017=id20k, 2020=id~37k, 2026=id~105k)지만
# 삭제/비공개 **갭(None)** 이 섞여 있다. 경계 id 와 최대 id 는 **라이브로 발견**하고
# (하드코딩 금지), since 날짜는 호출자(스크립트/설정)가 주입한다.


def _fetch_detail(pid: int) -> dict | None:
    """employment_companies/{id} 상세 1건. 없는 id(갭)·오류는 None."""
    try:
        d = _http_get_json(f"{_API}/{pid}")
    except Exception:
        return None
    return d if isinstance(d, dict) and d.get("id") else None


def _detail_date(detail: dict) -> str | None:
    """공고의 게시 시점(start_time 우선, 없으면 created_at)."""
    return detail.get("start_time") or detail.get("created_at")


_IMG_SRC = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)


def _image_urls(posting: dict) -> list[str]:
    """상세 content(HTML) 내 **모든** ``<img src>`` 를 등장 순서대로 추출 — OCR
    enrichment(#375 Phase 0) 입력원.

    한국 공채 자격요건은 대개 포스터 이미지라 ``content`` 가 ``<img>`` 뿐인 경우가 많다.
    **필터링·중복제거 없이 전부 보존**한다(노이즈 필터는 실제 샘플 확인 후 별도 추가 예정).
    개인정보(자소서/지원자)는 수집하지 않는다 — 자격요건 포스터 URL 만. content 없으면 [].
    """
    content = posting.get("content")
    if not isinstance(content, str) or not content:
        return []
    return [url for m in _IMG_SRC.finditer(content) if (url := m.group(1).strip())]


def find_max_id() -> int:
    """현재 열린 공고 목록의 최대 id = 최신 경계(동적). 실패 시 0."""
    ids = [p.get("id") for p in _get_postings() if isinstance(p.get("id"), int)]
    return max(ids) if ids else 0


def _probe_live(pid: int, lo: int, hi: int, span: int = 60) -> tuple[int, str | None] | None:
    """pid 근처에서 살아있는 id 1개를 찾아 (id, 게시일) 반환. 갭 보정용. 못 찾으면 None."""
    detail = _fetch_detail(pid)
    if detail:
        return pid, _detail_date(detail)
    for delta in range(1, span + 1):
        for cand in (pid + delta, pid - delta):
            if lo <= cand <= hi:
                detail = _fetch_detail(cand)
                if detail:
                    return cand, _detail_date(detail)
    return None


def find_boundary_id(since_date: str, *, lo: int = 1, hi: int | None = None) -> int:
    """게시일 >= since_date 인 **최소 id**를 이진탐색(갭 허용). 경계가 모호하면 보수적으로 작은 id.

    since_date: 'YYYY-MM-DD'(또는 더 긴 ISO). 라이브 발견이라 하드코딩된 id 없음.
    """
    hi = hi if hi is not None else find_max_id()
    since = since_date[:10]
    boundary = hi
    while lo <= hi:
        mid = (lo + hi) // 2
        probe = _probe_live(mid, lo, hi)
        if probe is None:
            break  # 죽은 구간 — 더 좁히지 않음(보수적으로 현재 boundary 유지)
        pid, date = probe
        if (date or "")[:10] >= since:
            boundary = min(boundary, pid)
            hi = pid - 1
        else:
            lo = pid + 1
    return boundary


def iter_backfill_details(
    since_date: str,
    *,
    start_id: int | None = None,
    max_id: int | None = None,
    pause_sec: float = 0.3,
    max_requests: int | None = None,
):
    """[경계..최대] id 를 훑어 since_date 이후 공고 상세를 (id, detail) 로 yield.

    - 경계/최대 id 는 라이브 발견(미지정 시). 갭(None)·오류는 건너뜀.
    - pause_sec 로 예의 rate-limit(대량 스캔이므로 보수적으로). max_requests 안전 상한.
    - 재개: start_id 를 직전 진행 지점으로 주면 이어서 스캔.
    """
    top = max_id if max_id is not None else find_max_id()
    if top <= 0:
        logger.warning("자소설닷컴 backfill: 최대 id 발견 실패 — 중단")
        return
    start = start_id if start_id is not None else find_boundary_id(since_date, hi=top)
    since = since_date[:10]
    logger.info("자소설닷컴 backfill 스캔: id %d..%d (since=%s)", start, top, since)

    reqs = 0
    for pid in range(start, top + 1):
        if max_requests is not None and reqs >= max_requests:
            logger.info("자소설닷컴 backfill: max_requests(%d) 도달 — 중단(재개 start_id=%d)",
                        max_requests, pid)
            break
        detail = _fetch_detail(pid)
        reqs += 1
        if pause_sec:
            time.sleep(pause_sec)
        if not detail:
            continue
        d = _detail_date(detail)
        if d and d[:10] < since:   # 단조성상 드물지만 방어
            continue
        yield pid, detail


# ── 기간 열거 backfill (calendar_list) ───────────────────────────────────────
# id-scan(개별 상세 6~7만건)보다 훨씬 가벼운 경로: 채용 캘린더 목록 엔드포인트는
#   POST /employment/calendar_list.json  {"start_time","end_time"}
# 로 **해당 기간의 공고 목록을 한 번에** 반환한다(2020~ 과거 포함). 목록이라 포스터
# content 는 없고(image_file_name=로고/배너), 타깃 매칭분만 _fetch_detail 로 포스터를 보강한다.
_CALENDAR_API = "https://jasoseol.com/employment/calendar_list.json"


def _fetch_calendar(start_iso: str, end_iso: str) -> list[dict]:
    """calendar_list.json POST → 기간 내 공고 목록(employment). 실패/형식이상 시 []."""
    try:
        from ..http import post as http_post
    except ImportError:
        from app.collectors.hiring.sites.http import post as http_post  # type: ignore
    try:
        data = http_post(
            _CALENDAR_API, json={"start_time": start_iso, "end_time": end_iso}, headers=_HEADERS
        ).json()
    except Exception as exc:
        logger.warning("자소설닷컴 캘린더 fetch 실패(%s~%s): %s", start_iso[:10], end_iso[:10], exc)
        return []
    emp = data.get("employment") if isinstance(data, dict) else None
    return emp if isinstance(emp, list) else []


def _month_windows(since_date: str, until_date: str):
    """[since, until] 을 월 단위 [월초, 다음월초) 윈도우(UTC ISO 'Z')로 yield. 인접·비오버랩."""
    y, m = int(since_date[:4]), int(since_date[5:7])
    end_y, end_m = int(until_date[:4]), int(until_date[5:7])
    while (y, m) <= (end_y, end_m):
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        yield f"{y:04d}-{m:02d}-01T00:00:00.000Z", f"{ny:04d}-{nm:02d}-01T00:00:00.000Z"
        y, m = ny, nm


def iter_calendar_postings(
    since_date: str, until_date: str, *, pause_sec: float = 0.2
):
    """since~until 을 월별로 열거해 공고 목록 posting 을 yield(id 로 전역 dedup).

    - 목록 데이터(포스터 content 없음). 호출부가 타깃 필터 후 _fetch_detail 로 포스터 보강.
    - 월 경계는 [월초, 다음월초) 정확 분할이라 오버랩 불필요. 경계상 동일 공고 재등장은 id dedup 이 흡수.
    """
    seen: set = set()
    for start_iso, end_iso in _month_windows(since_date, until_date):
        batch = _fetch_calendar(start_iso, end_iso)
        for posting in batch:
            pid = posting.get("id") if isinstance(posting, dict) else None
            if pid is None or pid in seen:
                continue
            seen.add(pid)
            yield posting
        if pause_sec:
            time.sleep(pause_sec)


def _norm(name: str) -> str:
    """회사명 정규화: 소문자·공백/괄호/법인접미사 제거 → 느슨한 매칭용."""
    s = (name or "").lower()
    # 괄호와 그 안 내용 제거: "주택도시보증공사(hug)" → "주택도시보증공사"
    out, depth = [], 0
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    s = "".join(out)
    for token in ("주식회사", "(주)", "㈜", " "):
        s = s.replace(token, "")
    return s.strip()


class JasoseolCrawler(BaseSiteCrawler):
    """자소설닷컴 공개 API 크롤러(포털형: 전체 fetch 후 기업명 필터)."""

    source_label = "JASOSEOL"

    def crawl(self, company_name: str) -> list[dict]:
        target = _norm(company_name)
        if not target:
            return []

        records: list[dict] = []
        for posting in _get_postings():
            src_name = posting.get("name") or ""
            cand = _norm(src_name)
            if not cand:
                continue
            # 정확일치는 길이 무관 허용(2글자 회사명: 안랩·KT·SK 등). 부분일치는
            # 짧은 이름 오매칭 방지로 한쪽이 3자 이상일 때만(예: 'LG전자' vs 'LG생활건강' 비매칭).
            if not (
                cand == target
                or (len(target) >= 3 and target in cand)
                or (len(cand) >= 3 and cand in target)
            ):
                continue
            # 목록 API 에는 포스터가 없으므로(image_url 은 로고/배너), 매칭된 공고만
            # 상세를 fetch 해 content(포스터 <img>)를 확보 → image_urls 캡처 + 실제 게시일 보존.
            # 마감/오류 시 _fetch_detail 은 None → 목록 posting 으로 폴백(이미지 없이 레코드 유지).
            detail = _fetch_detail(posting["id"])
            detail = detail if detail else posting
            rec = self._to_record(
                company_name, src_name, detail,
                posting_date=_detail_date(detail),
            )
            if rec:
                records.append(rec)
            time.sleep(_FETCH_PAUSE_SEC)   # 매칭분만 fetch — 예의 대기(상한 없음)

        if records:
            logger.info("자소설닷컴 [%s]: %d건", company_name, len(records))
        return records

    def crawl_history(self, company_name: str, since_year: int | None = None) -> list[dict]:
        """채용 달력 기반 **과거~현재 공고 이력** 수집(마감된 과거 공고 포함).

        company_groups?all=true 디렉터리로 cg_id 해석 → 회사별 이력 엔드포인트 1요청.
        현재공고 crawl()과 달리 posting_date 를 **실제 게시일(start_time/created_at)**로 채워
        과거 시점 그대로의 시계열을 보존한다(누적 backfill 용).

        Args:
            company_name: 대상 기업명(우리 종목명).
            since_year:   이 연도 이전(start_time 기준) 공고는 제외(선택).
        """
        cg_id = _resolve_cg_id(company_name)
        if cg_id is None:
            logger.info("자소설닷컴 이력: '%s' 디렉터리 미발견", company_name)
            return []

        records: list[dict] = []
        for posting in _fetch_company_history(cg_id):
            opened = posting.get("start_time") or posting.get("created_at")
            if since_year and opened and opened[:4].isdigit() and int(opened[:4]) < since_year:
                continue
            src_name = posting.get("name") or company_name
            rec = self._to_record(company_name, src_name, posting, posting_date=opened)
            if rec:
                records.append(rec)

        if records:
            logger.info("자소설닷컴 이력 [%s, cg=%d]: %d건", company_name, cg_id, len(records))
        return records

    def _to_record(
        self,
        query_company: str,
        src_name: str,
        posting: dict,
        *,
        posting_date: str | None = None,
    ) -> dict | None:
        title = (posting.get("title") or "").strip()
        pid = posting.get("id")
        if not title or pid is None:
            return None

        # 직군 목록(employments[].field) → 설명/기술추출 시드. 개인정보 없음.
        fields = [
            (e.get("field") or "").strip()
            for e in (posting.get("employments") or [])
            if isinstance(e, dict) and e.get("field")
        ]
        description = " / ".join(dict.fromkeys(fields)) or None  # 중복 제거·순서 유지

        record = self._make_record(
            company_name=src_name,        # 자소설 실명 그대로(매칭 대상은 _resolve_stock)
            job_title=title,
            source_url=f"https://jasoseol.com/recruit/{pid}",
            job_description=description,
            closing_date=posting.get("end_time"),   # 원문 ISO 그대로(없으면 None)
            tech_stack=self.extract_tech(f"{title} {description or ''}"),
        )
        # 안정적 dedup 시드(공고 id 기준) — 재실행 시 source_hash 일치.
        record["unique_key"] = f"JASOSEOL|{pid}"
        # 과거 이력 수집 시 실제 게시일을 보존(없으면 now() 기본값 유지).
        if posting_date:
            record["posting_date"] = posting_date
        # 직군 수요 신호: duty-groups 태그 → 직군명(이미지 자격요건을 대체하는 구조화 신호).
        duty_ids, duty_names = _duty_info(posting)
        record["duty_group_ids"] = duty_ids
        record["duty_groups"] = duty_names
        # 세부 기술 후속 보강용 원천 보존(이미지 OCR/회사사이트). 분석 단계에서 선택 사용.
        record["employment_page_url"] = posting.get("employment_page_url")
        # #375 Phase 0: 자격요건 포스터 이미지 URL 보존(OCR enrichment 입력). content 없으면 [].
        record["image_urls"] = _image_urls(posting)
        return record
