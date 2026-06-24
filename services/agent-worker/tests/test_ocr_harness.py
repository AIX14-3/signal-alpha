"""#390 OCR 하니스 — 키워드 추출·점수 순수함수 단위테스트 (OCR/엔진 무관)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research"))

import ocr_harness as oh  # noqa: E402


class ExtractSkillsTest(unittest.TestCase):
    def test_basic_stack(self):
        text = "백엔드: Python, Java, Spring Boot, MySQL / 인프라: Docker, Kubernetes(K8s), AWS, CI/CD"
        got = oh.extract_skills(text)
        self.assertSetEqual(
            got, {"Python", "Java", "Spring Boot", "MySQL", "Docker", "Kubernetes", "AWS", "CI/CD"})

    def test_c_family_disambiguation(self):
        got = oh.extract_skills("우대: C++ / C# / C 언어 / Go")
        self.assertSetEqual(got, {"C++", "C#", "C", "Go"})

    def test_java_not_javascript(self):
        self.assertEqual(oh.extract_skills("JavaScript 경험"), {"JavaScript"})
        self.assertEqual(oh.extract_skills("Java 8 경험"), {"Java"})

    def test_no_skills(self):
        self.assertEqual(oh.extract_skills("방송기술 경영지원 행정"), set())

    def test_new_dict_entries(self):
        text = ("AI: ML, LLM, sLM 모델 / MLOps, DevSecOps / 데이터 분석, 클라우드 보안 / "
                "영상 처리·영상 알고리즘 / Windows App, HW 개발, AI Agent, Service Mesh")
        got = oh.extract_skills(text)
        for expect in ("ML", "LLM", "sLM", "MLOps", "DevSecOps", "데이터분석", "클라우드 보안",
                       "영상처리", "영상 알고리즘", "Windows App", "HW개발", "AI Agent", "Service Mesh"):
            self.assertIn(expect, got, expect)

    def test_ml_not_substring(self):
        got = oh.extract_skills("MLOps 와 HTML 그리고 XML")
        self.assertNotIn("ML", got)
        self.assertIn("MLOps", got)


class CanonicalizeTest(unittest.TestCase):
    def test_dot_js_and_space(self):
        self.assertEqual(oh.canonicalize_token("React.js"), "react")
        self.assertEqual(oh.canonicalize_token("Vue.js"), "vue")
        self.assertEqual(oh.canonicalize_token("Spring Boot"), "springboot")
        self.assertEqual(oh.canonicalize_token("클라우드 보안"), "클라우드보안")

    def test_special_chars_preserved(self):
        self.assertEqual(oh.canonicalize_token("C++"), "c++")
        self.assertEqual(oh.canonicalize_token("C#"), "c#")
        self.assertEqual(oh.canonicalize_token(".NET"), ".net")


class ScoreTest(unittest.TestCase):
    def test_prf(self):
        s = oh.prf({"Python", "Java", "Docker"}, {"Python", "Docker", "Kubernetes"})
        self.assertEqual((s["tp"], s["fp"], s["fn"]), (2, 1, 1))
        self.assertAlmostEqual(s["precision"], 0.667, places=2)
        self.assertAlmostEqual(s["recall"], 0.667, places=2)

    def test_canonical_match_bridges_naming(self):
        # 추출 canonical(React/Spring Boot) ↔ GT 표기(React.js/Spring Boot) 정합
        s = oh.prf({"React"}, {"React.js"})
        self.assertEqual((s["tp"], s["fp"], s["fn"]), (1, 0, 0))
        s = oh.prf({"Spring Boot"}, {"Spring Boot"})
        self.assertEqual((s["tp"], s["fp"], s["fn"]), (1, 0, 0))

    def test_empty_ground_truth_unmeasured(self):
        s = oh.prf({"Python"}, set())
        self.assertIsNone(s["precision"])
        self.assertEqual(s["fp"], 1)

    def test_perfect(self):
        s = oh.prf({"Python", "Go"}, {"Python", "Go"})
        self.assertEqual((s["precision"], s["recall"], s["f1"]), (1.0, 1.0, 1.0))

    def test_aggregate_micro(self):
        rows = [
            {"tp": 2, "fp": 1, "fn": 1},
            {"tp": 3, "fp": 0, "fn": 2},
        ]
        m = oh.aggregate(rows)
        self.assertEqual((m["tp"], m["fp"], m["fn"]), (5, 1, 3))
        self.assertAlmostEqual(m["precision"], 5 / 6, places=2)
        self.assertAlmostEqual(m["recall"], 5 / 8, places=2)


if __name__ == "__main__":
    unittest.main()
