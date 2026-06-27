"""analysis_mode DB 제약 정합 가드 — 핸들러가 미허용 값을 쓰면 적재가 영구 실패한다.

DB CHECK(analysis_results_analysis_mode_check)는 {full, dart_only, quick}만 허용한다. 과거
PRICE 핸들러가 'price_only'를 써서 ANALYZE_PRICE 가 항상 실패했는데, 단위테스트는 fake
connection(CHECK 미실행)이라 못 잡았다. 이 가드는 소스에서 ``analysis_mode="..."`` 값을 직접
스캔해 허용집합에 있는지 검증한다 — 라이브 DB 없이 같은 버그 클래스를 차단한다.

제약을 확장(예: 'price_only' 추가 마이그레이션)하면 아래 ALLOWED 도 같이 갱신할 것.
"""

import re
import unittest
from pathlib import Path

# DB CHECK(analysis_results_analysis_mode_check) 와 일치해야 한다
# (database/migrations/0002_published_baseline.sql, schema.sql).
ALLOWED_ANALYSIS_MODES = {"full", "dart_only", "quick"}

_ORCHESTRATOR = Path(__file__).resolve().parents[1] / "app" / "orchestrator"
_PATTERN = re.compile(r"""analysis_mode\s*=\s*["']([^"']+)["']""")


class AnalysisModeContractTest(unittest.TestCase):
    def test_all_handler_analysis_modes_are_db_allowed(self):
        offenders: list[str] = []
        for path in _ORCHESTRATOR.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for match in _PATTERN.finditer(text):
                mode = match.group(1)
                if mode not in ALLOWED_ANALYSIS_MODES:
                    rel = path.relative_to(_ORCHESTRATOR.parents[1])
                    offenders.append(f"{rel}: analysis_mode={mode!r}")
        self.assertEqual(
            offenders,
            [],
            "DB CHECK 미허용 analysis_mode 발견(적재 실패함). "
            f"허용={sorted(ALLOWED_ANALYSIS_MODES)} / 위반={offenders}",
        )

    def test_guard_actually_scans_something(self):
        # 패턴이 깨져 0건 스캔하는 것을 방지(가드의 가드).
        found = [
            m.group(1)
            for path in _ORCHESTRATOR.rglob("*.py")
            for m in _PATTERN.finditer(path.read_text(encoding="utf-8"))
        ]
        self.assertTrue(found, "analysis_mode 사용처를 하나도 못 찾음 — 스캔 패턴 점검 필요.")


if __name__ == "__main__":
    unittest.main()
