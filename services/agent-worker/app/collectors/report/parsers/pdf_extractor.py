import fitz
from pathlib import Path


def extract_text(pdf_path: str | Path) -> str:
    """PDF에서 전체 텍스트 추출"""
    doc = fitz.open(str(pdf_path))
    pages = [page.get_text("text") for page in doc]
    doc.close()
    return "\n".join(pages)


def extract_first_pages(pdf_path: str | Path, n: int = 3) -> str:
    """앞 n페이지만 추출 (목표주가·투자의견은 대부분 1~3페이지)"""
    doc = fitz.open(str(pdf_path))
    pages = []
    for i, page in enumerate(doc):
        if i >= n:
            break
        pages.append(page.get_text("text"))
    doc.close()
    return "\n".join(pages)
