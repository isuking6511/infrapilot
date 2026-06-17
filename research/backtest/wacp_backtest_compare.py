import os
import json
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Callable, Any, Tuple

import numpy as np
import pandas as pd


@dataclass
class BacktestConfig:
    initial_equity: float = 10000.0
    fee_rate: float = 0.0005
    slippage_rate: float = 0.0005
    risk_per_trade: float = 0.01
    allow_short: bool = True
    output_dir: str = "results"


@dataclass
class WACPConfig:
    atr_length: int = 14
    atr_multiplier: float = 1.5
    wick_body_ratio: float = 2.0
    displacement_multiplier: float = 1.2
    confluence_threshold: int = 6
    projection_ks: List[float] = field(
        default_factory=lambda: [0.618, 1.0, 1.272, 1.618, 2.0, 2.618, 3.618, 4.236]
    )
    touch_tolerance_atr: float = 0.15
    horizontal_lookback: int = 500
    max_horizontal_levels: int = 300
    stop_atr_buffer: float = 0.5
    rr: float = 2.0
    structure_lookback: int = 20
    pivot_lookback: int = 10
    anchor_proximity_bars: int = 5
    take_profit_mode: str = "rr"


@dataclass
class AnchorSet:
    id: str
    direction: str
    A_time: str
    A_price: float
    B_time: str
    B_price: float
    C_time: str
    C_price: float
    enabled: bool = True


@dataclass
class DiagonalSet:
    id: str
    type: str
    point1_time: str
    point1_price: float
    point2_time: str
    point2_price: float
    enabled: bool = True


class StrategyBase:
    name: str = "base"

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError


class ExistingStrategyAdapter(StrategyBase):
    def __init__(self, strategy: Any, name: str = "existing"):
        self.name = name
        self.strategy = strategy

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        if hasattr(self.strategy, "generate_signals"):
            signals = self.strategy.generate_signals(df)
        elif callable(self.strategy):
            signals = self.strategy(df)
        else:
            raise ValueError("Existing strategy must be callable or have generate_signals().")

        if not isinstance(signals, pd.DataFrame):
            raise ValueError("Existing strategy must return a pandas DataFrame.")

        required_cols = ["signal", "stop", "take_profit", "score", "reason", "anchor_id"]
        for col in required_cols:
            if col not in signals.columns:
                signals[col] = 0 if col == "signal" else np.nan

        return signals


def validate_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    required_cols = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    out = out.sort_values("timestamp")
    out = out.drop_duplicates("timestamp")
    out = out.dropna(subset=required_cols)
    out[["open", "high", "low", "close", "volume"]] = out[
        ["open", "high", "low", "close", "volume"]
    ].astype(float)
    out = out.set_index("timestamp", drop=False)
    return out


