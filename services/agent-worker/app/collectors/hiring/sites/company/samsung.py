"""
samsung.py
삼성전자 공식 채용 크롤러 — 통합 채용포털 안내(guidance-only)

구 careers.samsung.com REST API 는 폐기(DNS 死). 신 채용은
https://www.samsungcareers.com/hr/ (끝 슬래시 필수) 통합 포털로 이전됐는데,
이 포털은 **관계사 선택형 디렉터리**(관계사 26개, /subsid/detail/<CODE> 타일)이고
삼성전자 자체(DX부문 C10CAA / DS부문 C10CAH)는 flat 공고 목록을 노출하지 않으며
현재 자체 공고가 0건이다. 긁을 직무 목록이 없으므로 삼성바이오로직스와 동일하게
'채용 안내' 1건만 기록한다(공식 채용관 생존 신호). raw_html 은 CollectorResult 로
보존하여, 추후 전자 자체 공고가 노출되면 격리/reparse 기반 복구가 가능하다.
"""

from __future__ import annotations

import logging

from ..base_site import BaseSiteCrawler

logger = logging.getLogger(__name__)

_BASE = "https://www.samsungcareers.com"
_PORTAL = f"{_BASE}/hr/"


class SamsungCrawler(BaseSiteCrawler):
    source_label = "SAMSUNG_CAREERS"

    def crawl(self, company_name: str) -> list:
        """통합 채용포털 안내 1건 반환(원본 HTML best-effort 보존)."""
        raw_html: str | None = None
        try:
            from ..http import get as http_get

            # User-Agent는 http_get이 풀에서 로테이션 주입한다.
            raw_html = http_get(_PORTAL).text
        except Exception as exc:  # 포털 fetch 실패해도 안내는 그대로 기록
            logger.debug("삼성전자 포털 fetch 실패(안내만 기록): %s", exc)

        return self._parse_samsung_guidance(company_name, raw_html)

    def _parse_samsung_guidance(self, company: str, raw_html: str | None = None) -> list:
        """삼성전자 — '채용 안내' 1건만 반환 (#175 / #243).

        samsungcareers.com/hr/ 는 관계사 선택형 통합 포털이라 직무 목록을 직접 노출하지
        않는다(삼성전자 = DX부문 C10CAA / DS부문 C10CAH 타일). 전자 자체 공고도 현재 0건.
        메뉴/관계사 타일을 긁으면 공고로 오파싱되므로 안내 1건만 기록한다.
        raw_html 은 CollectorResult 로 보존(미래 전자 자체 공고 노출 시 reparse 복구).
        """
        from ...base_collector import CollectorResult

        return [CollectorResult(
            data=self._make_record(
                company,
                "삼성전자 채용 안내",
                _PORTAL,
                job_description=(
                    "삼성 통합 채용포털(관계사 선택형). 삼성전자(DX부문 C10CAA/DS부문 C10CAH)는 "
                    "현재 자체 공고 미노출. 실 공고는 포털/포털 경로 참조."
                ),
            ),
            raw_payload=raw_html,
            source_label=self.source_label,
        )]
