"""Time-based data splits with a hard lock on the final segment.

The loop may only ever see train (tuning) and valid (acceptance). The final 20%
exists to be opened exactly once, after the loop ends — requesting it without
the explicit unlock flag raises, so a careless run cannot leak it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

TRAIN_FRACTION = 0.6
VALID_FRACTION = 0.2


class FinalSegmentLockedError(RuntimeError):
    """Raised when the final hold-out segment is requested without unlocking."""


@dataclass(frozen=True)
class DateSplit:
    train_dates: pd.DatetimeIndex
    valid_dates: pd.DatetimeIndex
    final_dates: pd.DatetimeIndex


def chronological_split(dates: pd.Series) -> DateSplit:
    unique = pd.DatetimeIndex(sorted(pd.unique(dates)))
    n = len(unique)
    train_end = int(n * TRAIN_FRACTION)
    valid_end = int(n * (TRAIN_FRACTION + VALID_FRACTION))
    return DateSplit(
        train_dates=unique[:train_end],
        valid_dates=unique[train_end:valid_end],
        final_dates=unique[valid_end:],
    )


def segment_dates(split: DateSplit, segment: str, *, unlock_final: bool = False) -> pd.DatetimeIndex:
    if segment == "train":
        return split.train_dates
    if segment == "valid":
        return split.valid_dates
    if segment == "final":
        if not unlock_final:
            raise FinalSegmentLockedError(
                "final segment is locked; pass unlock_final=True (--unlock-final) "
                "only for the one-time post-loop evaluation"
            )
        return split.final_dates
    raise ValueError(f"unknown segment: {segment!r}")


def walk_forward_windows(
    dates: pd.DatetimeIndex,
    *,
    test_months: int = 6,
    min_train_months: int = 12,
) -> list[tuple[pd.DatetimeIndex, pd.DatetimeIndex]]:
    """Expanding-train windows with consecutive ``test_months`` test blocks.

    Walk-forward runs inside train+valid only; the final segment is excluded by
    construction because callers pass only unlocked dates.
    """
    if len(dates) == 0:
        return []
    start = dates.min()
    windows: list[tuple[pd.DatetimeIndex, pd.DatetimeIndex]] = []
    test_start = start + pd.DateOffset(months=min_train_months)
    while test_start <= dates.max():
        test_end = test_start + pd.DateOffset(months=test_months)
        train_dates = dates[dates < test_start]
        test_dates = dates[(dates >= test_start) & (dates < test_end)]
        if len(test_dates) > 0 and len(train_dates) > 0:
            windows.append((train_dates, test_dates))
        test_start = test_end
    return windows
