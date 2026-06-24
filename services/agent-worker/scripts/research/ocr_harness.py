"""#390 OCR 벤치마크 하니스 — 엔진 교체형(Tesseract 먼저), READ-ONLY.

평가셋 이미지(`data/eval_set/images/`) → OCR 엔진 → 텍스트 → 기술 키워드 사전 매칭 →
ground_truth(`labels_draft.json`) 대비 precision/recall/F1 집계.

엔진은 동일 입력·동일 키워드 사전으로 비교(차이는 OCR 정보만). 키워드 매칭·점수는 **순수
함수**로 분리해 OCR 없이 단위테스트(test_ocr_harness.py). Tesseract 는 native 바이너리라
미설치 시 안내만 한다(설치 후 재실행).

USAGE
    uv run --with pillow --with pytesseract python scripts/research/ocr_harness.py --engine tesseract
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_AW_ROOT = Path(__file__).resolve().parents[2]
_EVAL = _AW_ROOT / "data" / "eval_set"

# ── 기술 키워드 사전 (canonical, regex, case-sensitive?) ──────────────────────
# 짧고 모호한 토큰(C/Go/R)은 경계·대소문자로 오탐을 줄인다. 전 엔진 공통 사전(공정).
_TECH: list[tuple[str, str, bool]] = [
    ("C++", r"c\s*\+\+", False),
    ("C#", r"c\s*#", False),
    ("C", r"(?<![\w+#])C(?![\w+#])", True),          # 대문자 C 단독 (C++/C# 제외)
    ("Python", r"\bpython\b", False),
    ("Java", r"\bjava\b(?!script)", False),
    ("JavaScript", r"\bjavascript\b", False),
    ("TypeScript", r"\btypescript\b", False),
    ("Go", r"\bgo(?:lang)?\b", False),
    ("Scala", r"\bscala\b", False),
    ("Kotlin", r"\bkotlin\b", False),
    ("Rust", r"\brust\b", False),
    ("Spring Boot", r"\bspring(?:\s*boot)?\b", False),  # GT 라벨 정합(canonical=Spring Boot)
    ("React", r"\breact(?:\.js)?\b", False),
    ("Vue", r"\bvue(?:\.js)?\b", False),
    ("Django", r"\bdjango\b", False),
    ("FastAPI", r"\bfastapi\b", False),
    (".NET", r"\.net\b", False),
    ("Node.js", r"\bnode(?:\.js)?\b", False),
    ("PyTorch", r"\bpytorch\b", False),
    ("TensorFlow", r"\btensorflow\b", False),
    ("MySQL", r"\bmysql\b", False),
    ("PostgreSQL", r"\b(?:postgres(?:ql)?)\b", False),
    ("Oracle", r"\boracle\b", False),
    ("Redis", r"\bredis\b", False),
    ("MongoDB", r"\bmongodb\b", False),
    ("Hadoop", r"\bhadoop\b", False),
    ("Spark", r"\bspark\b", False),
    ("Kafka", r"\bkafka\b", False),
    ("ELK", r"\b(?:elk|elasticsearch)\b", False),
    ("Docker", r"\bdocker\b", False),
    ("Kubernetes", r"\b(?:kubernetes|k8s)\b", False),
    ("Helm", r"\bhelm\b", False),
    ("ArgoCD", r"\bargo\s?cd\b", False),
    ("Jenkins", r"\bjenkins\b", False),
    ("GitOps", r"\bgitops\b", False),
    ("Git", r"\bgit\b(?!ops|hub|lab)", False),
    ("Terraform", r"\bterraform\b", False),
    ("CI/CD", r"\bci/?cd\b", False),
    ("Linux", r"\blinux\b", False),
    ("AWS", r"\baws\b", False),
    ("GCP", r"\bgcp\b", False),
    ("Azure", r"\bazure\b", False),
    ("Grafana", r"\bgrafana\b", False),
    ("Prometheus", r"\bprometheus\b", False),
    ("Loki", r"\bloki\b", False),
    ("Jaeger", r"\bjaeger\b", False),
    ("Bash", r"\bbash\b", False),
    ("CMake", r"\bcmake\b", False),
    ("DPDK", r"\bdpdk\b", False),
    ("IDA", r"\bida\b", False),
    ("MLOps", r"\bmlops\b", False),
    ("DevSecOps", r"\bdevsecops\b", False),
    ("DevOps", r"\bdevops\b", False),
    ("eBPF", r"\bebpf\b", False),
    # ── AI/ML 약어 (짧고 모호 → 대소문자·경계로 오탐 차단) ──
    ("ML", r"(?<![A-Za-z])ML(?![A-Za-z])", True),       # MLOps/HTML/XML 오탐 방지
    ("LLM", r"(?<![A-Za-z])LLM(?![A-Za-z])", True),
    ("sLM", r"\bs(?:mall\s*)?lm\b", False),             # sLM/SLM
    ("AI Agent", r"\bai\s*agent\b", False),
    ("Service Mesh", r"\bservice\s*mesh\b", False),
    # ── 한글 직무/기술 용어 (GT 라벨 정합) ──
    ("데이터분석", r"데이터\s*분석", False),
    ("클라우드 보안", r"클라우드\s*보안", False),
    ("영상처리", r"영상\s*처리", False),
    ("영상 알고리즘", r"영상\s*알고리즘", False),
    ("Windows App", r"\bwindows\s*app\b", False),
    ("HW개발", r"(?:\bhw\b|하드웨어)\s*개발", False),
]
_COMPILED = [(name, re.compile(pat, 0 if cs else re.IGNORECASE)) for name, pat, cs in _TECH]


# ── 순수 함수 (단위테스트 대상) ───────────────────────────────────────────────
def canonicalize_token(tok: str) -> str:
    """비교용 정규화 키. 추출/GT 표기차를 흡수해 '같은 저울'로 맞춘다.

    소문자화 + ``.js`` 접미 제거 + 전체 공백 제거. 특수문자(``+``/``#``/``.``)는 보존해
    ``C++``/``C#``/``.NET`` 토큰 유실을 막는다.
        React.js → react,  "Spring Boot" → springboot,  "클라우드 보안" → 클라우드보안
    """
    t = tok.strip().lower()
    t = re.sub(r"\.js\b", "", t)   # react.js→react, node.js→node
    t = re.sub(r"\s+", "", t)      # 공백 제거 → 한글/복합어 표기차 흡수
    return t


def extract_skills(text: str) -> set[str]:
    """OCR 텍스트 → 사전 매칭된 canonical 기술 집합(전 엔진 공통)."""
    return {name for name, rx in _COMPILED if rx.search(text or "")}


def prf(extracted: set[str], ground_truth: set[str]) -> dict[str, float]:
    """precision/recall/F1. 비교는 canonical 키로(표기차 정합). gt 공집합은 측정 제외(None)."""
    if not ground_truth:
        return {"precision": None, "recall": None, "f1": None, "tp": 0, "fp": len(extracted), "fn": 0}
    ex = {canonicalize_token(s) for s in extracted}
    gt = {canonicalize_token(s) for s in ground_truth}
    tp = len(ex & gt)
    fp = len(ex - gt)
    fn = len(gt - ex)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3),
            "tp": tp, "fp": fp, "fn": fn}


def aggregate(rows: list[dict]) -> dict:
    """micro(전체 tp/fp/fn 합) P/R/F1."""
    tp = sum(r["tp"] for r in rows)
    fp = sum(r["fp"] for r in rows)
    fn = sum(r["fn"] for r in rows)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3), "tp": tp, "fp": fp, "fn": fn}


# ── OCR 엔진 (Tesseract; Paddle/Surya 후속) ───────────────────────────────────
def run_tesseract(image_path: Path, lang: str = "kor+eng") -> str:
    """이미지 → Tesseract 텍스트. webp 는 PIL 로 열어 PNG 변환 후 OCR(leptonica webp 의존 회피)."""
    import pytesseract
    from PIL import Image

    with Image.open(image_path) as im:
        return pytesseract.image_to_string(im.convert("RGB"), lang=lang)


_ENGINES = {"tesseract": run_tesseract}


def run(args: argparse.Namespace) -> dict:
    engine = _ENGINES[args.engine]
    labels = json.loads((Path(args.labels)).read_text(encoding="utf-8"))
    images_dir = Path(args.images)
    rows = []
    for fname, meta in labels.items():
        gt = set(meta.get("ground_truth_skills") or [])
        if not gt:
            continue  # 라벨 없는(비개발/제외) 포스터는 측정 제외
        img = images_dir / fname
        if not img.exists():
            continue
        text = engine(img, lang=args.lang)
        extracted = extract_skills(text)
        score = prf(extracted, gt)
        rows.append({"file": fname, "company": meta.get("company"),
                     "extracted": sorted(extracted), "gt": sorted(gt), **score})
    return {"engine": args.engine, "n": len(rows), "rows": rows, "micro": aggregate(rows)}


def print_report(rep: dict) -> None:
    print(f"\nOCR BENCHMARK — engine={rep['engine']}  labeled={rep['n']}")
    print("=" * 78)
    for r in rep["rows"]:
        print(f"[{r['company']}]  P={r['precision']} R={r['recall']} F1={r['f1']}  "
              f"(tp{r['tp']}/fp{r['fp']}/fn{r['fn']})")
        print(f"    추출: {r['extracted']}")
        print(f"    정답: {r['gt']}")
    m = rep["micro"]
    print("-" * 78)
    print(f"micro  P={m['precision']}  R={m['recall']}  F1={m['f1']}  (tp{m['tp']}/fp{m['fp']}/fn{m['fn']})")


def main() -> None:
    # Windows 콘솔 기본 cp949 는 한글 사전·'—' 출력 시 깨진다 → UTF-8 강제.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser(description="OCR 벤치마크 하니스 (#390)")
    ap.add_argument("--engine", default="tesseract", choices=list(_ENGINES))
    ap.add_argument("--images", default=str(_EVAL / "images"))
    ap.add_argument("--labels", default=str(_EVAL / "labels_draft.json"))
    ap.add_argument("--lang", default="kor+eng")
    args = ap.parse_args()
    try:
        print_report(run(args))
    except Exception as exc:  # noqa: BLE001
        print(f"\n⚠️  엔진 실행 실패({args.engine}): {exc}", file=sys.stderr)
        if args.engine == "tesseract":
            print("   Tesseract 바이너리 + kor 데이터 필요: "
                  "conda install -c conda-forge tesseract tesseract-data-kor  (또는 OS 패키지)",
                  file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
