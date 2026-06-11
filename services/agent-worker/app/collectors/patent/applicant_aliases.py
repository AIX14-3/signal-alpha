from __future__ import annotations


_CORPORATE_PREFIXES = (
    "주식회사 ",
    "(주)",
    "㈜",
)

_CORPORATE_SUFFIXES = (
    " 주식회사",
    "주식회사",
    " Inc.",
    " INC.",
    " Co., Ltd.",
    " CO., LTD.",
    " Corporation",
    " CORPORATION",
    " Corp.",
    " CORP.",
    " Ltd.",
    " LTD.",
)

_LETTER_TO_KOREAN = {
    "A": "에이",
    "B": "비",
    "C": "씨",
    "D": "디",
    "E": "이",
    "F": "에프",
    "G": "지",
    "H": "에이치",
    "I": "아이",
    "J": "제이",
    "K": "케이",
    "L": "엘",
    "M": "엠",
    "N": "엔",
    "O": "오",
    "P": "피",
    "Q": "큐",
    "R": "알",
    "S": "에스",
    "T": "티",
    "U": "유",
    "V": "브이",
    "W": "더블유",
    "X": "엑스",
    "Y": "와이",
    "Z": "제트",
}


def build_applicant_aliases(
    *,
    stock_name: str | None,
    applicant_name: str | None,
    applicant_names: list[str] | None,
) -> list[str]:
    names: list[str] = []
    seeds = [applicant_name, stock_name, *(applicant_names or [])]
    for name in seeds:
        if not name:
            continue
        for alias in applicant_alias_variants(name):
            if alias and alias not in names:
                names.append(alias)
    return names


def applicant_alias_variants(name: str) -> list[str]:
    base = _normalize_company_name(name)
    if not base:
        return []

    stripped = _strip_corporate_designator(base)
    variants = [
        base,
        stripped,
        f"{stripped} 주식회사",
        f"주식회사 {stripped}",
    ]
    no_space = base.replace(" ", "")
    if no_space != base:
        variants.append(no_space)

    korean_acronym = _expand_leading_latin_acronym(stripped)
    if korean_acronym:
        variants.extend(
            [
                korean_acronym,
                f"{korean_acronym} 주식회사",
                f"주식회사 {korean_acronym}",
            ]
        )

    if _has_latin(stripped):
        variants.extend(
            [
                f"{stripped} Inc.",
                f"{stripped} INC.",
                f"{stripped} Corporation",
            ]
        )

    deduped: list[str] = []
    for variant in variants:
        normalized = _normalize_company_name(variant)
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _normalize_company_name(name: str) -> str:
    return " ".join(name.strip().split())


def _strip_corporate_designator(name: str) -> str:
    stripped = _normalize_company_name(name)
    changed = True
    while changed:
        changed = False
        for prefix in _CORPORATE_PREFIXES:
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):].strip()
                changed = True
        for suffix in _CORPORATE_SUFFIXES:
            if stripped.endswith(suffix):
                stripped = stripped[: -len(suffix)].strip()
                changed = True
    return stripped


def _expand_leading_latin_acronym(name: str) -> str | None:
    text = name.strip()
    letters = []
    for char in text:
        if char.isascii() and char.isalpha():
            letters.append(char.upper())
            continue
        break
    if not letters:
        return None
    prefix = "".join(_LETTER_TO_KOREAN.get(letter, letter.lower()) for letter in letters)
    return prefix + text[len(letters):].lstrip()


def _has_latin(value: str) -> bool:
    return any(char.isascii() and char.isalpha() for char in value)