def calculate_atr(df: pd.DataFrame, length: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(length).mean()


def detect_pivots(df: pd.DataFrame, lookback: int) -> Tuple[pd.Series, pd.Series]:
    roll_high = df["high"].rolling(lookback, min_periods=lookback).max()
    roll_low = df["low"].rolling(lookback, min_periods=lookback).min()
    pivot_high = df["high"] >= roll_high
    pivot_low = df["low"] <= roll_low
    return pivot_high.fillna(False), pivot_low.fillna(False)


def map_time_to_index(df: pd.DataFrame, time_value: str) -> int:
    ts = pd.to_datetime(time_value, utc=True)
    idx = df.index
    pos = idx.searchsorted(ts)
    if pos <= 0:
        return 0
    if pos >= len(idx):
        return len(idx) - 1
    before = idx[pos - 1]
    after = idx[pos]
    if abs((ts - before).total_seconds()) <= abs((after - ts).total_seconds()):
        return pos - 1
    return pos


def line_price(p1: Tuple[int, float], p2: Tuple[int, float], x: int) -> float:
    x1, y1 = p1
    x2, y2 = p2
    if x2 == x1:
        return y2
    slope = (y2 - y1) / (x2 - x1)
    return y1 + slope * (x - x1)


def candle_touches_level(high: float, low: float, level: float, tolerance: float) -> bool:
    return (low - tolerance) <= level <= (high + tolerance)


def build_projection_levels(anchor: AnchorSet, ks: List[float]) -> List[Dict[str, float]]:
    levels = []
    for k in ks:
        if anchor.direction.lower() == "short":
            level = anchor.C_price - abs(anchor.A_price - anchor.B_price) * k
        else:
            level = anchor.C_price + abs(anchor.B_price - anchor.A_price) * k
        levels.append({"k": k, "level": level})
    return levels


def build_pitchfork_lines(anchor: AnchorSet, idx_a: int, idx_b: int, idx_c: int) -> Dict[str, Dict[str, float]]:
    mid_index = int((idx_b + idx_c) / 2)
    mid_price = (anchor.B_price + anchor.C_price) / 2

    slope = (mid_price - anchor.A_price) / max(1, (mid_index - idx_a))

    def _line_at(base_index: int, base_price: float, x: int) -> float:
        return base_price + slope * (x - base_index)

    median = {"base_index": idx_a, "base_price": anchor.A_price, "slope": slope}
    upper = {"base_index": idx_b, "base_price": anchor.B_price, "slope": slope}
    lower = {"base_index": idx_c, "base_price": anchor.C_price, "slope": slope}

    median_at_b = _line_at(idx_a, anchor.A_price, idx_b)
    offset = anchor.B_price - median_at_b
    warning_upper = {"base_index": idx_b, "base_price": anchor.B_price + offset, "slope": slope}
    warning_lower = {"base_index": idx_c, "base_price": anchor.C_price - offset, "slope": slope}

    return {
        "median": median,
        "upper": upper,
        "lower": lower,
        "warning_upper": warning_upper,
        "warning_lower": warning_lower,
    }


def build_diagonal_lines(
    diagonals: List[DiagonalSet], df: pd.DataFrame
) -> List[Dict[str, Any]]:
    lines = []
    for diag in diagonals:
        if not diag.enabled:
            continue
        i1 = map_time_to_index(df, diag.point1_time)
        i2 = map_time_to_index(df, diag.point2_time)
        if i1 == i2:
            continue
        slope = (diag.point2_price - diag.point1_price) / (i2 - i1)
        lines.append(
            {
                "id": diag.id,
                "type": diag.type,
                "i1": i1,
                "p1": diag.point1_price,
                "slope": slope,
            }
        )
    return lines


def build_wick_body_levels(
    df: pd.DataFrame,
    idx: int,
    wick_body_ratio: float,
    pivot_high: bool,
    pivot_low: bool,
    liquidity_sweep: bool,
    structure_break: bool,
    anchor_near: bool,
) -> List[Dict[str, Any]]:
    row = df.iloc[idx]
    open_p = row["open"]
    close_p = row["close"]
    high = row["high"]
    low = row["low"]

    body = abs(close_p - open_p)
    upper_wick = high - max(open_p, close_p)
    lower_wick = min(open_p, close_p) - low

    levels = []
    qualifies = pivot_high or pivot_low or liquidity_sweep or structure_break or anchor_near

    if qualifies and body > 0:
        if upper_wick > body * wick_body_ratio:
            levels.append({"price": max(open_p, close_p), "type": "upper"})
        if lower_wick > body * wick_body_ratio:
            levels.append({"price": min(open_p, close_p), "type": "lower"})

    return levels


def update_consumed_levels(
    levels: List[Dict[str, Any]], high: float, low: float, tolerance: float
) -> List[Dict[str, Any]]:
    touched = []
    for lvl in levels:
        if candle_touches_level(high, low, lvl["price"], tolerance):
            prev_count = lvl.get("touch_count", 0)
            lvl["touch_count"] = prev_count + 1
            touched.append({"level": lvl, "prev_count": prev_count})
    return touched


def compute_confluence_score(
    projection_touches: int,
    pitchfork_touches: Dict[str, bool],
    diagonal_touches: Dict[str, int],
    horizontal_touches: List[Dict[str, Any]],
    liquidity_sweep: bool,
    structure_break: bool,
    displacement: bool,
    clear_stop: bool,
) -> Tuple[int, List[str]]:
    score = 0
    reasons = []

    if projection_touches > 0:
        score += projection_touches
        reasons.append(f"projection_touch={projection_touches}")

    if pitchfork_touches.get("median"):
        score += 2
        reasons.append("pitchfork_median")
    if pitchfork_touches.get("upper") or pitchfork_touches.get("lower"):
        score += 2
        reasons.append("pitchfork_parallel")
    if pitchfork_touches.get("warning_upper") or pitchfork_touches.get("warning_lower"):
        score += 1
        reasons.append("pitchfork_warning")

    for diag_type, count in diagonal_touches.items():
        if count <= 0:
            continue
        if diag_type == "legacy_angle":
            score += 2 * count
        elif diag_type == "neckline_angle":
            score += 2 * count
        else:
            score += 1 * count
        reasons.append(f"diagonal_{diag_type}={count}")

    for touch in horizontal_touches:
        prev_count = touch["prev_count"]
        if prev_count == 0:
            score += 1
            reasons.append("fresh_horizontal")
        else:
            score -= 2
            reasons.append("consumed_horizontal")

    if liquidity_sweep:
        score += 2
        reasons.append("liquidity_sweep")
    if structure_break:
        score += 2
        reasons.append("structure_break")
    if displacement:
        score += 1
        reasons.append("displacement")
    if clear_stop:
        score += 1
        reasons.append("clear_stop")

    return score, reasons


class WACPStrategy(StrategyBase):
    name: str = "wacp"

    def __init__(
        self,
        config: WACPConfig,
        anchors: List[AnchorSet],
        diagonals: Optional[List[DiagonalSet]] = None,
    ):
        self.config = config
        self.anchors = [a for a in anchors if a.enabled]
        self.diagonals = diagonals or []
        self.last_signals: Optional[pd.DataFrame] = None

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = validate_ohlcv(df)
        atr = calculate_atr(df, self.config.atr_length)
        pivot_high, pivot_low = detect_pivots(df, self.config.pivot_lookback)

        diagonal_lines = build_diagonal_lines(self.diagonals, df)
        anchor_maps = []
        for anchor in self.anchors:
            idx_a = map_time_to_index(df, anchor.A_time)
            idx_b = map_time_to_index(df, anchor.B_time)
            idx_c = map_time_to_index(df, anchor.C_time)
            pitchfork = build_pitchfork_lines(anchor, idx_a, idx_b, idx_c)
            projections = build_projection_levels(anchor, self.config.projection_ks)
            anchor_maps.append(
                {
                    "anchor": anchor,
                    "idx_a": idx_a,
                    "idx_b": idx_b,
                    "idx_c": idx_c,
                    "pitchfork": pitchfork,
                    "projections": projections,
                }
            )

        signals = pd.DataFrame(
            index=df.index,
            columns=["signal", "stop", "take_profit", "score", "reason", "anchor_id"],
        )
        signals["signal"] = 0
        signals["stop"] = np.nan
        signals["take_profit"] = np.nan
        signals["score"] = 0
        signals["reason"] = ""
        signals["anchor_id"] = ""

        horizontal_levels: List[Dict[str, Any]] = []
        last_swing_high = None
        last_swing_low = None

        for i, (ts, row) in enumerate(df.iterrows()):
            if pivot_high.iloc[i]:
                last_swing_high = row["high"]
            if pivot_low.iloc[i]:
                last_swing_low = row["low"]

            prev_swing_high = last_swing_high
            prev_swing_low = last_swing_low

            structure_break = False
            if prev_swing_high is not None and row["close"] > prev_swing_high:
                structure_break = True
            if prev_swing_low is not None and row["close"] < prev_swing_low:
                structure_break = True

            liquidity_sweep = False
            if prev_swing_high is not None and row["high"] > prev_swing_high and row["close"] < prev_swing_high:
                liquidity_sweep = True
            if prev_swing_low is not None and row["low"] < prev_swing_low and row["close"] > prev_swing_low:
                liquidity_sweep = True

            body = abs(row["close"] - row["open"])
            displacement = False
            if not np.isnan(atr.iloc[i]) and body > atr.iloc[i] * self.config.displacement_multiplier:
                displacement = True

            anchor_near = False
            for anchor_map in anchor_maps:
                if abs(i - anchor_map["idx_a"]) <= self.config.anchor_proximity_bars:
                    anchor_near = True
                if abs(i - anchor_map["idx_b"]) <= self.config.anchor_proximity_bars:
                    anchor_near = True
                if abs(i - anchor_map["idx_c"]) <= self.config.anchor_proximity_bars:
                    anchor_near = True

            new_levels = build_wick_body_levels(
                df,
                i,
                self.config.wick_body_ratio,
                pivot_high.iloc[i],
                pivot_low.iloc[i],
                liquidity_sweep,
                structure_break,
                anchor_near,
            )
            for lvl in new_levels:
                lvl["touch_count"] = 0
                lvl["created_index"] = i
                horizontal_levels.append(lvl)

            if len(horizontal_levels) > self.config.max_horizontal_levels:
                horizontal_levels = horizontal_levels[-self.config.max_horizontal_levels :]

            high = row["high"]
            low = row["low"]
            tolerance = 0.0
            if not np.isnan(atr.iloc[i]):
                tolerance = atr.iloc[i] * self.config.touch_tolerance_atr

            horizontal_touches = update_consumed_levels(horizontal_levels, high, low, tolerance)

            best_signal = None
            for anchor_map in anchor_maps:
                anchor = anchor_map["anchor"]
                if i <= anchor_map["idx_c"]:
                    continue

                projection_touches = 0
                for proj in anchor_map["projections"]:
                    if candle_touches_level(high, low, proj["level"], tolerance):
                        projection_touches += 1

                pitchfork_touches = {
                    "median": False,
                    "upper": False,
                    "lower": False,
                    "warning_upper": False,
                    "warning_lower": False,
                }
                for key, line in anchor_map["pitchfork"].items():
                    line_val = line["base_price"] + line["slope"] * (i - line["base_index"])
                    if candle_touches_level(high, low, line_val, tolerance):
                        pitchfork_touches[key] = True

                diagonal_touches: Dict[str, int] = {}
                for dline in diagonal_lines:
                    line_val = dline["p1"] + dline["slope"] * (i - dline["i1"])
                    if candle_touches_level(high, low, line_val, tolerance):
                        diagonal_touches[dline["type"]] = diagonal_touches.get(dline["type"], 0) + 1

                stop_price = None
                if anchor.direction.lower() == "long":
                    candidates = [anchor.C_price]
                    if prev_swing_low is not None:
                        candidates.append(prev_swing_low)
                    touched_levels = [t["level"]["price"] for t in horizontal_touches]
                    if touched_levels:
                        candidates.append(min(touched_levels))
                    stop_price = min(candidates) - (atr.iloc[i] * self.config.stop_atr_buffer)
                else:
                    candidates = [anchor.C_price]
                    if prev_swing_high is not None:
                        candidates.append(prev_swing_high)
                    touched_levels = [t["level"]["price"] for t in horizontal_touches]
                    if touched_levels:
                        candidates.append(max(touched_levels))
                    stop_price = max(candidates) + (atr.iloc[i] * self.config.stop_atr_buffer)

                clear_stop = False
                if stop_price is not None and not np.isnan(atr.iloc[i]):
                    if abs(row["close"] - stop_price) <= atr.iloc[i] * 1.5:
                        clear_stop = True

                score, reasons = compute_confluence_score(
                    projection_touches,
                    pitchfork_touches,
                    diagonal_touches,
                    horizontal_touches,
                    liquidity_sweep,
                    structure_break,
                    displacement,
                    clear_stop,
                )

                if score < self.config.confluence_threshold:
                    continue

                if anchor.direction.lower() == "long":
                    touch_support = (
                        projection_touches > 0
                        or pitchfork_touches["lower"]
                        or any(t["level"]["type"] == "lower" for t in horizontal_touches)
                    )
                    if not touch_support:
                        continue
                    if not (structure_break or liquidity_sweep):
                        continue
                    signal_val = 1
                else:
                    touch_resistance = (
                        projection_touches > 0
                        or pitchfork_touches["upper"]
                        or any(t["level"]["type"] == "upper" for t in horizontal_touches)
                    )
                    if not touch_resistance:
                        continue
                    if not (structure_break or liquidity_sweep):
                        continue
                    signal_val = -1

                entry_price = row["close"]
                if self.config.take_profit_mode == "rr" and stop_price is not None:
                    risk = abs(entry_price - stop_price)
                    if risk <= 0:
                        continue
                    if signal_val == 1:
                        take_profit = entry_price + risk * self.config.rr
                    else:
                        take_profit = entry_price - risk * self.config.rr
                else:
                    take_profit = np.nan

                reason = ";".join(reasons)
                candidate = {
                    "signal": signal_val,
                    "stop": stop_price,
                    "take_profit": take_profit,
                    "score": score,
                    "reason": reason,
                    "anchor_id": anchor.id,
                }

                if best_signal is None or candidate["score"] > best_signal["score"]:
                    best_signal = candidate

            if best_signal is not None:
                signals.at[ts, "signal"] = best_signal["signal"]
                signals.at[ts, "stop"] = best_signal["stop"]
                signals.at[ts, "take_profit"] = best_signal["take_profit"]
                signals.at[ts, "score"] = best_signal["score"]
                signals.at[ts, "reason"] = best_signal["reason"]
                signals.at[ts, "anchor_id"] = best_signal["anchor_id"]

        self.last_signals = signals.copy()
        return signals


class BacktestEngine:
    def __init__(self, config: BacktestConfig):
        self.config = config

    def _apply_cost(self, price: float, direction: int, side: str) -> float:
        if direction == 1:
            if side == "entry":
                return price * (1 + self.config.slippage_rate + self.config.fee_rate)
            return price * (1 - self.config.slippage_rate - self.config.fee_rate)
        if direction == -1:
            if side == "entry":
                return price * (1 - self.config.slippage_rate - self.config.fee_rate)
            return price * (1 + self.config.slippage_rate + self.config.fee_rate)
        return price

    def run(self, df: pd.DataFrame, strategy: StrategyBase) -> Tuple[pd.DataFrame, pd.DataFrame]:
        df = validate_ohlcv(df)
        signals = strategy.generate_signals(df)

        trades = []
        equity_curve = []

        equity = self.config.initial_equity
        position = None
        entry_index = None
        entry_price = None
        stop_price = None
        take_profit = None
        size = None
        entry_signal = None
        bars_held = 0
        exposure_bars = 0

        i = 0
        while i < len(df) - 1:
            row = df.iloc[i]
            ts = row["timestamp"]

            if position is None:
                sig = signals.iloc[i]
                signal_val = int(sig["signal"])
                if signal_val == 0:
                    equity_curve.append({"timestamp": ts, "equity": equity})
                    i += 1
                    continue

                if signal_val == -1 and not self.config.allow_short:
                    equity_curve.append({"timestamp": ts, "equity": equity})
                    i += 1
                    continue

                entry_index = i + 1
                if entry_index >= len(df):
                    break

                entry_price_raw = df["open"].iloc[entry_index]
                entry_price = self._apply_cost(entry_price_raw, signal_val, "entry")
                stop_price = float(sig["stop"]) if not np.isnan(sig["stop"]) else None
                take_profit = float(sig["take_profit"]) if not np.isnan(sig["take_profit"]) else None

                if stop_price is None:
                    equity_curve.append({"timestamp": ts, "equity": equity})
                    i += 1
                    continue

                risk_per_unit = abs(entry_price_raw - stop_price)
                if risk_per_unit <= 0:
                    equity_curve.append({"timestamp": ts, "equity": equity})
                    i += 1
                    continue

                risk_amount = equity * self.config.risk_per_trade
                size = risk_amount / risk_per_unit
                position = signal_val
                entry_signal = sig
                bars_held = 0
                i = entry_index
                continue

            row = df.iloc[i]
            ts = row["timestamp"]
            high = row["high"]
            low = row["low"]
            close = row["close"]

            bars_held += 1
            exposure_bars += 1

            exit_price_raw = None
            exit_reason = "TIMEOUT"

            stop_hit = False
            tp_hit = False

            if position == 1:
                if stop_price is not None and low <= stop_price:
                    stop_hit = True
                if take_profit is not None and high >= take_profit:
                    tp_hit = True
            else:
                if stop_price is not None and high >= stop_price:
                    stop_hit = True
                if take_profit is not None and low <= take_profit:
                    tp_hit = True

            if stop_hit and tp_hit:
                exit_price_raw = stop_price
                exit_reason = "STOP"
            elif stop_hit:
                exit_price_raw = stop_price
                exit_reason = "STOP"
            elif tp_hit:
                exit_price_raw = take_profit
                exit_reason = "TP"

            if exit_price_raw is None and i == len(df) - 1:
                exit_price_raw = close
                exit_reason = "EOD"

            if exit_price_raw is not None:
                exit_price = self._apply_cost(exit_price_raw, position, "exit")
                if position == 1:
                    pnl = (exit_price - entry_price) * size
                else:
                    pnl = (entry_price - exit_price) * size

                equity += pnl
                r_multiple = pnl / (self.config.risk_per_trade * self.config.initial_equity)
                trades.append(
                    {
                        "entry_time": df["timestamp"].iloc[entry_index],
                        "exit_time": ts,
                        "direction": "long" if position == 1 else "short",
                        "entry_price": entry_price_raw,
                        "exit_price": exit_price_raw,
                        "pnl": pnl,
                        "r_multiple": r_multiple,
                        "bars_held": bars_held,
                        "score": entry_signal["score"],
                        "reason": entry_signal["reason"],
                        "anchor_id": entry_signal["anchor_id"],
                        "result": exit_reason,
                    }
                )

                position = None
                entry_index = None
                entry_price = None
                stop_price = None
                take_profit = None
                size = None
                entry_signal = None
                bars_held = 0

            equity_curve.append({"timestamp": ts, "equity": equity})
            i += 1

        equity_df = pd.DataFrame(equity_curve)
        equity_df = equity_df.drop_duplicates("timestamp")
        equity_df = equity_df.sort_values("timestamp")

        trades_df = pd.DataFrame(trades)
        if not trades_df.empty:
            trades_df["avg_bars_held"] = trades_df["bars_held"].mean()
        return trades_df, equity_df


class PerformanceAnalyzer:
    def summarize(self, trades: pd.DataFrame, equity: pd.DataFrame, initial_equity: float) -> Dict[str, Any]:
        summary = {
            "final_equity": float(equity["equity"].iloc[-1]) if not equity.empty else initial_equity,
            "total_return": 0.0,
            "CAGR": 0.0,
            "MDD": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "average_R": 0.0,
            "expectancy": 0.0,
            "trade_count": int(len(trades)),
            "avg_bars_held": float(trades["bars_held"].mean()) if not trades.empty else 0.0,
            "max_consecutive_losses": 0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "exposure_time": 0.0,
            "best_trade": float(trades["r_multiple"].max()) if not trades.empty else 0.0,
            "worst_trade": float(trades["r_multiple"].min()) if not trades.empty else 0.0,
        }

        summary["total_return"] = summary["final_equity"] / initial_equity - 1

        if not equity.empty and len(equity) > 1:
            start = equity["timestamp"].iloc[0]
            end = equity["timestamp"].iloc[-1]
            years = (end - start).total_seconds() / (365.25 * 24 * 3600)
            if years > 0:
                summary["CAGR"] = (summary["final_equity"] / initial_equity) ** (1 / years) - 1

            eq = equity["equity"].values
            peak = np.maximum.accumulate(eq)
            drawdown = (eq - peak) / peak
            summary["MDD"] = float(drawdown.min())

            returns = pd.Series(eq).pct_change().dropna()
            if not returns.empty and returns.std() != 0:
                bar_minutes = (
                    equity["timestamp"].diff().dt.total_seconds().median() / 60.0
                )
                if bar_minutes and bar_minutes > 0:
                    bars_per_year = 525600.0 / bar_minutes
                else:
                    bars_per_year = 252.0
                summary["sharpe"] = float(returns.mean() / returns.std() * np.sqrt(bars_per_year))
                downside = returns[returns < 0]
                if downside.std() != 0:
                    summary["sortino"] = float(returns.mean() / downside.std() * np.sqrt(bars_per_year))

        if not trades.empty:
            wins = trades[trades["r_multiple"] > 0]
            losses = trades[trades["r_multiple"] <= 0]
            summary["win_rate"] = len(wins) / len(trades)
            profit = wins["r_multiple"].sum()
            loss = losses["r_multiple"].sum()
            summary["profit_factor"] = float(profit / abs(loss)) if loss != 0 else 0.0
            summary["average_R"] = float(trades["r_multiple"].mean())
            avg_win = wins["r_multiple"].mean() if not wins.empty else 0.0
            avg_loss = losses["r_multiple"].mean() if not losses.empty else 0.0
            summary["expectancy"] = summary["win_rate"] * avg_win + (1 - summary["win_rate"]) * avg_loss

            max_losses = 0
            current_losses = 0
            for r in trades["r_multiple"].values:
                if r <= 0:
                    current_losses += 1
                    max_losses = max(max_losses, current_losses)
                else:
                    current_losses = 0
            summary["max_consecutive_losses"] = max_losses

        if not equity.empty and len(equity) > 1:
            summary["exposure_time"] = float(trades["bars_held"].sum()) / float(len(equity)) if not trades.empty else 0.0

        return summary


def compare_strategies(
    df: pd.DataFrame,
    existing_strategy: StrategyBase,
    wacp_strategy: WACPStrategy,
    config: BacktestConfig,
) -> pd.DataFrame:
    os.makedirs(config.output_dir, exist_ok=True)

    engine = BacktestEngine(config)
    analyzer = PerformanceAnalyzer()

    trades_existing, equity_existing = engine.run(df, existing_strategy)
    trades_wacp, equity_wacp = engine.run(df, wacp_strategy)

    summary_existing = analyzer.summarize(trades_existing, equity_existing, config.initial_equity)
    summary_existing["strategy"] = existing_strategy.name

    summary_wacp = analyzer.summarize(trades_wacp, equity_wacp, config.initial_equity)
    summary_wacp["strategy"] = wacp_strategy.name

    trades_existing.to_csv(os.path.join(config.output_dir, "trades_existing.csv"), index=False)
    trades_wacp.to_csv(os.path.join(config.output_dir, "trades_wacp.csv"), index=False)
    equity_existing.to_csv(os.path.join(config.output_dir, "equity_existing.csv"), index=False)
    equity_wacp.to_csv(os.path.join(config.output_dir, "equity_wacp.csv"), index=False)

    if wacp_strategy.last_signals is not None:
        signals_out = wacp_strategy.last_signals.copy()
        signals_out["timestamp"] = signals_out.index
        signals_out.to_csv(os.path.join(config.output_dir, "wacp_signals.csv"), index=False)

    summary_df = pd.DataFrame([summary_existing, summary_wacp])
    summary_df.to_csv(os.path.join(config.output_dir, "comparison_summary.csv"), index=False)
    return summary_df


def _load_anchor_sets(path: str) -> List[AnchorSet]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [AnchorSet(**item) for item in raw]


def _load_diagonal_sets(path: str) -> List[DiagonalSet]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [DiagonalSet(**item) for item in raw]


def _load_existing_strategy(module_path: str, class_name: str) -> StrategyBase:
    import importlib.util

    spec = importlib.util.spec_from_file_location("existing_strategy", module_path)
    if spec is None or spec.loader is None:
        raise ValueError("Failed to load existing strategy module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    strategy_cls = getattr(module, class_name)
    return ExistingStrategyAdapter(strategy_cls(), name="existing")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="WACP vs Existing Strategy Backtest")
    parser.add_argument("--data", required=True, help="Path to OHLCV CSV with timestamp column")
    parser.add_argument("--anchors", required=True, help="Path to manual anchor sets JSON")
    parser.add_argument("--diagonals", default=None, help="Path to diagonal sets JSON")
    parser.add_argument("--existing-module", required=True, help="Path to existing strategy module file")
    parser.add_argument("--existing-class", required=True, help="Existing strategy class name")
    parser.add_argument("--output-dir", default="results", help="Output directory")
    args = parser.parse_args()

    df = pd.read_csv(args.data)
    anchors = _load_anchor_sets(args.anchors)
    diagonals = _load_diagonal_sets(args.diagonals) if args.diagonals else []

    existing = _load_existing_strategy(args.existing_module, args.existing_class)
    wacp = WACPStrategy(WACPConfig(), anchors, diagonals)

    config = BacktestConfig(output_dir=args.output_dir)
    summary_df = compare_strategies(df, existing, wacp, config)
    print(summary_df)


if __name__ == "__main__":
    main()
