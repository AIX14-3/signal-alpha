"""SEC 수집 대상 기업(유니버스) — AI 선도주 보드 기준.

연결점(collection target). 여기 목록이 SEC 수집기의 기본 대상이며, 운영에서는
환경변수 SEC_TARGET_TICKERS 로 덮어쓰거나(쉼표 구분), 추후 DB 테이블로 옮길 수 있다.
즉 하드코딩이 아니라 "기본값"이며 나중에 수정 가능하다.

비상장(OpenAI·Anthropic·xAI)은 SEC 공시가 없어 listed=False 로 표기한다.
이들은 상장 상대방(MSFT/AMZN/GOOGL) 공시로 우회 추적한다(해외공시 설계서 §4).
삼성전자·SK하이닉스는 국내(DART) 담당이라 SEC 대상에서 제외한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SecTarget:
    ticker: str
    name: str
    market: str  # "US" | "TW-ADR" | "private"
    listed: bool  # SEC 공시 존재 여부
    category: str  # platform / gpu / cpu / networking / cloud / foundry / ai-lab
    note: str = ""


# AI 선도주 보드(화이트보드) 기준 기본 대상. 나중에 수정 가능.
AI_LEADER_TARGETS: list[SecTarget] = [
    SecTarget("MSFT", "Microsoft", "US", True, "platform"),
    SecTarget("GOOGL", "Alphabet", "US", True, "platform"),
    SecTarget("AMZN", "Amazon", "US", True, "platform"),
    SecTarget("AAPL", "Apple", "US", True, "platform"),
    SecTarget("META", "Meta Platforms", "US", True, "platform"),
    SecTarget("NVDA", "NVIDIA", "US", True, "gpu"),
    SecTarget("AMD", "Advanced Micro Devices", "US", True, "gpu-cpu"),
    SecTarget("INTC", "Intel", "US", True, "cpu"),
    SecTarget("AVGO", "Broadcom", "US", True, "networking"),
    SecTarget("ORCL", "Oracle", "US", True, "cloud"),
    SecTarget("TSM", "Taiwan Semiconductor (ADR)", "TW-ADR", True, "foundry", "20-F/6-K"),
    # 비상장: SEC 공시 없음 → 상장 상대방 공시로 우회.
    SecTarget("OPENAI", "OpenAI", "private", False, "ai-lab", "비상장: MSFT 공시로 우회"),
    SecTarget("ANTHROPIC", "Anthropic", "private", False, "ai-lab", "비상장: AMZN/GOOGL 공시로 우회"),
    SecTarget("XAI", "xAI", "private", False, "ai-lab", "비상장"),
]


def listed_targets() -> list[SecTarget]:
    """SEC 공시가 존재하는(상장) 대상만."""
    return [t for t in AI_LEADER_TARGETS if t.listed]


def default_target_tickers() -> list[str]:
    """수집기 기본 대상 티커(상장사만)."""
    return [t.ticker for t in listed_targets()]


def target_by_ticker(ticker: str) -> SecTarget | None:
    key = ticker.strip().upper()
    for t in AI_LEADER_TARGETS:
        if t.ticker == key:
            return t
    return None
