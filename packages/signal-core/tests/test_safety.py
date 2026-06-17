import pytest
from signal_core.safety import (
    AI_STYLE_PHRASES,
    DISCLAIMER,
    FORBIDDEN_PHRASES,
    INVESTMENT_RECOMMENDATION_PHRASES,
    SAFE_ALTERNATIVES,
    contains_forbidden,
    find_violations,
    sanitize,
)


class TestConstants:
    def test_forbidden_phrases_includes_both_categories(self):
        assert set(INVESTMENT_RECOMMENDATION_PHRASES).issubset(set(FORBIDDEN_PHRASES))
        assert set(AI_STYLE_PHRASES).issubset(set(FORBIDDEN_PHRASES))

    def test_no_duplicate_phrases(self):
        assert len(FORBIDDEN_PHRASES) == len(set(FORBIDDEN_PHRASES))

    def test_safe_alternatives_covers_all_forbidden(self):
        missing = [p for p in FORBIDDEN_PHRASES if p not in SAFE_ALTERNATIVES]
        assert missing == [], f"대체 표현 누락: {missing}"

    def test_disclaimer_is_not_empty(self):
        assert len(DISCLAIMER) > 0
        assert "투자 권유" in DISCLAIMER
        assert "AI 에이전트" in DISCLAIMER


class TestContainsForbidden:
    def test_detects_investment_recommendation(self):
        assert contains_forbidden("지금 삼성전자 매수하세요 좋은 기회입니다") is True

    def test_detects_ai_style_phrase(self):
        assert contains_forbidden("종합적으로 분석한 결과 긍정적인 흐름이 감지되었습니다") is True

    def test_clean_text_returns_false(self):
        clean = "SK하이닉스 HBM 채용 18건으로 전월 대비 240% 증가했고, 공시 3건 모두 긍정 방향입니다."
        assert contains_forbidden(clean) is False

    def test_empty_string_returns_false(self):
        assert contains_forbidden("") is False


class TestFindViolations:
    def test_returns_all_matching_phrases(self):
        text = "종합적으로 분석한 결과 매수하세요 그리고 추천합니다"
        violations = find_violations(text)
        assert "종합적으로" in violations
        assert "매수하세요" in violations
        assert "추천합니다" in violations

    def test_returns_empty_list_for_clean_text(self):
        clean = "리포트 목표가 평균 112,000원으로 현재가 대비 31.8% 상단입니다."
        assert find_violations(clean) == []

    def test_no_false_positives_on_partial_match(self):
        # "매수" 단독은 금지어 아님 — "매수하세요", "매수 추천" 등 조합만 금지
        text = "매수 거래량이 증가했습니다"
        violations = find_violations(text)
        assert "매수 추천" not in violations
        assert "매수하세요" not in violations


class TestSanitize:
    def test_replaces_investment_phrase(self):
        result = sanitize("지금 삼성전자 매수하세요")
        assert "매수하세요" not in result
        assert "데이터가 긍정 방향을 가리킵니다" in result

    def test_removes_ai_style_filler(self):
        result = sanitize("종합적으로 분석한 결과 전반적으로 긍정적입니다")
        assert "종합적으로" not in result
        assert "전반적으로" not in result

    def test_replaces_ai_style_phrase_with_neutral(self):
        result = sanitize("상승세가 감지되었습니다")
        assert "감지되었습니다" not in result
        assert "있습니다" in result

    def test_clean_text_unchanged(self):
        clean = "공시 3건 모두 긍정 방향입니다."
        assert sanitize(clean) == clean

    @pytest.mark.parametrize("phrase", list(INVESTMENT_RECOMMENDATION_PHRASES))
    def test_all_investment_phrases_are_sanitized(self, phrase: str):
        result = sanitize(f"테스트: {phrase}")
        assert phrase not in result

    @pytest.mark.parametrize("phrase", list(AI_STYLE_PHRASES))
    def test_all_ai_style_phrases_are_sanitized(self, phrase: str):
        result = sanitize(f"테스트: {phrase}")
        assert phrase not in result

    def test_longer_phrase_wins_over_substring(self):
        # "사세요" ⊂ "지금 사세요" — 긴 표현이 먼저 치환돼야 한다(dict 순서 무관 견고성).
        result = sanitize("지금 사세요")
        assert result == "복수 소스가 긍정 방향으로 수렴합니다"

    def test_overlapping_substring_phrases_all_removed(self):
        # 부분문자열 관계가 섞여도 원 금지 표현이 결과에 하나도 남지 않아야 한다.
        result = sanitize("지금 사세요 그리고 좋은 종목입니다")
        for phrase in ("지금 사세요", "사세요", "좋은 종목입니다", "좋은 종목"):
            assert phrase not in result
