"""채용 DataLab 키워드 그룹 생성기 — 순수 로직, DB 호출 없음."""
from __future__ import annotations


class HiringKeywordGenerator:
    """
    기업명 → 네이버 DataLab API 키워드 그룹 변환.

    외부에서 기업 리스트를 주입받으며 DB에 직접 접근하지 않는다.

    네이버 DataLab API 규격 (keywordGroups 항목):
        {
            "groupName": str,
            "keywords":  list[str]   ← 순수 문자열 배열 ({"name": ...} 형태 아님)
        }
    """

    # 15개 크롤링 대상 기업 중 단축 검색어가 의미 있는 8개만 등록.
    # 나머지 7개(카카오/크래프톤/기아/한미반도체/스튜디오드래곤/셀트리온/유한양행)는
    # 이미 짧거나 통용 약칭이 없으므로 full name 4개 키워드만 생성된다.
    _SHORT_NAME_MAP: dict[str, str] = {
        "삼성전자":         "삼성",
        "SK하이닉스":       "하이닉스",
        "삼성바이오로직스": "삼성바이오",
        "SM엔터테인먼트":   "SM",
        "현대자동차":       "현대",
        "NAVER":            "네이버",
        "HYBE":             "하이브",
        "HL만도":           "만도",
    }

    def generate_keyword_group(
        self, company_name: str, category: str = "tech"
    ) -> dict:
        """
        단일 기업명 → DataLab keywordGroup dict 반환.

        Returns:
            {
                "groupName":     "삼성전자_HIRING_TREND",
                "keywords":      ["삼성전자 채용", "삼성전자 공채", ...],  # list[str]
                "keyword_count": 5,
                "category":      "tech"
            }

        Raises:
            ValueError: company_name이 빈 문자열일 때
        """
        if not company_name or not company_name.strip():
            raise ValueError("company_name은 빈 문자열일 수 없습니다.")

        keywords = self._build_keywords(company_name)
        return {
            "groupName":     f"{company_name}_HIRING_TREND",
            "keywords":      keywords,           # ← list[str]: 네이버 API 규격
            "keyword_count": len(keywords),
            "category":      category,
        }

    def generate_for_multiple_companies(
        self,
        companies: list[dict],
    ) -> dict[str, dict]:
        """
        외부에서 주입된 기업 리스트로 일괄 키워드 그룹 생성 (DB 호출 없음).

        Args:
            companies: [
                {"company_id": 1, "company_name": "삼성전자", "category": "반도체"},
                ...
            ]
            company_name 키가 없거나 빈 값인 항목은 건너뜀.
            category 키가 없으면 기본값 "tech" 사용.

        Returns:
            {"삼성전자": {"groupName": ..., "keywords": [...], ...}, ...}
        """
        result: dict[str, dict] = {}
        for company in companies:
            name = company.get("company_name", "")
            if not name or not name.strip():
                continue
            category = company.get("category", "tech")
            result[name] = self.generate_keyword_group(name, category=category)
        return result

    def _build_keywords(self, company_name: str) -> list[str]:
        """full name 4개 + short name 채용 1개(해당하는 경우). 순수 문자열 반환."""
        keywords = [
            f"{company_name} 채용",
            f"{company_name} 공채",
            f"{company_name} 입사",
            f"{company_name} 채용공고",
        ]
        short = self._SHORT_NAME_MAP.get(company_name)
        if short and short != company_name:
            keywords.append(f"{short} 채용")
        return keywords
