from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from strategies import evaluate_oos, oos_trade_ledger, strategy_signals

load_dotenv(Path(__file__).with_name(".env"))

app = FastAPI(title="MNQ Market Lab API", version="0.1.0")
origins = [x.strip() for x in os.getenv("API_CORS_ORIGINS", "http://localhost:5173").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


def mt5_ready() -> None:
    terminal = mt5.terminal_info()
    if terminal is not None and terminal.connected:
        return
    terminal_path = os.getenv("MT5_TERMINAL_PATH")
    initialized = mt5.initialize(path=terminal_path, timeout=60_000) if terminal_path else mt5.initialize(timeout=60_000)
    if not initialized:
        raise HTTPException(503, f"Não foi possível conectar ao terminal MT5: {mt5.last_error()}")


def bars_frame(symbol: str, count: int = 2000, timeframe: int | None = None, start_pos: int = 1) -> pd.DataFrame:
    mt5_ready()
    info = mt5.symbol_info(symbol)
    if info is None:
        raise HTTPException(404, f"Símbolo '{symbol}' não existe no Market Watch. Confira o nome do broker.")
    if not info.visible and not mt5.symbol_select(symbol, True):
        raise HTTPException(400, f"Não foi possível selecionar {symbol}: {mt5.last_error()}")
    rates = mt5.copy_rates_from_pos(symbol, timeframe or mt5.TIMEFRAME_M1, start_pos, min(max(count, 50), 500_000))
    if rates is None or len(rates) == 0:
        raise HTTPException(502, "MT5 não retornou candles. Verifique histórico M1 e conexão.")
    frame = pd.DataFrame(rates)
    frame["time"] = frame["time"].astype("int64")
    frame.rename(columns={"tick_volume": "volume"}, inplace=True)
    frame.sort_values("time", inplace=True)
    return frame[["time", "open", "high", "low", "close", "volume"]]


def history_months_frame(symbol: str, months: int) -> pd.DataFrame:
    """Request the calendar range directly, then fall back to smaller terminal history reads."""
    mt5_ready()
    info = mt5.symbol_info(symbol)
    if info is None:
        raise HTTPException(404, f"Símbolo '{symbol}' não existe no Market Watch.")
    if not info.visible and not mt5.symbol_select(symbol, True):
        raise HTTPException(400, f"Não foi possível selecionar {symbol}: {mt5.last_error()}")
    end = pd.Timestamp.now(tz="UTC")
    start = end - pd.DateOffset(months=months)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, start.to_pydatetime(), end.to_pydatetime())
    if rates is None or len(rates) == 0:
        range_error = str(mt5.last_error())
        # Some terminals only expose already-cached bars to the Python bridge.
        for amount in (250_000, 100_000, 50_000, 20_000, 5_000):
            rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 1, amount)
            if rates is not None and len(rates):
                break
        if rates is None or len(rates) == 0:
            raise HTTPException(502, f"O MT5 não forneceu candles M1 para {months} meses. Erro do terminal: {range_error}; último erro: {mt5.last_error()}. Abra o gráfico M1 do símbolo, pressione Home para carregar histórico e confirme o limite Máx. barras no gráfico.")
    frame = pd.DataFrame(rates)
    frame["time"] = frame["time"].astype("int64")
    frame.rename(columns={"tick_volume": "volume"}, inplace=True)
    frame.sort_values("time", inplace=True)
    frame.drop_duplicates("time", keep="last", inplace=True)
    current_m1_start = int(end.timestamp()) // 60 * 60
    frame = frame.loc[frame.time < current_m1_start]
    return frame[["time", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


FVG_TIMEFRAMES = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30}
FVG_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800}


def find_fvgs(frame: pd.DataFrame, timeframe: str) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    if len(frame) < 3:
        return zones
    highs, lows, times = frame.high.to_numpy(), frame.low.to_numpy(), frame.time.to_numpy()
    for i in range(2, len(frame)):
        # Three-candle imbalance: current low above high two bars back, or vice versa.
        if lows[i] > highs[i - 2]:
            bottom, top, side = float(highs[i - 2]), float(lows[i]), "bullish"
            filled = bool(np.any(lows[i + 1 :] <= bottom))
        elif highs[i] < lows[i - 2]:
            bottom, top, side = float(highs[i]), float(lows[i - 2]), "bearish"
            filled = bool(np.any(highs[i + 1 :] >= top))
        else:
            continue
        if not filled:
            zones.append({"time": int(times[i]), "end_time": int(times[-1]) + FVG_SECONDS.get(timeframe, 300), "bottom": bottom, "top": top, "side": side, "timeframe": timeframe, "filled": False})
    return zones[-100:]


