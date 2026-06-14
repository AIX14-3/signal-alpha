"""Forward-return labels for each horizon.

For horizon H, the label at row t uses ONLY close[t+H] / close[t] - 1.
Features at row t use only data up to t (see indicators.py), so there is a
clean t | t+H separation and no look-ahead leakage.

Labels:  +1 (up) / -1 (down) when |return| > DEAD_ZONE, else 0 (flat).
Flat rows are excluded from directional accuracy so we don't score noise.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import DEAD_ZONE, HORIZONS


def add_labels(df: pd.DataFrame, dead_zone: float = DEAD_ZONE) -> pd.DataFrame:
    out = df.copy()
    for name, h in HORIZONS.items():
        fwd_ret = out["close"].shift(-h) / out["close"] - 1
        out[f"ret_{name}"] = fwd_ret
        lab = np.where(fwd_ret > dead_zone, 1,
                       np.where(fwd_ret < -dead_zone, -1, 0))
        # Rows without a full forward window get NaN (not a real 0/flat).
        lab = lab.astype(float)
        lab[fwd_ret.isna().to_numpy()] = np.nan
        out[f"label_{name}"] = lab
    return out
