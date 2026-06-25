"""TCN volatility forecast — CPU (lightweight deep learning). Needs `torch`.

A small Temporal Convolutional Network (dilated causal 1-D convolutions) is the
CPU-runnable DL line: unlike the GPU foundation models (Kronos / Chronos-2) it
trains and infers fast on a CPU at this data scale (~400 sessions/ticker), so it
slots into the same gate as the EWMA/HAR-RV/GARCH/LightGBM CPU set.

It forecasts the SAME quantity as every other model — the realized volatility
over the next H trading days at origin ``asof`` — and is strictly point-in-time:
training windows only use rows whose H-day target is already realized as of
``asof``; the forecast window ends exactly at ``asof``.

Implementation is self-contained (plain ``torch.nn``; no external TCN package).
``torch`` is an optional extra (``signal-alpha-vol-models[dl]`` — the CPU wheel is
enough); ``HAVE_TORCH`` lets the model registry skip this model when torch is not
installed, exactly like ``HAVE_LGBM`` / ``HAVE_ARCH`` for the other backends.
"""

from __future__ import annotations

import math

import numpy as np

from vol_models.common.data_contract import DataContract
from vol_models.common.harness import run_model
from vol_models.common.rv import h_day_vol_from_daily_var

NAME = "tcn"

try:
    import torch
    from torch import nn

    HAVE_TORCH = True
except Exception:  # noqa: BLE001 — missing torch = unavailable, not fatal
    HAVE_TORCH = False


# Defaults (overridable via ModelSpec.cfg). Kept small so CPU training over a few
# hundred windows stays sub-second.
_WINDOW = 48          # input sequence length fed to the TCN
_HIDDEN = 16          # conv channels per temporal block
_KERNEL = 3           # causal conv kernel size
_LEVELS = 2           # temporal blocks (dilations 1, 2, ...)
_EPOCHS = 150         # full-batch Adam epochs
_LR = 0.01
_MIN_TRAIN = 80       # below this many windows, fall back to a flat RV estimate


if HAVE_TORCH:

    class _Chomp1d(nn.Module):
        """Trim the right padding so the convolution stays causal."""

        def __init__(self, chomp: int) -> None:
            super().__init__()
            self._chomp = chomp

        def forward(self, x):  # noqa: D401
            return x[:, :, : -self._chomp] if self._chomp > 0 else x

    class _TemporalBlock(nn.Module):
        """Dilated causal conv → ReLU, with a 1x1 residual shortcut."""

        def __init__(self, in_ch: int, out_ch: int, kernel: int, dilation: int) -> None:
            super().__init__()
            pad = (kernel - 1) * dilation
            self.net = nn.Sequential(
                nn.Conv1d(in_ch, out_ch, kernel, padding=pad, dilation=dilation),
                _Chomp1d(pad),
                nn.ReLU(),
                nn.Conv1d(out_ch, out_ch, kernel, padding=pad, dilation=dilation),
                _Chomp1d(pad),
                nn.ReLU(),
            )
            self.down = nn.Conv1d(in_ch, out_ch, 1) if in_ch != out_ch else None
            self.relu = nn.ReLU()

        def forward(self, x):
            out = self.net(x)
            res = x if self.down is None else self.down(x)
            return self.relu(out + res)

    class _TCN(nn.Module):
        """Stacked temporal blocks → last-step head → scalar (log H-day variance)."""

        def __init__(self, in_ch: int, hidden: int, kernel: int, levels: int) -> None:
            super().__init__()
            blocks = []
            chans = in_ch
            for level in range(levels):
                blocks.append(_TemporalBlock(chans, hidden, kernel, dilation=2**level))
                chans = hidden
            self.tcn = nn.Sequential(*blocks)
            self.head = nn.Linear(hidden, 1)

        def forward(self, x):  # x: (batch, channels, length)
            feat = self.tcn(x)[:, :, -1]  # last timestep
            return self.head(feat)


def _features(rv: np.ndarray, ret: np.ndarray) -> np.ndarray:
    """Per-day input channels: log daily-variance proxy, return, |return|."""
    log_rv = np.log(np.clip(rv, 1e-12, None))
    feats = np.stack([log_rv, ret, np.abs(ret)], axis=1)  # (n, 3)
    mu = feats.mean(axis=0)
    sd = feats.std(axis=0) + 1e-8
    return (feats - mu) / sd


def predict(contract: DataContract, asof_idx: int, horizon: int, cfg: dict, rng) -> float:
    if not HAVE_TORCH:
        raise RuntimeError("`torch` not installed — pip install 'signal-alpha-vol-models[dl]'")

    window = int(cfg.get("window", _WINDOW))
    hist = contract.history(asof_idx).reset_index(drop=True)
    n = len(hist)
    rv = hist["rv_d"].to_numpy(dtype=float)
    ret = hist["ret"].fillna(0.0).to_numpy(dtype=float)

    flat_fallback = h_day_vol_from_daily_var(float(rv[-1]), horizon)
    if n < window + horizon + 1:
        return flat_fallback

    feats = _features(rv, ret)          # (n, 3), point-in-time (rows <= asof)
    r2 = ret**2

    # Supervised windows: origin t in [window-1, n-1-H]; target = realized H-day
    # variance over (t, t+H] (only future returns through t+H <= asof).
    xs: list[np.ndarray] = []
    ys: list[float] = []
    for t in range(window - 1, n - horizon):
        xs.append(feats[t - window + 1 : t + 1])
        ys.append(math.log(max(float(r2[t + 1 : t + 1 + horizon].sum()), 1e-12)))
    if len(xs) < int(cfg.get("min_train", _MIN_TRAIN)):
        return flat_fallback

    x = torch.tensor(np.transpose(np.asarray(xs), (0, 2, 1)), dtype=torch.float32)  # (B,C,L)
    y = torch.tensor(np.asarray(ys), dtype=torch.float32).unsqueeze(1)
    y_mu, y_sd = y.mean(), y.std() + 1e-8
    y_norm = (y - y_mu) / y_sd

    torch.manual_seed(int(cfg.get("seed", 42)))  # reproducible weights
    model = _TCN(
        in_ch=3,
        hidden=int(cfg.get("hidden", _HIDDEN)),
        kernel=int(cfg.get("kernel", _KERNEL)),
        levels=int(cfg.get("levels", _LEVELS)),
    )
    opt = torch.optim.Adam(model.parameters(), lr=float(cfg.get("lr", _LR)))
    loss_fn = nn.MSELoss()

    model.train()
    for _ in range(int(cfg.get("epochs", _EPOCHS))):
        opt.zero_grad()
        loss = loss_fn(model(x), y_norm)
        loss.backward()
        opt.step()

    # Forecast window ends exactly at asof (rows n-window .. n-1).
    x_asof = torch.tensor(
        np.transpose(feats[n - window : n][None, ...], (0, 2, 1)), dtype=torch.float32
    )
    model.eval()
    with torch.no_grad():
        log_var = float(model(x_asof).item()) * float(y_sd) + float(y_mu)

    # np.exp saturates to inf (math.exp would *raise* OverflowError on a large
    # finite log_var, bypassing the isfinite guard below and surfacing as a model
    # error instead of this graceful fallback).
    var_h = float(np.exp(log_var))
    if not math.isfinite(var_h) or var_h <= 0.0:
        return flat_fallback
    return float(math.sqrt(var_h))


if __name__ == "__main__":
    run_model(predict, NAME)
