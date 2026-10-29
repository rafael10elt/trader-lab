from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def _wilder(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def _htf_bias(data: pd.DataFrame, minutes: int) -> pd.Series:
    indexed = data.copy()
    indexed["dt"] = pd.to_datetime(indexed.time, unit="s", utc=True)
    indexed = indexed.set_index("dt")
    rule = f"{minutes}min"
    htf = indexed.resample(rule, origin="start_day", closed="left", label="right").agg({"open":"first", "high":"max", "low":"min", "close":"last", "volume":"sum"}).dropna()
    e20, e50 = _ema(htf.close, 20), _ema(htf.close, 50)
    htf["bias"] = np.select([(htf.close > e20) & (e20 > e50), (htf.close < e20) & (e20 < e50)], [1, -1], default=0)
    left = pd.DataFrame({"dt": pd.to_datetime(data.time, unit="s", utc=True), "ix": np.arange(len(data))}).sort_values("dt")
    right = htf[["bias"]].reset_index().rename(columns={"dt":"closed_at"}).sort_values("closed_at")
    aligned = pd.merge_asof(left, right, left_on="dt", right_on="closed_at", direction="backward", allow_exact_matches=True)
    return pd.Series(aligned.bias.fillna(0).to_numpy(), index=data.index, dtype="int8")


def strategy_signals(frame: pd.DataFrame, mintick: float = 0.01, timezone: str = "Europe/Dublin", session_start: str = "14:45", session_end: str = "16:30") -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    data = frame.copy().sort_values("time").reset_index(drop=True)
    close, high, low, open_, volume = data.close, data.high, data.low, data.open, data.volume
    for n in (9, 20, 50, 200):
        data[f"ema{n}"] = _ema(close, n)
    previous = close.shift(1)
    tr = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1).max(axis=1)
    data["atr14"] = _wilder(tr, 14)
    data["vwap"] = (data.hlc3vol if "hlc3vol" in data else ((high + low + close) / 3 * volume)).groupby(pd.to_datetime(data.time, unit="s", utc=True).dt.tz_convert("America/New_York").dt.date).cumsum() / volume.groupby(pd.to_datetime(data.time, unit="s", utc=True).dt.tz_convert("America/New_York").dt.date).cumsum().replace(0, np.nan)

    # Source Pine strategy: M15/M30 gate uses only completed higher-timeframe bars.
    bias15, bias30 = _htf_bias(data, 15), _htf_bias(data, 30)
    stack_bull = (data.ema9 > data.ema20) & (data.ema20 > data.ema50) & (data.ema50 > data.ema200) & (close > data.vwap)
    stack_bear = (data.ema9 < data.ema20) & (data.ema20 < data.ema50) & (data.ema50 < data.ema200) & (close < data.vwap)
    bull_age = stack_bull.groupby((~stack_bull).cumsum()).cumcount() + 1
    bear_age = stack_bear.groupby((~stack_bear).cumsum()).cumcount() + 1
    early_bull, early_bear = bull_age <= 35, bear_age <= 35
    atr = data.atr14
    too_far = (close - data.ema200).abs() > 3.2 * atr
    chop = (data.ema9 - data.ema50).abs() < 0.8 * atr
    touched_bull = (low <= data.ema9) & (low >= data.ema20 - 20 * mintick)
    touched_bear = (high >= data.ema9) & (high <= data.ema20 + 20 * mintick)
    bar_range = (high - low).replace(0, np.nan)
    ignition_bull = (close > open_) & (close >= high - bar_range * 0.35) & (close > data.ema9)
    ignition_bear = (close < open_) & (close <= low + bar_range * 0.35) & (close < data.ema9)
    local = pd.to_datetime(data.time, unit="s", utc=True).dt.tz_convert(ZoneInfo(timezone)).dt.time
    start_t, end_t = time.fromisoformat(session_start), time.fromisoformat(session_end)
    in_session = (local >= start_t) & (local <= end_t)
    gate_long = (bias15 >= 0) & (bias30 >= 0)
    gate_short = (bias15 <= 0) & (bias30 <= 0)
    lumi_long = stack_bull & early_bull & touched_bull & ignition_bull & ~too_far & ~chop & gate_long & in_session
    lumi_short = stack_bear & early_bear & touched_bear & ignition_bear & ~too_far & ~chop & gate_short & in_session

    # Additional liquid-market research baselines. These are candidates, not promises of profitability.
    ema_long = (data.ema9 > data.ema20) & (data.ema9.shift(1) <= data.ema20.shift(1)) & (close > data.ema50)
    ema_short = (data.ema9 < data.ema20) & (data.ema9.shift(1) >= data.ema20.shift(1)) & (close < data.ema50)
    dev = close - data.vwap
    mean_long = (dev.shift(1) < -1.25 * atr.shift(1)) & (dev >= -0.5 * atr)
    mean_short = (dev.shift(1) > 1.25 * atr.shift(1)) & (dev <= 0.5 * atr)
    prior_high, prior_low = high.shift(1).rolling(20).max(), low.shift(1).rolling(20).min()
    breakout_long = (close > prior_high) & (close > data.ema50)
    breakout_short = (close < prior_low) & (close < data.ema50)
    signals = {
        "Lumi MSFVG · pullback": pd.Series(np.select([lumi_long, lumi_short], [1, -1], default=0), index=data.index),
        "EMA 9/20 · tendência": pd.Series(np.select([ema_long, ema_short], [1, -1], default=0), index=data.index),
        "VWAP · reversão à média": pd.Series(np.select([mean_long, mean_short], [1, -1], default=0), index=data.index),
        "Rompimento · 20 candles": pd.Series(np.select([breakout_long, breakout_short], [1, -1], default=0), index=data.index),
    }
    data["bias_m15"], data["bias_m30"] = bias15, bias30
    return data, signals


