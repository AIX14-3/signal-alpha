"""#390 OCR 평가셋 빌더 — 자소설닷컴 IT/개발 포스터 이미지 수집 (READ-ONLY, DB 미오염).

현재 열린 공고 중 **IT/개발 직군**만 골라(직군 필터), 상세(content HTML)의 `<img>` 를 추출
(Phase 0 `_image_urls` 재사용)하고, **로고·배너로 보이는 작은 이미지는 크기 필터로 제외**한 뒤
포스터급 이미지를 로컬에 다운로드한다. 함께 `labels_draft.json` 을 생성해 **사람이
ground_truth_skills 를 눈으로 보고 최종 확정**하게 한다(노이즈 필터 없이 전량 — 빈 칸은 사람이 채움).

개인정보 미수집: 자격요건 포스터 이미지·공고 메타(회사/제목)만. 자소서/지원자 정보 없음.
DB 접근 0(API fetch + 파일 다운로드만). 평가 전용 산출물이라 커밋 대상 아님(data/eval_set/ gitignore).

USAGE
    uv run --with pillow python scripts/research/fetch_jasoseol_eval_images.py --clean --count 22
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

_AW_ROOT = Path(__file__).resolve().parents[2]  # services/agent-worker
sys.path.insert(0, str(_AW_ROOT))

import app.collectors.hiring.sites.jasoseol as jaso  # noqa: E402
from app.collectors.hiring.sites.http import get as http_get  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("fetch_eval_images")

_DEFAULT_OUT = _AW_ROOT / "data" / "eval_set"
_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# IT/개발 직군 키워드 — posting employments[].field + duty group 이름에 매칭(소문자 비교).
# '영업 엔지니어' 등 오탐을 줄이려 bare 'engineer'는 제외, 개발 맥락 단어 위주.
_IT_KEYWORDS = (
    "개발", "소프트웨어", "sw", "s/w", "백엔드", "프론트", "풀스택", "서버", "웹개발",
    "데이터", "ai", "인공지능", "머신러닝", "딥러닝", "ml", "인프라", "devops", "클라우드",
    "정보보안", "보안", "네트워크", "임베디드", "펌웨어", "qa엔지니어", "안드로이드", "ios",
    "프로그래", "developer", "backend", "frontend", "fullstack", "software", "platform",
)


def _is_it_dev(posting: dict) -> bool:
    """공고가 IT/개발 직군인가 — list 단계에서 detail fetch 전에 걸러 비용 절약."""
    fields = [(e.get("field") or "") for e in (posting.get("employments") or []) if isinstance(e, dict)]
    _, duty_names = jaso._duty_info(posting)
    haystack = " ".join(fields + duty_names).lower()
    return any(kw in haystack for kw in _IT_KEYWORDS)


def _ext_of(url: str) -> str:
    ext = Path(urlparse(url).path).suffix.lower()
    return ext if ext in _IMG_EXTS else ".png"


def _download(url: str) -> bytes:
    return http_get(url).content


def _passes_size(data: bytes, min_dim: int, min_bytes: int) -> bool:
    """로고·배너(작은 이미지) 제외. PIL 로 (w,h) 측정, 없으면 byte-size 폴백."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
        return w >= min_dim and h >= min_dim
    except ImportError:
        return len(data) >= min_bytes  # PIL 미설치 → 용량 폴백
    except Exception:
        return False  # 디코드 실패 = 깨진/비이미지 → 제외


def build(args: argparse.Namespace) -> dict:
    out_dir = Path(args.out)
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    if args.clean:
        for f in images_dir.iterdir():
            if f.is_file():
                f.unlink()
        logger.info("🧹 기존 이미지 비움: %s", images_dir)

    jaso.reset_cache()
    postings = jaso._fetch_all_postings()
    it_postings = [p for p in postings if _is_it_dev(p)]
    logger.info("현재 공고 %d건 → IT/개발 직군 %d건 (목표 %d장, min_dim=%dpx)",
                len(postings), len(it_postings), args.count, args.min_dim)

    labels: dict[str, dict] = {}
    scanned = skipped_size = 0
    for p in it_postings:
        if len(labels) >= args.count:
            break
        pid = p.get("id")
        if pid is None:
            continue
        scanned += 1
        detail = jaso._fetch_detail(pid)
        if not detail:
            continue
        urls = jaso._image_urls(detail)
        if not urls:
            continue
        company = detail.get("name") or p.get("name")
        title = detail.get("title") or p.get("title")
        multi = len(urls) > 1
        for idx, url in enumerate(urls):
            if len(labels) >= args.count:
                break
            try:
                data = _download(url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("  다운로드 실패 %s: %s", url, exc)
                continue
            if not _passes_size(data, args.min_dim, args.min_bytes):
                skipped_size += 1
                continue
            fname = f"announcement_id_{pid}" + (f"_{idx}" if multi else "") + _ext_of(url)
            (images_dir / fname).write_bytes(data)
            labels[fname] = {
                "original_url": url,
                "company": company,
                "title": title,
                "ground_truth_skills": [],  # 사람이 눈으로 보고 최종 확정
            }
            logger.info("  [%2d/%d] %s  (%s)", len(labels), args.count, fname, company)
        time.sleep(args.pause)

    labels_path = out_dir / "labels_draft.json"
    labels_path.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("=" * 60)
    logger.info("완료: IT공고 스캔 %d · 이미지 %d장 (크기필터 제외 %d) → %s",
                scanned, len(labels), skipped_size, images_dir)
    logger.info("라벨 초안 → %s  (ground_truth_skills 를 사람이 채울 것)", labels_path)
    return labels


def main() -> None:
    ap = argparse.ArgumentParser(description="자소설 IT/개발 OCR 평가셋 빌더 (#390, DB 미오염)")
    ap.add_argument("--count", type=int, default=22, help="목표 이미지 장수(기본 22, 20~25)")
    ap.add_argument("--min-dim", type=int, default=360, help="최소 가로·세로 px(로고·배너 제외, 기본 360)")
    ap.add_argument("--min-bytes", type=int, default=20000, help="PIL 미설치 시 최소 바이트 폴백")
    ap.add_argument("--pause", type=float, default=0.25, help="공고 간 대기초")
    ap.add_argument("--clean", action="store_true", help="기존 이미지 디렉터리 비우고 재생성")
    ap.add_argument("--out", default=str(_DEFAULT_OUT), help="출력 디렉터리(기본 data/eval_set)")
    args = ap.parse_args()
    build(args)


if __name__ == "__main__":
    main()
