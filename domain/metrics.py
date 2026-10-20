"""实时与离线共享单位、指标和绩效实现。"""

import numpy as np
import pandas as pd


def price_change(price: float, previous: float) -> float:
    return price / previous - 1 if previous > 0 else float("nan")


def vwap(amount: float, volume: float) -> float:
    return amount / volume if volume > 0 else float("nan")


def turnover(volume: float, float_shares: float) -> float:
    return volume / float_shares if float_shares > 0 else float("nan")


def volume_ratio(volume: float, same_time_history: list[float]) -> float:
    return (
        volume / np.mean(same_time_history)
        if same_time_history and np.mean(same_time_history) > 0
        else float("nan")
    )


def order_imbalance(bids: list[float], asks: list[float]) -> float:
    b, a = sum(bids), sum(asks)
    return (b - a) / (b + a) if b + a > 0 else float("nan")


def performance(equity: pd.DataFrame, capital: float, benchmark: pd.Series | None = None) -> dict:
    if equity.empty:
        return {}
    nav = equity.set_index("date").equity.astype(float) / capital
    returns = nav.pct_change(fill_method=None)
    returns.iloc[0] = nav.iloc[0] - 1
    n = len(nav)
    annual = nav.iloc[-1] ** (252 / n) - 1 if nav.iloc[-1] > 0 else -1
    vol = float(returns.std(ddof=0) * np.sqrt(252))
    peak = nav.cummax().clip(lower=1)
    drawdown = nav / peak - 1
    max_dd = float(-drawdown.min())
    duration = 0
    longest = 0
    for d in drawdown:
        duration = duration + 1 if d < -1e-12 else 0
        longest = max(longest, duration)
    trough = int(np.argmin(drawdown.to_numpy()))
    peak_position = int(np.argmax(nav.iloc[: trough + 1].to_numpy()))
    after = nav.iloc[trough + 1 :]
    recovered = np.flatnonzero(after.to_numpy() >= float(peak.iloc[trough]) - 1e-12)
    recovery = int(recovered[0] + 1) if len(recovered) else (0 if max_dd == 0 else None)
    result = dict(
        total_return=float(nav.iloc[-1] - 1),
        annual_return=float(annual),
        annual_volatility=vol,
        sharpe=float(returns.mean() * 252 / vol) if vol else 0,
        max_drawdown=max_dd,
        calmar=float(annual / max_dd) if max_dd else 0,
        drawdown_duration=longest,
        recovery_days=recovery,
        drawdown_peak_date=str(nav.index[peak_position]),
        drawdown_trough_date=str(nav.index[trough]),
        daily_win_rate=float((returns > 0).mean()),
        sessions=n,
    )
    idx = pd.to_datetime(nav.index)
    r = pd.Series(returns.to_numpy(), index=idx)
    result["monthly"] = {
        str(k.date()): float(v) for k, v in ((1 + r).resample("ME").prod() - 1).items()
    }
    result["yearly"] = {
        str(k.year): float(v) for k, v in ((1 + r).resample("YE").prod() - 1).items()
    }
    if benchmark is not None and not benchmark.empty:
        b = benchmark.reindex(nav.index).pct_change(fill_method=None)
        # 首日基准缺前值时不补零，明确匹配区间。
        valid = b.notna() & returns.notna()
        ex = returns[valid] - b[valid]
        te = float(ex.std(ddof=0) * np.sqrt(252))
        result.update(
            tracking_error=te,
            information_ratio=float(ex.mean() * 252 / te) if te else 0,
            excess_return=float((1 + returns[valid]).prod() - (1 + b[valid]).prod()),
        )
    return result
