"""日频事件撮合。只接收内存快照，不访问网络、磁盘或 Qt。"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from collections.abc import Callable
import math
import pandas as pd
import numpy as np
from domain.data import ResearchData
from domain.metrics import performance


@dataclass
class CostConfig:
    commission: float = 0.00025
    minimum: float = 5
    transfer: float = 0.00001
    stamp: float = 0.0005
    slippage: float = 0.0005
    impact: float = 0.001
    participation: float = 0.05
    multiplier: float = 1


@dataclass
class BacktestConfig:
    capital: float = 5_000_000
    frequency: str = "weekly"
    strict: bool = True
    daily_turnover: float = 0.30
    warn_drawdown: float = 0.15
    reduce_drawdown: float = 0.20
    execution: str = "open"
    costs: CostConfig = field(default_factory=CostConfig)
    rules: dict = field(default_factory=dict)


@dataclass
class Lot:
    quantity: int
    buy_date: str
    cost: float


@dataclass
class Result:
    equity: pd.DataFrame
    trades: pd.DataFrame
    holdings: pd.DataFrame
    rejected: pd.DataFrame
    metrics: dict
    metadata: dict


def tick(price: float) -> float:
    return float(Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def lot_quantity(quantity: float, board: str, rules: dict) -> int:
    r = rules.get("lot_rules", {}).get(board, {"minimum": 100, "step": 100})
    q = int(math.floor(max(0, quantity) / r["step"])) * r["step"]
    return q if q >= r["minimum"] else 0


def limits(
    board: str,
    is_st: bool,
    date: str,
    prev_close: float,
    listing_day: int,
    rules: dict,
    list_date: str | None = None,
) -> tuple[float | None, float | None]:
    available = [r for r in rules.get("rules", {}).get(board, []) if r["effective"] <= date]
    if not available:
        raise ValueError(f"没有 {board} 在 {date} 生效的限价规则")
    current = available[-1]
    # IPO 按上市时适用制度，不能给历史老股重新套新股豁免。
    initial = [r for r in available if r["effective"] <= (list_date or date)]
    ipo = initial[-1] if initial else current
    if listing_day <= ipo["ipo_days"]:
        up, down = ipo["ipo_up"], ipo["ipo_down"]
        return (
            tick(prev_close * (1 + up)) if up is not None else None,
            tick(prev_close * (1 - down)) if down is not None else None,
        )
    pct = current["st"] if is_st else current["normal"]
    return tick(prev_close * (1 + pct)), tick(prev_close * (1 - pct))


def fee(notional: float, side: str, date: str, cfg: CostConfig, rules: dict) -> dict:
    def rate(schedule: str, fallback: float) -> float:
        selected = [r["rate"] for r in rules.get(schedule, []) if r["effective"] <= date]
        return selected[-1] if selected else fallback

    m = cfg.multiplier
    return {
        "commission": max(cfg.minimum, notional * cfg.commission) * m if notional else 0,
        "transfer": notional * rate("transfer_schedule", cfg.transfer) * m,
        "stamp": notional * rate("stamp_schedule", cfg.stamp) * m if side == "sell" else 0,
    }


def run(
    data: ResearchData,
    start: str,
    end: str,
    signal: Callable[[str], dict[str, float]],
    cfg: BacktestConfig,
    progress: Callable[[int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    benchmark: pd.Series | None = None,
) -> Result:
    if cfg.capital <= 0 or not 0 < cfg.costs.participation <= 1 or not 0 < cfg.daily_turnover <= 2:
        raise ValueError("资金、参与率或换手约束无效")
    if cfg.execution not in ("open", "vwap"):
        raise ValueError("执行方式必须为open或vwap")
    if cfg.frequency not in ("daily", "weekly", "monthly"):
        raise ValueError("调仓频率无效")
    if data.provenance == "realtime":
        raise ValueError("日频回测拒绝实时库")
    sessions = [d for d in data.dates() if start <= d <= end]
    if cfg.strict:
        data.check_history(sessions)
    if not sessions:
        raise ValueError("所选区间没有真实交易日")
    if (
        not data.actions.empty
        and (
            data.actions.get("rights_ratio", pd.Series(0, index=data.actions.index)).fillna(0) > 0
        ).any()
    ):
        raise ValueError("包含配股事件，需要显式缴款模型；已停止回测")
    stocks = data.stocks.set_index("code")
    bars = data.daily.set_index(["date", "code"]).sort_index()
    state = data.states.set_index(["date", "code"]) if not data.states.empty else pd.DataFrame()
    actions = {}
    if not data.actions.empty:
        for d, g in data.actions.groupby("ex_date"):
            actions[d] = g
    lots: dict[str, list[Lot]] = {}
    last_prices = {}
    cash = cfg.capital
    pending: dict[str, float] | None = None
    trades = []
    rejected = []
    equities = []
    snapshots = []
    peak = cfg.capital
    total_fees = 0.0
    traded = 0.0
    for i, date in enumerate(sessions):
        if cancelled and cancelled():
            raise InterruptedError("回测已取消")
        day = bars.loc[date] if date in bars.index.get_level_values(0) else pd.DataFrame()

        def qty(c):
            return sum(lot.quantity for lot in lots.get(c, []))

        for _, a in actions.get(date, pd.DataFrame()).iterrows():
            code = a.code
            held = qty(code)
            dividend = a.get("cash_per_share", 0)
            cash += held * (float(dividend) if pd.notna(dividend) else 0)
            ratio = a.get("share_ratio", 0)
            ratio = float(ratio) if pd.notna(ratio) else 0
            if ratio:
                for lot in lots.get(code, []):
                    old = lot.quantity
                    lot.quantity = int(math.floor(old * (1 + ratio)))
                    lot.cost = lot.cost * old / lot.quantity
                if code in last_prices:
                    last_prices[code] /= 1 + ratio
        # 开盘只用开盘/前日估值，绝不使用当日收盘估计可用资金。
        for code in lots:
            if code in day.index and not bool(day.loc[code].get("suspended", False)):
                last_prices[code] = float(day.loc[code].open)
        opening = cash + sum(qty(c) * last_prices.get(c, 0) for c in lots)
        budget = opening * cfg.daily_turnover
        if pending is not None:
            if (
                any(not np.isfinite(w) or w < 0 for w in pending.values())
                or sum(pending.values()) > 1 + 1e-9
            ):
                raise ValueError("信号权重必须非负且总和不超过1")
            targets = {c: opening * w for c, w in pending.items()}
            allcodes = sorted(set(lots) | set(targets))
            plans = []
            for code in allcodes:
                if code not in day.index:
                    rejected.append(dict(date=date, code=code, reason="缺行情，不能交易"))
                    continue
                bar = day.loc[code]
                board = stocks.loc[code, "board"]
                raw = (
                    float(bar.open)
                    if cfg.execution == "open"
                    else float(bar.amount / bar.volume) if bar.volume > 0 else 0
                )
                if raw <= 0 or bool(bar.get("suspended", False)) or bar.volume <= 0:
                    rejected.append(dict(date=date, code=code, reason="停牌或无有效成交"))
                    continue
                desired = lot_quantity(targets.get(code, 0) / raw, board, cfg.rules)
                delta = desired - qty(code)
                if not delta:
                    continue
                plans.append((delta > 0, code, delta, raw, bar, board))
            # 卖出优先，现金可当日用于买入。
            for is_buy, code, delta, raw, bar, board in sorted(plans, key=lambda x: (x[0], x[1])):
                side = "buy" if is_buy else "sell"
                reason = None
                st = False
                if not state.empty and (date, code) in state.index:
                    st = bool(state.loc[(date, code)].is_st)
                listing_day = len(
                    [d for d in data.dates() if stocks.loc[code, "list_date"] <= d <= date]
                )
                up = bar.get("limit_up", np.nan)
                down = bar.get("limit_down", np.nan)
                if pd.isna(up) or pd.isna(down):
                    if cfg.strict and date > cfg.rules.get("verified_through", "9999-12-31"):
                        raise ValueError(
                            "交易规则未核验到该日期；请导入交易所实际limit_up/limit_down，或核验并更新rules.yaml"
                        )
                    try:
                        theory = limits(
                            board,
                            st,
                            date,
                            float(bar.prev_close),
                            listing_day,
                            cfg.rules,
                            str(stocks.loc[code, "list_date"]),
                        )
                        if pd.isna(up):
                            up = theory[0]
                        if pd.isna(down):
                            down = theory[1]
                    except ValueError as exc:
                        reason = str(exc)
                if is_buy and up is not None and pd.notna(up) and raw >= up - 0.00001:
                    reason = "开盘涨停，保守拒买"
                if not is_buy and down is not None and pd.notna(down) and raw <= down + 0.00001:
                    reason = "开盘跌停，保守拒卖"
                if reason:
                    rejected.append(dict(date=date, code=code, reason=reason))
                    continue
                available = sum(lot.quantity for lot in lots.get(code, []) if lot.buy_date < date)
                desired = abs(delta) if is_buy else min(abs(delta), available)
                # 开盘容量还受上一真实交易日成交额约束，避免偷用整日量决定无限成交。
                past = data.daily.loc[
                    (data.daily.code == code) & (data.daily.date < date)
                ].sort_values("date")
                previous_amount = float(past.iloc[-1].amount) if not past.empty else 0
                capacity = min(float(bar.amount), previous_amount) * cfg.costs.participation
                maximum = min(desired, int(min(capacity, budget) / raw))
                quantity = (
                    lot_quantity(maximum, board, cfg.rules)
                    if is_buy
                    else (
                        maximum
                        if maximum == available
                        else (maximum // 100 * 100 if board in ("main", "chinext") else maximum)
                    )
                )
                if quantity <= 0:
                    rejected.append(
                        dict(date=date, code=code, reason="T+1、交易单位、换手或流动性约束")
                    )
                    continue
                participation = quantity * raw / max(previous_amount, 1)
                slip = (
                    cfg.costs.slippage + cfg.costs.impact * math.sqrt(participation)
                ) * cfg.costs.multiplier
                price = raw * (1 + slip if is_buy else 1 - slip)
                price = tick(price)
                if (is_buy and pd.notna(up) and up is not None and price > up) or (
                    not is_buy and pd.notna(down) and down is not None and price < down
                ):
                    rejected.append(dict(date=date, code=code, reason="滑点后超出限价"))
                    continue
                while is_buy and quantity > 0:
                    fees = fee(quantity * price, side, date, cfg.costs, cfg.rules)
                    if quantity * price + sum(fees.values()) <= cash + 1e-8:
                        break
                    step = cfg.rules.get("lot_rules", {}).get(board, {"step": 100})["step"]
                    quantity = lot_quantity(quantity - step, board, cfg.rules)
                if quantity <= 0:
                    rejected.append(dict(date=date, code=code, reason="可用现金不足"))
                    continue
                notional = quantity * price
                fees = fee(notional, side, date, cfg.costs, cfg.rules)
                costs = sum(fees.values())
                if not is_buy and cash + notional - costs < -1e-8:
                    rejected.append(
                        dict(date=date, code=code, reason="卖出费用超过可用现金与成交额")
                    )
                    continue
                realized = 0.0
                held_days = []
                if is_buy:
                    cash -= notional + costs
                    lots.setdefault(code, []).append(
                        Lot(quantity, date, (notional + costs) / quantity)
                    )
                else:
                    cash += notional - costs
                    remaining = quantity
                    for lot in lots.get(code, []):
                        if lot.buy_date >= date:
                            continue
                        sold = min(lot.quantity, remaining)
                        realized += sold * (price - lot.cost)
                        held_days.extend(
                            [(pd.Timestamp(date) - pd.Timestamp(lot.buy_date)).days] * bool(sold)
                        )
                        lot.quantity -= sold
                        remaining -= sold
                        if not remaining:
                            break
                    lots[code] = [lot for lot in lots[code] if lot.quantity > 0]
                    realized -= costs
                total_fees += costs
                traded += notional
                budget -= quantity * raw
                trades.append(
                    dict(
                        date=date,
                        code=code,
                        side=side,
                        quantity=quantity,
                        price=price,
                        notional=notional,
                        **fees,
                        slippage=abs(price - raw) * quantity,
                        realized_pnl=realized,
                        holding_days=float(np.mean(held_days)) if held_days else 0,
                    )
                )
        suspended_value = 0.0
        for code in lots:
            if code in day.index and float(day.loc[code].close) > 0:
                last_prices[code] = float(day.loc[code].close)
            value = qty(code) * last_prices.get(code, 0)
            if code not in day.index or bool(day.loc[code].get("suspended", False)):
                suspended_value += value
        net = cash + sum(qty(c) * last_prices.get(c, 0) for c in lots)
        peak = max(peak, net)
        equities.append(
            dict(
                date=date,
                equity=net,
                cash=cash,
                nav=net / cfg.capital,
                costs=total_fees,
                drawdown=net / peak - 1,
                suspended_weight=suspended_value / net if net else 0,
            )
        )
        for code in lots:
            if not qty(code):
                continue
            state_row = (
                state.loc[(date, code)]
                if not state.empty and (date, code) in state.index
                else pd.Series(dtype=object)
            )
            snapshots.append(
                dict(
                    date=date,
                    code=code,
                    quantity=qty(code),
                    sellable=sum(lot.quantity for lot in lots[code] if lot.buy_date < date),
                    price=last_prices.get(code, 0),
                    weight=qty(code) * last_prices.get(code, 0) / net,
                    industry=state_row.get("industry", "未知"),
                )
            )
        current = pd.Timestamp(date)
        next_date = pd.Timestamp(sessions[i + 1]) if i + 1 < len(sessions) else None
        rebalance = (
            cfg.frequency == "daily"
            or (
                cfg.frequency == "weekly"
                and (next_date is None or current.isocalendar()[:2] != next_date.isocalendar()[:2])
            )
            or (
                cfg.frequency == "monthly"
                and (
                    next_date is None
                    or (current.year, current.month) != (next_date.year, next_date.month)
                )
            )
        )
        # 第一日也可建信号，但仍只能次日执行。
        if rebalance or i == 0 or 1 - net / peak >= cfg.reduce_drawdown:
            pending = signal(date)
            if 1 - net / peak >= cfg.reduce_drawdown:
                pending = {c: w * 0.5 for c, w in pending.items()}
        else:
            pending = None
        if progress:
            progress(int((i + 1) / len(sessions) * 100), f"撮合 {date}")
    eq = pd.DataFrame(equities)
    ts = pd.DataFrame(trades)
    met = performance(eq, cfg.capital, benchmark)
    met.update(
        total_fees=total_fees,
        annual_turnover=traded / eq.equity.mean() * 252 / len(eq),
        cost_ratio=total_fees / cfg.capital,
        capacity_estimate=float(
            data.daily.groupby("code").amount.mean().median() * cfg.costs.participation * 20
        ),
        capacity_note="成交额参与率粗估，非可执行容量承诺",
    )
    if not ts.empty:
        sells = ts.loc[ts.side == "sell"]
        gain = sells.loc[sells.realized_pnl > 0].realized_pnl.mean()
        loss = sells.loc[sells.realized_pnl < 0].realized_pnl.mean()
        met.update(
            trade_win_rate=float((sells.realized_pnl > 0).mean()) if len(sells) else None,
            profit_loss_ratio=(
                float(gain / abs(loss)) if pd.notna(gain) and pd.notna(loss) and loss else None
            ),
            average_holding_days=float(sells.holding_days.mean()) if len(sells) else None,
        )
    return Result(
        eq,
        ts,
        pd.DataFrame(snapshots),
        pd.DataFrame(rejected),
        met,
        {
            "data_version": data.version,
            "data_provenance": data.provenance,
            "capital": cfg.capital,
            "start": start,
            "end": end,
            "execution": cfg.execution,
            "rules_version": cfg.rules.get("rule_version", "unknown"),
            "strict": cfg.strict,
            "warnings": ["研究回测不代表未来收益；日频成交与容量为保守近似；分红按税前。"]
            + ([] if cfg.strict else ["非严格研究模式：数据完整性未认证"]),
        },
    )