class BacktestRequest(BaseModel):
    symbol: str = Field(default_factory=lambda: os.getenv("MT5_SYMBOL", "MNQ"), min_length=1, max_length=32)
    count: int = Field(5000, ge=100, le=100000)
    fast_ema: int = Field(9, ge=2, le=100)
    slow_ema: int = Field(21, ge=3, le=300)
    cost_points_per_fill: float = Field(1.0, ge=0)
    slippage_points: float = Field(1.0, ge=0)


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        mt5_ready()
    except HTTPException as exc:
        return {"ok": True, "api_connected": True, "mt5_connected": False, "mt5_error": exc.detail, "symbol": os.getenv("MT5_SYMBOL", "MNQ")}
    account = mt5.account_info()
    terminal = mt5.terminal_info()
    connected = bool(terminal and terminal.connected and account)
    result = {"ok": True, "api_connected": True, "mt5_connected": connected, "account_mode": "demo" if account and account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "real_or_unknown", "login": account.login if account else None, "server": account.server if account else None, "currency": account.currency if account else None, "balance": account.balance if account else None, "symbol": os.getenv("MT5_SYMBOL", "MNQ"), "mt5_error": None if connected else str(mt5.last_error())}
    return result


@app.get("/bars")
def get_bars(symbol: str | None = None, count: int = 500) -> dict[str, Any]:
    frame = bars_frame(symbol or os.getenv("MT5_SYMBOL", "MNQ"), count)
    return {"symbol": symbol or os.getenv("MT5_SYMBOL", "MNQ"), "timeframe": "M1", "bars": frame.to_dict(orient="records")}


@app.get("/market-data")
def market_data(symbol: str | None = None, fvg_tf: str = "M5", count: int = 500) -> dict[str, Any]:
    selected_symbol = symbol or os.getenv("MT5_SYMBOL", "MNQ")
    tf_name = fvg_tf.upper()
    if tf_name not in FVG_TIMEFRAMES:
        raise HTTPException(400, "fvg_tf deve ser M1, M5, M15 ou M30.")
    try:
        mt5_ready()
    except HTTPException as exc:
        return {"api_connected": True, "mt5_connected": False, "mt5_error": exc.detail, "symbol": selected_symbol, "bars": [], "fvgs": []}
    account, terminal = mt5.account_info(), mt5.terminal_info()
    connected = bool(terminal and terminal.connected and account)
    try:
        history = bars_frame(selected_symbol, max(count, 5000), mt5.TIMEFRAME_M1, 0)
        fvg_bars = bars_frame(selected_symbol, 500, FVG_TIMEFRAMES[tf_name], 1)
        tick = mt5.symbol_info_tick(selected_symbol)
        info = mt5.symbol_info(selected_symbol)
        mintick = float(info.trade_tick_size or info.point) if info else 0.01
        analyzed, signals = strategy_signals(history, mintick=mintick)
        shown = analyzed.tail(min(max(count, 100), 2000))
        bars = json.loads(shown.to_json(orient="records"))
        latest = analyzed.iloc[-1]
        atr_median = float(analyzed.atr14.tail(200).median()) if analyzed.atr14.notna().any() else 0.0
        regime_trend = "Tendência de alta" if latest.ema20 > latest.ema50 else "Tendência de baixa" if latest.ema20 < latest.ema50 else "Lateral / indefinido"
        regime_vol = "Volatilidade alta" if atr_median and latest.atr14 > atr_median * 1.35 else "Volatilidade normal/baixa"
        current_signals = [{"name": name, "side": "buy" if int(series.iloc[-1]) == 1 else "sell" if int(series.iloc[-1]) == -1 else "flat"} for name, series in signals.items()]
        return {"api_connected": True, "mt5_connected": connected, "mt5_error": None if connected else "Terminal aberto, mas sem conta conectada ao servidor MT5.", "account_mode": "demo" if account and account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "real_or_unknown", "login": account.login if account else None, "server": account.server if account else None, "balance": account.balance if account else None, "currency": account.currency if account else None, "symbol": selected_symbol, "timeframe": "M1", "bars": bars, "fvgs": find_fvgs(fvg_bars, tf_name), "fvg_tf": tf_name, "signals": current_signals, "regime": {"trend": regime_trend, "volatility": regime_vol, "atr14": round(float(latest.atr14), 4) if pd.notna(latest.atr14) else None}, "tick": {"time": int(tick.time), "bid": tick.bid, "ask": tick.ask} if tick else None, "symbol_info": {"point": info.point, "tick_size": info.trade_tick_size, "tick_value": info.trade_tick_value, "digits": info.digits} if info else None, "server_time": int(datetime.now(timezone.utc).timestamp())}
    except HTTPException as exc:
        return {"api_connected": True, "mt5_connected": connected, "mt5_error": exc.detail, "symbol": selected_symbol, "bars": [], "fvgs": []}


