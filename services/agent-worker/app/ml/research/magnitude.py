"""매그니튜드(움직임의 크기) 타깃 — 방향(↑/↓)이 아니라 *얼마나 크게* 움직이는가.

채용 단독 방향 알파는 3소스 전부 기각됐다. 학술적으로 어텐션류 신호는 방향엔 무력하지만
변동성·거래량 *크기* 엔 선행한다([[attention-lead-lag-evidence]]) → 매그니튜드는 미검증 영역.

이 모듈은 두 매그니튜드 타깃과, 그걸 기존 *분류* 파이프라인에 그대로 태우기 위한 **per-date
횡단면 이진 라벨러**를 제공한다(상위 절반=큰 움직임=1, 하위 절반=0). 연속 매그니튜드 자체는
``Dataset.excess_returns`` 슬롯에 실어 ``rank_ic_xs`` 가 "모델 score가 실제 움직임 크기를
횡단면으로 랭크하는가"를 채점한다 — 그래서 16모델·워크포워드·permutation·BH-FDR 전부 무변경
재사용된다.

순수·결정적. 누수 차단: 매그니튜드는 as_of *이후* 가격에서만 계산(피처는 as_of 이하).
``labels.py`` (B 세션 소유)는 건드리지 않고 ``excess_return_pct`` 만 재사용한다.
"""

from __future__ import annotations

from collections import defaultdict

from .datalab_dataset import PriceSeries
from .labels import excess_return_pct

MAGNITUDE_TARGETS = ("abs_return", "realized_vol")


def abs_excess(stock_return_pct: float, benchmark_return_pct: float) -> float:
    """|초과수익| — 시장 대비 움직임의 크기(부호 제거). labels.excess_return_pct 재사용."""
    return abs(excess_return_pct(stock_return_pct, benchmark_return_pct))


def forward_realized_vol(
    prices: PriceSeries, as_of, horizon_sessions: int
) -> float | None:
    """(as_of, as_of+h] 구간 일간수익률의 표본표준편차(%) — forward 실현 변동성.

    ``as_of`` 다음 거래일부터 h거래일까지의 종가로 일별 수익률 h개를 만들고 그 std(ddof=1)를
    낸다. as_of가 거래일이 아니거나 forward 데이터가 모자라면(라벨 날조 금지) ``None``.
    절대 변동성이라 벤치마크가 필요 없다(종목 자체 움직임의 크기).
    """
    i = prices._index.get(as_of)
    if i is None or i + horizon_sessions >= len(prices.closes):
        return None
    closes = prices.closes
    rets: list[float] = []
    for t in range(i + 1, i + horizon_sessions + 1):
        prev = closes[t - 1]
        if prev == 0:
            return None
        rets.append((closes[t] / prev - 1.0) * 100.0)
    n = len(rets)
    if n < 2:
        return None
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)  # 표본분산(ddof=1)
    return var ** 0.5


def cross_sectional_median_labels(
    mags: list[float], dates: list[int], *, min_cross_section: int = 6
) -> tuple[list[int], list[int]]:
    """per-date 횡단면 이진 라벨: 같은 날 종목들 중 매그니튜드 상위 절반=1·하위 절반=0.

    ``mags``/``dates`` 는 같은 길이(행별 매그니튜드 / 날짜 ordinal). 각 날짜에서 매그니튜드로
    정렬해 하위 절반에 0, 상위 절반에 1을 준다. 홀수면 정확히 중앙값 행 1개를 버려 균형을
    맞춘다. 종목 수가 ``min_cross_section`` 미만인 날짜는 횡단면이 빈약해 통째로 버린다
    (랭킹 의미 없음). 반환: (보존할 원본 인덱스 리스트, 그에 대응하는 라벨 리스트).

    절대-임계값 대신 *횡단면 상대* 라벨이라 미래 정보가 새지 않는다(같은 날 종목만 사용).
    """
    idx_by_date: dict[int, list[int]] = defaultdict(list)
    for i, d in enumerate(dates):
        idx_by_date[d].append(i)

    keep: list[int] = []
    labels: list[int] = []
    for _, idxs in idx_by_date.items():
        if len(idxs) < min_cross_section:
            continue
        order = sorted(idxs, key=lambda i: mags[i])
        n = len(order)
        half = n // 2
        for i in order[:half]:          # 하위 절반
            keep.append(i)
            labels.append(0)
        for i in order[n - half:]:      # 상위 절반 (홀수면 중앙 1개 자동 제외)
            keep.append(i)
            labels.append(1)
    return keep, labels


__all__ = [
    "MAGNITUDE_TARGETS",
    "abs_excess",
    "forward_realized_vol",
    "cross_sectional_median_labels",
]
