"""Descriptive risk statistics, not a claim of statistical trading advantage."""
import math

import numpy as np


def wilson_interval(wins, observations):
    if observations < 0 or wins < 0 or wins > observations:
        raise ValueError("勝負筆數不一致")
    if not observations:
        return None
    z = 1.959963984540054
    p = wins / observations
    denominator = 1 + z * z / observations
    center = (p + z * z / (2 * observations)) / denominator
    radius = z * math.sqrt(p * (1 - p) / observations + z * z / (4 * observations**2)) / denominator
    return {"lower": max(0., center - radius), "upper": min(1., center + radius), "level": .95,
            "method": "Wilson", "assumption": "獨立同分布交易的描述性區間；未校正交易相依、選樣及多重搜尋，不能當作未來勝率保證。"}


def finite(value):
    return float(value) if value is not None and math.isfinite(value) else None


def trade_statistics(trades):
    pnl = np.array([x["pnl"] for x in trades], dtype=float)
    winners, losers = pnl[pnl > 0], pnl[pnl < 0]
    n = len(trades)
    gains, losses = float(winners.sum()), float(-losers.sum())
    streak = max_streak = 0
    for value in pnl:
        streak = streak + 1 if value < 0 else 0
        max_streak = max(max_streak, streak)
    returns = [x["pnl"] / x["entry_cost"] for x in trades if x.get("entry_cost", 0) > 0]
    return {"winning_trades":len(winners),"losing_trades":len(losers),"breakeven_trades":int((pnl == 0).sum()),
            "profit_factor":finite(gains / losses) if losses else None,
            "profit_factor_status":"defined" if losses else "no_losses_observed" if gains else "no_nonzero_trades",
            "average_win":finite(float(winners.mean())) if len(winners) else None,
            "average_loss":finite(float(losers.mean())) if len(losers) else None,
            "payoff_ratio":finite(float(winners.mean() / -losers.mean())) if len(winners) and len(losers) else None,
            "expectancy_cash":finite(float(pnl.mean())) if n else None,
            "expectancy_return":finite(float(np.mean(returns))) if returns else None,
            "max_consecutive_losses":max_streak,"win_rate_interval95":wilson_interval(len(winners),n),
            "sample_note":"沒有已完成交易" if not n else "交易樣本偏少" if n < 20 else "仍須檢查交易相依、選樣與樣本外穩定性"}


def equity_statistics(equities, returns, cagr):
    peaks = np.maximum.accumulate(equities)
    drawdowns = equities / peaks - 1
    duration = longest = 0
    for dd in drawdowns[1:]:
        duration = duration + 1 if dd < -1e-12 else 0
        longest = max(longest,duration)
    downside = float(np.sqrt(np.mean(np.minimum(returns,0)**2)))
    max_dd = float(abs(np.min(drawdowns)))
    vol = float(np.std(returns,ddof=1)*np.sqrt(252)) if len(returns)>1 else None
    return {"annual_volatility":finite(vol),
            "sortino":finite(float(np.mean(returns)) / downside * np.sqrt(252)) if downside > 1e-12 else None,
            "calmar":finite(cagr/max_dd) if cagr is not None and max_dd>1e-12 else None,
            "longest_drawdown_bars":longest,"current_drawdown_bars":duration,"drawdown_unrecovered":duration>0,
            "annualization_note":"日報酬年化採252日、無風險利率與最低可接受報酬0；缺值、非同步交易及短樣本需另行評估。"}