class StrategyReportRequest(BaseModel):
    symbol: str = Field(default_factory=lambda: os.getenv("MT5_SYMBOL", "MNQ"), min_length=1, max_length=32)
    count: int = Field(500_000, ge=500, le=500_000)
    period_months: int = Field(6, ge=6, le=12)
    cost_points_per_fill: float = Field(1.0, ge=0)


def apply_history_window(frame: pd.DataFrame, months: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    if frame.empty:
        return frame, {"start": None, "end": None, "actual_months": 0.0, "coverage_ok": False}
    last_time = pd.to_datetime(int(frame.time.iloc[-1]), unit="s", utc=True)
    cutoff = last_time - pd.DateOffset(months=months)
    window = frame.loc[frame.time >= int(cutoff.timestamp())].copy().reset_index(drop=True)
    first_time = pd.to_datetime(int(window.time.iloc[0]), unit="s", utc=True) if len(window) else last_time
    actual_months = max(0.0, (last_time - first_time).total_seconds() / (30.4375 * 86400))
    return window, {"start": first_time.isoformat(), "end": last_time.isoformat(), "actual_months": round(actual_months, 2), "coverage_ok": actual_months >= months * 0.9}


def _trade_metrics(group: pd.DataFrame) -> dict[str, Any]:
    pnl = group.net_points.astype(float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    equity = pnl.cumsum().to_numpy()
    peak = np.maximum.accumulate(np.r_[0.0, equity])[1:]
    return {
        "trades": int(len(group)),
        "win_rate": round(float((pnl > 0).mean() * 100), 1) if len(group) else 0.0,
        "net_points": round(float(pnl.sum()), 2),
        "expectancy_points": round(float(pnl.mean()), 2) if len(group) else 0.0,
        "profit_factor": round(float(wins.sum() / abs(losses.sum())), 2) if len(losses) and losses.sum() else (99.0 if len(wins) else 0.0),
        "max_drawdown_points": round(float(np.max(peak - equity)), 2) if len(equity) else 0.0,
        "avg_hold_bars": round(float(group.bars_held.mean()), 1) if len(group) else 0.0,
    }


def _time_profiles(ledger: pd.DataFrame, strategy_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries = ledger.copy()
    if not entries.empty:
        local = pd.to_datetime(entries.entry_time, unit="s", utc=True).dt.tz_convert("Europe/Dublin")
        entries["hour"] = local.dt.hour
        entries["weekday"] = local.dt.dayofweek
    weekdays = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
    by_hour = {int(key): group for key, group in entries.groupby("hour", sort=True)} if not entries.empty else {}
    by_day = {int(key): group for key, group in entries.groupby("weekday", sort=True)} if not entries.empty else {}
    hourly = [{"strategy": strategy_name, "hour": hour, "window": f"{hour:02d}:00–{hour:02d}:59", **_trade_metrics(by_hour.get(hour, entries.iloc[0:0]))} for hour in range(24)]
    daily = [{"strategy": strategy_name, "weekday": weekdays[day], "weekday_index": day, **_trade_metrics(by_day.get(day, entries.iloc[0:0]))} for day in range(7)]
    return hourly, daily


@app.post("/research/strategies")
def research_strategies(req: StrategyReportRequest) -> dict[str, Any]:
    frame = history_months_frame(req.symbol, req.period_months)
    frame, coverage = apply_history_window(frame, req.period_months)
    if len(frame) < 1000:
        raise HTTPException(400, f"Histórico insuficiente: {len(frame)} candles M1 para a janela solicitada de {req.period_months} meses.")
    info = mt5.symbol_info(req.symbol)
    analyzed, candidates = strategy_signals(frame, mintick=float(info.trade_tick_size or info.point) if info else 0.01)
    trend_state = np.sign(analyzed.ema20 - analyzed.ema50).fillna(0)
    atr_baseline = analyzed.atr14.rolling(200, min_periods=30).median()
    high_vol = analyzed.atr14 > atr_baseline * 1.35
    current_trend, current_vol = int(trend_state.iloc[-1]), bool(high_vol.iloc[-1])
    same_regime = (trend_state == current_trend) & (high_vol == current_vol)
    results = []
    hourly_windows, weekday_windows = [], []
    for name, signal in candidates.items():
        overall = evaluate_oos(analyzed, signal, req.cost_points_per_fill)
        similar = evaluate_oos(analyzed, signal, req.cost_points_per_fill, same_regime)
        results.append({"name": name, **overall, "regime_trades": similar["trades"], "regime_win_rate": similar["win_rate"], "regime_net_points": similar["net_points"], "regime_profit_factor": similar["profit_factor"], "regime_max_drawdown_points": similar["max_drawdown_points"], "regime_score": similar["score"]})
        hourly, daily = _time_profiles(oos_trade_ledger(analyzed, signal, req.cost_points_per_fill), name)
        hourly_windows.extend(hourly)
        weekday_windows.extend(daily)
    results.sort(key=lambda item: item["regime_score"], reverse=True)
    eligible = [item for item in results if coverage["coverage_ok"] and item["regime_trades"] >= 15 and item["regime_profit_factor"] >= 1.05 and item["regime_net_points"] > 0]
    best = eligible[0] if eligible else None
    last = analyzed.iloc[-1]
    recommendation = (f"No holdout e em barras com regime semelhante ao atual, o candidato que passou os filtros foi {best['name']}. Classificação estatística; não é garantia nem sinal de entrada." if best else "Nenhum candidato passou os critérios mínimos no regime atual. Sem recomendação para operar.")
    if not coverage["coverage_ok"]:
        recommendation = f"O MT5 forneceu apenas {coverage['actual_months']} meses de candles para uma janela solicitada de {req.period_months}. A amostra está incompleta; não há recomendação confiável até aumentar o limite de barras do terminal e recarregar o histórico."
    top_windows = sorted((w for w in hourly_windows if w["trades"] >= 15 and w["profit_factor"] >= 1.05 and w["net_points"] > 0), key=lambda w: (w["net_points"] / max(w["max_drawdown_points"], 1), w["trades"]), reverse=True)[:10]
    return {"symbol": req.symbol, "timeframe": "M1", "generated_at": int(datetime.now(timezone.utc).timestamp()), "sample_bars": len(analyzed), "requested_months": req.period_months, "coverage": coverage, "validation": "Holdout cronológico: primeiro 70% para warmup; últimos 30% avaliados. Ranking filtrado por regime comparável ao atual.", "time_analysis": "Janelas horárias agregam o holdout OOS completo e não estão condicionadas ao regime atual. Horários Europe/Dublin têm ajuste de horário de verão; cada trade é classificado pela hora local de entrada. Segmentação exploratória, sujeita a viés de seleção.", "regime": {"trend": "alta" if current_trend > 0 else "baixa" if current_trend < 0 else "lateral", "volatility": "alta" if current_vol else "normal", "atr14": round(float(last.atr14), 4) if pd.notna(last.atr14) else None}, "best_candidate": best["name"] if best and coverage["coverage_ok"] else None, "recommendation": recommendation, "criteria": "Mínimo de 15 entradas OOS em períodos historicamente semelhantes, PF >= 1,05 e resultado líquido positivo; ranking por retorno ajustado ao drawdown e número de trades.", "strategies": results, "hourly_windows": hourly_windows, "weekday_windows": weekday_windows, "top_windows": top_windows}


class SingleStrategyRequest(StrategyReportRequest):
    strategy_name: str


@app.post("/research/backtest")
def research_backtest(req: SingleStrategyRequest) -> dict[str, Any]:
    frame = history_months_frame(req.symbol, req.period_months)
    frame, coverage = apply_history_window(frame, req.period_months)
    if len(frame) < 1000:
        raise HTTPException(400, f"Histórico insuficiente: {len(frame)} candles M1 para {req.period_months} meses.")
    info = mt5.symbol_info(req.symbol)
    analyzed, candidates = strategy_signals(frame, mintick=float(info.trade_tick_size or info.point) if info else 0.01)
    if req.strategy_name not in candidates:
        raise HTTPException(400, "Estratégia desconhecida. Consulte /research/strategies.")
    result = evaluate_oos(analyzed, candidates[req.strategy_name], req.cost_points_per_fill)
    return {"trades": result["trades"], "win_rate": result["win_rate"], "net_profit": result["net_points"], "profit_factor": result["profit_factor"], "max_drawdown": result["max_drawdown_points"], "equity": [], "message": f"{req.strategy_name} · holdout OOS cronológico. {result['oos_bars']} candles de validação; amostra {coverage['start']} → {coverage['end']} ({coverage['actual_months']} meses). Entradas no próximo open; saída por sinal oposto. Port exploratório: sem sizing, stops/alvos do Pine, spread dinâmico ou limites diários."}


def compute_backtest(frame: pd.DataFrame, fast: int, slow: int, req: BacktestRequest) -> dict[str, Any]:
    if fast >= slow:
        raise HTTPException(400, "fast_ema deve ser menor que slow_ema.")
    data = frame.copy()
    data["fast"] = data.close.ewm(span=fast, adjust=False).mean()
    data["slow"] = data.close.ewm(span=slow, adjust=False).mean()
    # Signals are formed at bar close and positions start on the next bar.
    data["signal"] = np.where(data.fast > data.slow, 1, -1)
    data["position"] = data.signal.shift(1).fillna(0)
    data["change"] = data.close.diff().fillna(0)
    data["turnover"] = data.position.diff().abs().fillna(data.position.abs())
    # Price units are points; without broker contract metadata, report point-PnL proxy.
    data["pnl_points"] = data.position * data.change - data.turnover * (req.slippage_points + req.cost_points_per_fill)
    data["equity"] = data.pnl_points.cumsum()
    exits = data.loc[data.turnover > 0, "pnl_points"]
    wins = exits[exits > 0]
    losses = exits[exits < 0]
    gross_loss = abs(losses.sum())
    peak = data.equity.cummax().clip(lower=0)
    drawdown = (peak - data.equity).max()
    return {"trades": int((data.turnover > 0).sum()), "win_rate": float((exits > 0).mean() * 100) if len(exits) else 0.0, "net_profit": round(float(data.equity.iloc[-1]), 2), "profit_factor": round(float(wins.sum() / gross_loss), 3) if gross_loss else 0.0, "max_drawdown": round(float(drawdown) if pd.notna(drawdown) else 0.0, 3), "equity": [{"time": int(t), "value": round(float(v), 2)} for t, v in zip(data.time.iloc[::max(1, len(data)//120)], data.equity.iloc[::max(1, len(data)//120)])], "assumptions": {"signal_timing": "next bar after close", "cost_points_per_fill": req.cost_points_per_fill, "slippage_points_per_fill": req.slippage_points, "pnl_units": "points; contract value and position sizing not applied"}}


@app.post("/backtest")
def backtest(req: BacktestRequest) -> dict[str, Any]:
    frame = bars_frame(req.symbol, req.count)
    return compute_backtest(frame, req.fast_ema, req.slow_ema, req)


@app.post("/ml-research")
def ml_research(req: BacktestRequest) -> dict[str, Any]:
    frame = bars_frame(req.symbol, req.count).copy()
    close = frame["close"]
    frame["ret_1"] = close.pct_change()
    frame["ret_3"] = close.pct_change(3)
    frame["ret_10"] = close.pct_change(10)
    frame["range"] = (frame["high"] - frame["low"]) / close
    frame["body"] = (frame["close"] - frame["open"]) / close
    frame["volatility"] = frame["ret_1"].rolling(20).std()
    frame["volume_change"] = frame["volume"].pct_change().replace([np.inf, -np.inf], np.nan)
    # Features only use the completed bar. The outcome is the next bar open-to-close move.
    frame["next_move"] = frame["close"].shift(-1) - frame["open"].shift(-1)
    frame["target"] = (frame["next_move"] > 0).astype(int)
    features = ["ret_1", "ret_3", "ret_10", "range", "body", "volatility", "volume_change"]
    clean = frame.dropna(subset=features + ["next_move"]).copy()
    if len(clean) < 200:
        raise HTTPException(400, "São necessários pelo menos 200 candles M1 válidos para a pesquisa ML.")
    split = int(len(clean) * 0.7)
    train, test = clean.iloc[:split], clean.iloc[split:]
    if train.target.nunique() < 2 or test.target.nunique() < 2:
        raise HTTPException(400, "A janela não contém as duas classes de retorno necessárias para avaliar o modelo.")
    model = HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=15, l2_regularization=1.0, random_state=42)
    model.fit(train[features], train.target)
    prediction = model.predict(test[features])
    position = np.where(prediction == 1, 1.0, -1.0)
    turnover = np.abs(np.diff(np.r_[0.0, position]))
    pnl = position * test.next_move.to_numpy() - turnover * (req.slippage_points + req.cost_points_per_fill)
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(equity)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    sampled = np.arange(0, len(test), max(1, len(test) // 120))
    return {"trades": int((turnover > 0).sum()), "win_rate": float((pnl > 0).mean() * 100), "net_profit": round(float(equity[-1]), 2), "profit_factor": round(float(wins.sum() / abs(losses.sum())), 3) if len(losses) else 0.0, "max_drawdown": round(float(np.max(peak - equity)), 3), "equity": [{"time": int(t), "value": round(float(v), 2)} for t, v in zip(test.time.to_numpy()[sampled], equity[sampled])], "message": f"ML holdout cronológico: acurácia {float((prediction == test.target.to_numpy()).mean()) * 100:.1f}% · treino {len(train)} candles / validação {len(test)}. Modelo HistGradientBoosting; custos em pontos por fill; resultado em pontos, sem sizing/valor do contrato."}


class TradeRequest(BaseModel):
    symbol: str = Field(default_factory=lambda: os.getenv("MT5_SYMBOL", "MNQ"))
    side: str
    volume: float = Field(gt=0, le=1)
    stop_points: float = Field(gt=0)
    target_points: float = Field(gt=0)


@app.post("/trade/demo")
def demo_trade(req: TradeRequest) -> dict[str, Any]:
    if os.getenv("ALLOW_AUTOTRADING", "false").lower() != "true":
        raise HTTPException(403, "Execução desativada. Defina ALLOW_AUTOTRADING=true após validar o ambiente demo.")
    if os.getenv("MT5_DEMO_ONLY", "true").lower() != "true":
        raise HTTPException(403, "MT5_DEMO_ONLY deve permanecer true para este endpoint.")
    if req.side.lower() not in {"buy", "sell"}:
        raise HTTPException(400, "side deve ser buy ou sell.")
    mt5_ready()
    account = mt5.account_info()
    if account is None or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        raise HTTPException(403, "Bloqueado: o terminal conectado não confirmou conta demo.")
    info = mt5.symbol_info(req.symbol)
    tick = mt5.symbol_info_tick(req.symbol)
    if info is None or tick is None:
        raise HTTPException(404, "Símbolo/tick indisponível.")
    if not info.visible:
        mt5.symbol_select(req.symbol, True)
    buy = req.side.lower() == "buy"
    price = tick.ask if buy else tick.bid
    direction = 1 if buy else -1
    digits = info.digits
    request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": req.symbol, "volume": req.volume, "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, "price": price, "sl": round(price - direction * req.stop_points * info.point, digits), "tp": round(price + direction * req.target_points * info.point, digits), "deviation": 20, "magic": 2601026, "comment": "MNQ Market Lab demo", "type_time": mt5.ORDER_TIME_GTC, "type_filling": info.filling_mode}
    checked = mt5.order_check(request)
    if checked is None or checked.retcode not in (0, mt5.TRADE_RETCODE_DONE):
        err = str(mt5.last_error())
        raise HTTPException(400, f"Pré-validação MT5 recusada: {err}; {checked}")
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        raise HTTPException(502, f"Ordem rejeitada pelo MT5: {result}")
    return {"ok": True, "ticket": result.order, "symbol": req.symbol, "side": req.side, "volume": req.volume, "price": result.price, "mode": "demo"}