def evaluate_oos(frame: pd.DataFrame, signals: pd.Series, cost_points: float = 1.0, regime_mask: pd.Series | None = None) -> dict:
    clean = frame.reset_index(drop=True)
    raw_signal = signals.reset_index(drop=True).fillna(0).astype(int)
    split = max(1, int(len(clean) * 0.7))
    # Only the final chronological 30% is scored. Signal from a completed bar executes at next bar open.
    positions = np.zeros(len(clean), dtype=float)
    allowed = regime_mask.reset_index(drop=True).fillna(False).to_numpy(dtype=bool) if regime_mask is not None else np.ones(len(clean), dtype=bool)
    state = 0
    # Start the scored segment flat; only signals known at the end of the prior bar can open the holdout.
    for i in range(max(0, split - 1), len(clean)):
        signal = raw_signal[i]
        if not allowed[i]:
            state = 0
        elif signal:
            state = int(signal)
        positions[i] = state
    held = np.r_[0.0, positions[:-1]]
    held[split] = 0.0
    prev_close = clean.close.shift(1).to_numpy()
    opens, closes = clean.open.to_numpy(), clean.close.to_numpy()
    gap = np.nan_to_num(opens - prev_close, nan=0.0)
    gap_pnl = held * gap
    body_pnl = positions * (closes - opens)
    pnl = gap_pnl + body_pnl
    turnover = np.abs(positions - held)
    pnl -= turnover * cost_points
    pnl[:split] = 0
    test_pnl = pnl[split:]
    equity = np.cumsum(test_pnl)
    peak = np.maximum.accumulate(np.r_[0.0, equity])[1:]
    trade_results: list[float] = []
    current_side = float(held[split]) if split < len(held) else 0.0
    current_trade = 0.0
    for i in range(split, len(clean)):
        prior_side, next_side = float(held[i]), float(positions[i])
        if next_side != prior_side:
            if prior_side:
                current_trade += float(gap_pnl[i]) - cost_points
                trade_results.append(current_trade)
                current_trade = 0.0
            if next_side:
                current_trade = float(body_pnl[i]) - cost_points
        elif next_side:
            current_trade += float(gap_pnl[i] + body_pnl[i])
        current_side = next_side
    if current_side and len(clean) > split:
        trade_results.append(current_trade)
    trade_returns = np.asarray(trade_results, dtype=float)
    wins, losses = trade_returns[trade_returns > 0], trade_returns[trade_returns < 0]
    trade_count = len(trade_returns)
    profit_factor = float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() else 0.0
    drawdown = float(np.max(peak - equity)) if len(equity) else 0.0
    net = float(test_pnl.sum())
    score = (net / max(drawdown, 1.0)) * np.log1p(trade_count) if trade_count else -1e9
    return {"trades": trade_count, "win_rate": float(np.mean(trade_returns > 0) * 100) if trade_count else 0.0, "net_points": round(net, 2), "profit_factor": round(profit_factor, 3), "max_drawdown_points": round(drawdown, 2), "score": round(float(score), 4), "oos_bars": len(test_pnl)}


def oos_trade_ledger(frame: pd.DataFrame, signals: pd.Series, cost_points: float = 1.0) -> pd.DataFrame:
    """Build the simplified next-open/opposite-signal OOS trades for time-of-day analysis."""
    clean = frame.reset_index(drop=True)
    raw = signals.reset_index(drop=True).fillna(0).astype(int).to_numpy()
    split = max(1, int(len(clean) * 0.7))
    positions = np.zeros(len(clean), dtype=np.int8)
    state = 0
    for i in range(max(0, split - 1), len(clean)):
        if raw[i]:
            state = int(raw[i])
        positions[i] = state
    held = np.r_[0, positions[:-1]]
    held[split] = 0

    rows: list[dict] = []
    trade_side = 0
    trade_pnl = 0.0
    entry_time: int | None = None
    entry_bar = 0
    opens, closes = clean.open.to_numpy(), clean.close.to_numpy()
    prev_close = clean.close.shift(1).to_numpy()
    times = clean.time.to_numpy(dtype="int64")
    for i in range(split, len(clean)):
        prior, current = int(held[i]), int(positions[i])
        gap = float(opens[i] - prev_close[i]) if np.isfinite(prev_close[i]) else 0.0
        body = float(closes[i] - opens[i])
        if current != prior:
            if prior:
                trade_pnl += prior * gap - cost_points
                rows.append({"side": trade_side, "entry_time": entry_time, "exit_time": int(times[i]), "bars_held": i - entry_bar, "net_points": trade_pnl})
                trade_pnl = 0.0
                entry_time = None
            if current:
                trade_side = current
                entry_time = int(times[i])
                entry_bar = i
                trade_pnl = current * body - cost_points
        elif current:
            trade_pnl += current * (gap + body)
    if trade_side and entry_time is not None:
        rows.append({"side": trade_side, "entry_time": entry_time, "exit_time": int(times[-1]), "bars_held": len(clean) - entry_bar, "net_points": trade_pnl})
    return pd.DataFrame(rows, columns=["side", "entry_time", "exit_time", "bars_held", "net_points"])
