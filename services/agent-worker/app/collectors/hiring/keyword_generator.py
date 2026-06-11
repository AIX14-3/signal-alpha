"""채용 DataLab 키워드 그룹 생성기 — 순수 로직, DB 호출 없음."""
from __future__ import annotations


class HiringKeywordGenerator:
    """
    기업명 → 네이버 DataLab API 키워드 그룹 변환.

    내부에 하드코딩된 데이터가 전혀 없는 Pure 데이터 프로세서.
    약칭(short_name)은 외부(DB의 stocks.short_name 컬럼)에서 주입받는다.
    기업 추가·변경 시 코드 수정 없이 DB만 업데이트하면 된다.

    네이버 DataLab API 규격 (keywordGroups 항목):
        {
            "groupName": str,
            "keywords":  list[str]   ← 순수 문자열 배열 ({"name": ...} 형태 아님)
        }
    """

    def generate_keyword_group(
        self,
        company_name: str,
        category: str = "tech",
        short_name: str | None = None,
    ) -> dict:
        """
        단일 기업명 → DataLab keywordGroup dict 반환.

        Args:
            company_name: 기업명 (예: "삼성전자")
            category:     섹터 레이블 (기본값 "tech")
            short_name:   DB stocks.short_name 에서 주입된 약칭 (예: "삼성").
                          None 또는 company_name 과 동일하면 약칭 키워드 미추가.

        Returns:
            {
                "groupName":     "삼성전자_HIRING_TREND",
                "keywords":      ["삼성전자 채용", "삼성전자 공채", ...],  # list[str]
                "keyword_count": 5,
                "category":      "반도체"
            }

        Raises:
            ValueError: company_name이 빈 문자열일 때
        """
        if not company_name or not company_name.strip():
            raise ValueError("company_name은 빈 문자열일 수 없습니다.")

        keywords = self._build_keywords(company_name, short_name)
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
                {
                    "company_name": "삼성전자",
                    "category":     "반도체",       # 없으면 "tech"
                    "short_name":   "삼성",          # DB stocks.short_name, 없으면 None
                },
                ...
            ]
            company_name 키가 없거나 빈 값인 항목은 건너뜀.

        Returns:
            {"삼성전자": {"groupName": ..., "keywords": [...], ...}, ...}
        """
        result: dict[str, dict] = {}
        for company in companies:
            name = company.get("company_name", "")
            if not name or not name.strip():
                continue
            category = company.get("category", "tech")
            short_name = company.get("short_name")      # DB 주입값, 없으면 None
            result[name] = self.generate_keyword_group(
                name, category=category, short_name=short_name
            )
        return result

    def _build_keywords(self, company_name: str, short_name: str | None = None) -> list[str]:
        """full name 4개 + 외부 주입 약칭 채용 1개(해당하는 경우). 순수 문자열 반환."""
        keywords = [
            f"{company_name} 채용",
            f"{company_name} 공채",
            f"{company_name} 입사",
            f"{company_name} 채용공고",
        ]
        if short_name and short_name.strip() and short_name != company_name:
            keywords.append(f"{short_name} 채용")
        return keywords
