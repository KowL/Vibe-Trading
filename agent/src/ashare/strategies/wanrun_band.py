"""万润科技 (002654.SZ) 波段交易策略 v2.0

核心逻辑：
1. 超跌买入：股价偏离均线过远 + RSI低位 + 缩量止跌
2. 让利润奔跑：趋势不破不出，移动止盈保护利润
3. 硬止损控制回撤

Author: AI Trader
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

SYMBOL = "002654.SZ"
NAME = "万润科技"


@dataclass
class BandParams:
    """波段策略参数"""
    # 买入条件
    rsi_buy_max: float = 60          # RSI低于此值才买
    dev_from_ma20_min: float = -0.15  # 偏离20日线至少-15%
    dev_from_ma20_max: float = 0.10   # 偏离20日线不超过10%
    volume_shrink_pct: float = 1.2    # 成交量低于20日均量120%
    
    # 卖出条件
    stop_loss_pct: float = 0.12       # 硬止损12%（万润波动大，给足空间）
    trailing_stop_pct: float = 0.10   # 移动止盈：从最高点回撤10%
    min_profit_for_trailing: float = 0.15  # 盈利15%后才启动移动止盈
    
    # 仓位
    position_base: float = 0.30       # 基础仓位30%
    position_max: float = 0.50        # 最大仓位50%
    
    # 冷静期
    cooldown_days: int = 2            # 卖出后2天不买入


@dataclass
class BandState:
    """持仓状态"""
    holding: bool = False
    entry_price: float = 0.0
    entry_date: date | None = None
    max_price: float = 0.0
    current_return: float = 0.0
    max_return: float = 0.0
    stop_loss_price: float = 0.0
    last_exit_date: date | None = None
    cooldown_until: date | None = None
    trades: list[dict] = field(default_factory=list)

    def update(self, close_price: float, trade_date: date, ma20: float = 0, rsi: float = 50) -> dict | None:
        """更新持仓状态，返回卖出信号字典或None"""
        if not self.holding:
            return None
        
        self.current_return = (close_price - self.entry_price) / self.entry_price
        if close_price > self.max_price:
            self.max_price = close_price
        if self.current_return > self.max_return:
            self.max_return = self.current_return
        
        # 硬止损
        if close_price <= self.stop_loss_price:
            return {"reason": f"硬止损(-12%)", "price": close_price}
        
        # 动态止盈
        if self.current_return >= 0.15:
            return {"reason": "波段止盈(+15%)", "price": close_price}
        elif self.current_return >= 0.10:
            # 盈利10%后，如果继续涨到12%就持有，否则回撤3%卖
            if self.current_return >= 0.12:
                pass  # 继续持有等15%
            elif close_price < self.max_price * 0.97:
                return {"reason": "盈利回撤保护(+10%→回撤3%)", "price": close_price}
        
        # 移动止盈：盈利15%后，从最高点回撤10%卖出
        if self.current_return >= 0.15:
            trailing_stop = self.max_price * 0.90
            if close_price < trailing_stop:
                return {"reason": f"移动止盈(回撤10%)", "price": close_price}
        
        # 趋势反转卖出：价格跌破20日线且RSI>55（趋势结束）
        if close_price < ma20 * 0.98 and rsi > 55 and self.current_return > 0.05:
            return {"reason": "趋势反转(破20日线)", "price": close_price}
        
        # 盈利5%后RSI超买卖出
        if self.current_return > 0.05 and rsi > 75:
            return {"reason": "RSI超买获利了结", "price": close_price}
        
        return None

    def enter(self, price: float, trade_date: date) -> None:
        self.holding = True
        self.entry_price = price
        self.entry_date = trade_date
        self.max_price = price
        self.max_return = 0.0
        self.stop_loss_price = price * 0.88

    def exit(self, price: float, trade_date: date, reason: str) -> dict:
        pnl = (price - self.entry_price) / self.entry_price
        trade = {
            "entry_date": self.entry_date.isoformat() if self.entry_date else None,
            "exit_date": trade_date.isoformat(),
            "entry_price": self.entry_price,
            "exit_price": price,
            "return_pct": round(pnl * 100, 2),
            "reason": reason,
        }
        self.trades.append(trade)
        self.holding = False
        self.entry_price = 0.0
        self.entry_date = None
        self.max_price = 0.0
        self.last_exit_date = trade_date
        self.cooldown_until = trade_date + timedelta(days=3)
        return trade


class WanrunBandStrategy:
    """万润科技波段策略 v2.0"""

    def __init__(self, symbol: str = SYMBOL) -> None:
        self.symbol = symbol
        self.params = BandParams()
        self.state = BandState()

    def analyze(self, bars: list[dict]) -> dict:
        """分析K线，生成交易信号"""
        if len(bars) < 30:
            return {"signal": "watch", "reason": "数据不足"}

        closes = np.array([b["close"] for b in bars])
        highs = np.array([b["high"] for b in bars])
        lows = np.array([b["low"] for b in bars])
        volumes = np.array([b.get("volume", 0) for b in bars])
        dates = [b["date"] for b in bars]
        
        current_price = closes[-1]
        current_date = dates[-1] if isinstance(dates[-1], date) else date.fromisoformat(str(dates[-1]))
        
        # 计算指标
        ma20 = self._sma(closes, 20)
        rsi = self._rsi(closes, 14)
        vol_ma20 = self._sma(volumes, 20)
        
        c_ma20 = ma20[-1]
        c_rsi = rsi[-1]
        c_vol = volumes[-1]
        c_vol_ma20 = vol_ma20[-1]
        dev_from_ma20 = (current_price - c_ma20) / c_ma20
        
        p = self.params

        # 先检查持仓
        if self.state.holding:
            exit_sig = self.state.update(current_price, current_date, c_ma20, c_rsi)
            if exit_sig:
                trade = self.state.exit(exit_sig["price"], current_date, exit_sig["reason"])
                return {
                    "signal": "sell", "price": exit_sig["price"],
                    "reason": exit_sig["reason"], "trade": trade,
                }
            return {
                "signal": "hold", "price": current_price,
                "reason": f"持仓中，收益{self.state.current_return*100:.1f}%",
                "stop_loss": self.state.stop_loss_price,
            }

        # 检查冷静期
        if self.state.cooldown_until and current_date <= self.state.cooldown_until:
            return {
                "signal": "watch", "price": current_price,
                "reason": f"冷静期，还剩{(self.state.cooldown_until - current_date).days}天",
            }

        # 0. 必须连续下跌2天以上（真正的低点）
        falling_days = 0
        for j in range(-1, -10, -1):
            if len(closes) >= abs(j) + 1 and closes[j] < closes[j-1]:
                falling_days += 1
            else:
                break
        
        if falling_days < 2:
            return {
                "signal": "watch", "price": current_price,
                "reason": f"未连续下跌({falling_days}天)，不是买点",
            }

        # 买入条件：超跌 + 缩量止跌
        buy_reasons = []
        
        # 1. RSI低于60（放宽）
        rsi_ok = c_rsi < 60
        if rsi_ok:
            buy_reasons.append(f"RSI={c_rsi:.0f}")
        
        # 2. 偏离20日线（放宽到-15%~+10%）
        dev_ok = -0.15 <= dev_from_ma20 <= 0.10
        if dev_ok:
            buy_reasons.append(f"偏离均线{dev_from_ma20*100:.1f}%")
        
        # 3. 缩量或放量（放宽）
        vol_ok = c_vol < c_vol_ma20 * 1.5
        if vol_ok:
            buy_reasons.append("量能正常")
        
        # 4. 止跌（今天不比昨天跌太多）
        stop_falling = len(closes) >= 2 and closes[-1] >= closes[-2] * 0.95
        if stop_falling:
            buy_reasons.append("止跌")
        
        # 买入决策：至少满足2个条件 + 连续下跌2天以上
        score = sum([rsi_ok, dev_ok, vol_ok, stop_falling])
        if score >= 2 and falling_days >= 2:
            self.state.enter(current_price, current_date)
            return {
                "signal": "buy", "price": current_price,
                "reason": " + ".join(buy_reasons),
                "stop_loss": self.state.stop_loss_price,
                "confidence": score / 4,
            }

        return {
            "signal": "watch", "price": current_price,
            "reason": f"RSI={c_rsi:.0f} 偏离{dev_from_ma20*100:.1f}% 量{'缩' if vol_ok else '放'}",
        }

    def backtest(self, bars: list[dict]) -> dict:
        """回测"""
        bt_state = BandState()
        original_state = self.state
        self.state = bt_state
        
        signals = []
        for i in range(30, len(bars)):
            window = bars[:i + 1]
            sig = self.analyze(window)
            signals.append(sig)
        
        self.state = original_state
        trades = bt_state.trades
        
        if not trades:
            return {"total_trades": 0, "win_rate": 0, "total_return": 0, "trades": []}
        
        wins = [t for t in trades if t["return_pct"] > 0]
        returns = [t["return_pct"] for t in trades]
        
        cumulative = peak = max_dd = 0.0
        for r in returns:
            cumulative += r
            peak = max(peak, cumulative)
            max_dd = max(max_dd, peak - cumulative)
        
        return {
            "total_trades": len(trades),
            "win_rate": len(wins) / len(trades) if trades else 0,
            "total_return": sum(returns),
            "max_drawdown": max_dd,
            "avg_return": sum(returns) / len(returns),
            "trades": trades,
        }

    @staticmethod
    def _sma(data: np.ndarray, period: int) -> np.ndarray:
        ret = np.cumsum(data, dtype=float)
        ret[period:] = ret[period:] - ret[:-period]
        result = np.zeros(len(data), dtype=float)
        result[period - 1:] = ret[period - 1:] / period
        result[:period - 1] = np.nan
        return result

    @staticmethod
    def _rsi(data: np.ndarray, period: int = 14) -> np.ndarray:
        delta = np.diff(data)
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = np.zeros_like(data)
        avg_loss = np.zeros_like(data)
        avg_gain[period] = np.mean(gain[:period])
        avg_loss[period] = np.mean(loss[:period])
        for i in range(period + 1, len(data)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i - 1]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i - 1]) / period
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        rsi[:period] = np.nan
        return rsi


def run_backtest(bars: list[dict]) -> dict:
    strategy = WanrunBandStrategy()
    result = strategy.backtest(bars)
    signal = strategy.analyze(bars)
    result["current_signal"] = signal
    return result
