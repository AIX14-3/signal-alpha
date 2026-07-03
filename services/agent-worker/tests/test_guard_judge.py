import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.guard.gdelt import GuardArticle
from app.guard.judge import GeoRiskJudgment, GuardJudgeError, judge_articles, validate_judgment
from app.guard.prompts import GUARD_PROMPT_VERSION


def _valid_payload(**overrides):
    payload = {
        "severity": 88,
        "is_geopolitical_risk": True,
        "direction": "escalation",
        "summary": "이란-미국 무력 충돌 확전, 호르무즈 해협 봉쇄 우려.",
        "regions": ["Middle East", "Iran", "US"],
        "affected_themes": ["oil", "defense"],
        "confidence": 76,
        "evidence": ["미군 기지 추가 타격 발표", "유가 9% 급등"],
    }
    payload.update(overrides)
    return payload


class ValidateJudgmentTest(unittest.TestCase):
    def test_valid_payload_passes(self):
        judgment = validate_judgment(_valid_payload())
        self.assertIsInstance(judgment, GeoRiskJudgment)
        self.assertEqual(judgment.severity, 88)
        self.assertEqual(judgment.prompt_version, GUARD_PROMPT_VERSION)

    def test_severity_and_confidence_clamped_to_0_100(self):
        judgment = validate_judgment(_valid_payload(severity=150, confidence=-5))
        self.assertEqual(judgment.severity, 100)
        self.assertEqual(judgment.confidence, 0)

    def test_non_numeric_severity_rejected(self):
        with self.assertRaises(GuardJudgeError):
            validate_judgment(_valid_payload(severity="high"))

    def test_direction_outside_whitelist_rejected(self):
        with self.assertRaises(GuardJudgeError):
            validate_judgment(_valid_payload(direction="panic"))

    def test_missing_summary_rejected(self):
        with self.assertRaises(GuardJudgeError):
            validate_judgment(_valid_payload(summary="  "))

    def test_non_dict_payload_rejected(self):
        with self.assertRaises(GuardJudgeError):
            validate_judgment(["not", "a", "dict"])

    def test_boolean_field_type_enforced(self):
        with self.assertRaises(GuardJudgeError):
            validate_judgment(_valid_payload(is_geopolitical_risk="yes"))

    def test_korean_investment_advice_rejected(self):
        with self.assertRaises(GuardJudgeError):
            validate_judgment(_valid_payload(summary="지금은 매수 기회입니다."))

    def test_geopolitical_possession_wording_passes(self):
        # "보유"(핵보유국·핵무기 보유 등)는 지정학 텍스트에 흔하다 — 투자조언 오탐으로
        # 판정을 거부하면 위기 구간에 영구 블라인드가 되므로 금지어에서 제외됐다.
        judgment = validate_judgment(
            _valid_payload(
                summary="이란의 핵보유 능력을 둘러싼 긴장이 고조되고 있습니다.",
                evidence=["북한이 핵무기를 보유하고 있다는 분석"],
            )
        )
        self.assertTrue(judgment.is_geopolitical_risk)

    def test_english_investment_advice_rejected_word_boundary(self):
        # "buy/sell/hold" 는 단어 경계로만 걸린다 — household 같은 합성어는 통과.
        with self.assertRaises(GuardJudgeError):
            validate_judgment(_valid_payload(evidence=["analysts say buy the dip"]))
        judgment = validate_judgment(_valid_payload(evidence=["household spending fell"]))
        self.assertEqual(judgment.evidence, ["household spending fell"])

    def test_string_lists_normalized(self):
        judgment = validate_judgment(_valid_payload(regions=[" Iran ", "", 3]))
        self.assertEqual(judgment.regions, ["Iran", "3"])


class _FakeLlm:
    def __init__(self, payload):
        self.payload = payload
        self.prompts: list[str] = []

    async def generate_json(self, prompt):
        self.prompts.append(prompt)
        return self.payload


class JudgeArticlesTest(unittest.IsolatedAsyncioTestCase):
    async def test_judges_article_batch_with_single_call(self):
        llm = _FakeLlm(_valid_payload())
        articles = [
            GuardArticle(
                source="gdelt", article_hash="a" * 64, title="확전 속보", url="https://x/1",
                published_at=None,
            ),
            GuardArticle(
                source="gdelt", article_hash="b" * 64, title="유가 급등", url="https://x/2",
                published_at=None,
            ),
        ]
        judgment = await judge_articles(llm, articles)
        self.assertEqual(judgment.severity, 88)
        self.assertEqual(len(llm.prompts), 1)
        self.assertIn("https://x/1", llm.prompts[0])
        self.assertIn("https://x/2", llm.prompts[0])


if __name__ == "__main__":
    unittest.main()
