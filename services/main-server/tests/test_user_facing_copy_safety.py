import ast
from pathlib import Path


FORBIDDEN_NOTICE_PHRASES = (
    "매수",
    "매도",
    "보유 추천",
    "목표 수익률",
    "수익 예측",
    "추천 종목",
    "추천합니다",
    "추천드립니다",
    "투자 타이밍",
)

REQUIRED_NOTICE_PHRASES = (
    "데이터 방향성",
    "근거",
    "소스 간 일치도",
    "사용자 판단 보조",
)


def test_shared_api_notice_uses_product_boundary_language():
    notice = _read_notice()

    for phrase in FORBIDDEN_NOTICE_PHRASES:
        assert phrase not in notice

    for phrase in REQUIRED_NOTICE_PHRASES:
        assert phrase in notice


def test_api_route_string_literals_avoid_recommendation_copy():
    routes_dir = Path(__file__).resolve().parents[1] / "app" / "api" / "routes"

    violations: list[tuple[str, str, str]] = []
    for route_path in sorted(routes_dir.glob("*.py")):
        source = route_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for phrase in FORBIDDEN_NOTICE_PHRASES:
                    if phrase in node.value:
                        violations.append((route_path.name, phrase, node.value))

    assert violations == []


def _read_notice() -> str:
    route_path = Path(__file__).resolve().parents[1] / "app" / "api" / "routes" / "auth.py"
    source = route_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "NOTICE":
                    value = ast.literal_eval(node.value)
                    assert isinstance(value, str)
                    return value
    raise AssertionError("NOTICE constant not found in auth route")
