"""#390 OCR 벤치마크 하니스 — 엔진 교체형(Tesseract 먼저), READ-ONLY.

평가셋 이미지(`data/eval_set/images/`) → OCR 엔진 → 텍스트 → 기술 키워드 사전 매칭 →
ground_truth(`labels_draft.json`) 대비 precision/recall/F1 집계.

엔진(tesseract/paddle/surya)은 동일 입력·동일 키워드 사전·동일 정규화로 비교(차이는 OCR
정보만). 키워드 매칭·점수·canonicalize 는 **순수 함수**로 분리해 OCR 없이 단위테스트
(test_ocr_harness.py). 무거운 모델은 1회 적재 후 본루프 전 워밍업(첫 이미지 1회 폐기)으로
모델 로드/그래프 컴파일 비용을 레이턴시(median ms) 측정에서 분리한다. 엔진/언어팩 미설치
시 설치 안내만 한다.

USAGE
    uv run --with pillow --with pytesseract python scripts/research/ocr_harness.py --engine tesseract
    uv run --with pillow --with paddleocr   python scripts/research/ocr_harness.py --engine paddle
    uv run --with pillow --with surya-ocr    python scripts/research/ocr_harness.py --engine surya
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
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


# ── OCR 엔진 (loader → predict 클로저) ────────────────────────────────────────
# 무거운 모델(Paddle/Surya)을 이미지마다 새로 띄우지 않도록, loader 가 모델을 1회 적재한
# predict(image_path)->str 클로저를 돌려준다. import 는 loader 내부 지연 import(의존성 격리).
def _load_tesseract(lang: str):
    """Tesseract(native). webp 는 PIL 로 RGB 변환 후 OCR(leptonica webp 의존 회피)."""
    import pytesseract
    from PIL import Image

    def predict(image_path: Path) -> str:
        with Image.open(image_path) as im:
            return pytesseract.image_to_string(im.convert("RGB"), lang=lang)
    return predict


def _load_paddle(lang: str):
    """PaddleOCR. 모델 1회 적재 후 재사용. 3.x(predict/rec_texts) 우선, 2.x(.ocr 중첩) 폴백."""
    import os

    # PP-OCRv5 가 CPU oneDNN+PIR 경로에서 attribute 변환 버그를 일으킨다(onednn_instruction.cc).
    # paddle import 전에 oneDNN 비활성 → CPU 순정 커널로 우회.
    os.environ.setdefault("FLAGS_use_mkldnn", "0")

    import numpy as np
    from paddleocr import PaddleOCR
    from PIL import Image

    plang = "korean" if lang.startswith("kor") else (lang.split("+")[0] or "en")
    try:
        ocr = PaddleOCR(lang=plang, use_textline_orientation=True,  # PaddleOCR 3.x
                        enable_mkldnn=False)
    except Exception:  # noqa: BLE001 — 구버전 인자 폴백
        ocr = PaddleOCR(lang=plang, use_angle_cls=True)             # 2.x

    def predict(image_path: Path) -> str:
        with Image.open(image_path) as im:
            arr = np.asarray(im.convert("RGB"))
        result = ocr.predict(arr) if hasattr(ocr, "predict") else ocr.ocr(arr)
        texts: list[str] = []
        for res in (result or []):
            if hasattr(res, "get") and res.get("rec_texts") is not None:   # 3.x
                texts.extend(res["rec_texts"])
            elif isinstance(res, (list, tuple)):                           # 2.x: [box,(text,score)]
                texts.extend(ln[1][0] for ln in res if ln)
        return "\n".join(texts)
    return predict


def _load_surya(lang: str):
    """Surya(Torch). detection+recognition predictor 1회 적재 후 재사용."""
    from PIL import Image
    from surya.detection import DetectionPredictor
    from surya.recognition import RecognitionPredictor

    rec, det = RecognitionPredictor(), DetectionPredictor()
    langs = [lang.split("+")[0] or "ko"]

    def predict(image_path: Path) -> str:
        with Image.open(image_path) as im:
            preds = rec([im.convert("RGB")], [langs], det)
        lines = [tl.text for p in preds for tl in p.text_lines]
        return "\n".join(lines)
    return predict


_ENGINES = {"tesseract": _load_tesseract, "paddle": _load_paddle, "surya": _load_surya}


def _median(xs: list[float]) -> float | None:
    """꼬리값에 둔감한 중앙값(레이턴시 대표값). 빈 입력은 None."""
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def run(args: argparse.Namespace) -> dict:
    predict = _ENGINES[args.engine](args.lang)   # 모델 1회 적재
    labels = json.loads((Path(args.labels)).read_text(encoding="utf-8"))
    images_dir = Path(args.images)
    # GT 있는(측정 대상) 포스터만 추림
    targets = [(f, m, images_dir / f) for f, m in labels.items()
               if (m.get("ground_truth_skills") or []) and (images_dir / f).exists()]

    # 워밍업: 첫 실제 이미지 1회 추론 후 결과 폐기(모델 로드·그래프 컴파일 비용을 측정에서 분리).
    if targets:
        try:
            predict(targets[0][2])
        except Exception:  # noqa: BLE001 — 워밍업 실패는 본측정에서 다시 드러난다
            pass

    rows = []
    for fname, meta, img in targets:
        gt = set(meta["ground_truth_skills"])
        t0 = time.perf_counter()
        text = predict(img)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        extracted = extract_skills(text)
        score = prf(extracted, gt)
        rows.append({"file": fname, "company": meta.get("company"), "ms": ms,
                     "extracted": sorted(extracted), "gt": sorted(gt), **score})
    micro = aggregate(rows)
    micro["med_ms"] = _median([r["ms"] for r in rows])
    return {"engine": args.engine, "n": len(rows), "rows": rows, "micro": micro}


def print_report(rep: dict) -> None:
    print(f"\nOCR BENCHMARK — engine={rep['engine']}  labeled={rep['n']}")
    print("=" * 78)
    for r in rep["rows"]:
        print(f"[{r['company']}]  P={r['precision']} R={r['recall']} F1={r['f1']}  "
              f"(tp{r['tp']}/fp{r['fp']}/fn{r['fn']})  {r['ms']}ms")
        print(f"    추출: {r['extracted']}")
        print(f"    정답: {r['gt']}")
    m = rep["micro"]
    print("-" * 78)
    print(f"micro  P={m['precision']}  R={m['recall']}  F1={m['f1']}  "
          f"(tp{m['tp']}/fp{m['fp']}/fn{m['fn']})  median={m['med_ms']}ms")


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
        hint = {
            "tesseract": "Tesseract 바이너리 + kor 데이터 필요: conda install -c conda-forge "
                         "tesseract tesseract-data-kor (TESSDATA_PREFIX 로 tessdata 경로 지정).",
            "paddle": "PaddleOCR 필요: uv run --with paddleocr --with paddlepaddle ... "
                      "(최초 1회 모델 가중치 다운로드).",
            "surya": "Surya 필요: uv run --with surya-ocr ... (Torch + 최초 1회 모델 가중치 다운로드).",
        }.get(args.engine)
        if hint:
            print(f"   {hint}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
