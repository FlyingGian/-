"""行情契约及无I/O异动状态机，时间均为带时区时间。"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from collections import deque
import math
from domain.metrics import price_change, vwap, order_imbalance, turnover, volume_ratio


@dataclass
class Quote:
    code: str
    name: str
    price: float
    prev_close: float
    open: float
    high: float
    low: float
    volume: float
    amount: float
    quote_ts: datetime
    recv_ts: datetime
    bid_prices: list[float] = field(default_factory=list)
    bid_volumes: list[float] = field(default_factory=list)
    ask_prices: list[float] = field(default_factory=list)
    ask_volumes: list[float] = field(default_factory=list)
    limit_up: float | None = None
    limit_down: float | None = None
    float_shares: float = 0
    source: str = "unknown"

    def age(self, now: datetime) -> float:
        return max(0, (now - self.quote_ts).total_seconds())

    def row(self) -> dict:
        r = asdict(self)
        for c in ("quote_ts", "recv_ts"):
            r[c] = r[c].isoformat()
        return r


@dataclass
class Alert:
    code: str
    name: str
    kind: str
    quote_ts: str
    price: float
    change: float
    source: str
    evidence: str


class AlertEngine:
    def __init__(self, rules: dict, stale_stop: float = 15):
        self.rules = rules.get("rules", rules)
        self.stale_stop = stale_stop
        self.windows: dict[str, deque] = {}
        self.last_alert = {}
        self.was_limit = {}
        self.break_count = {}
        self.last_break = {}

    def indicators(self, q: Quote) -> dict:
        window = self.windows.get(q.code, deque())

        def old(seconds):
            eligible = [x for x in window if (q.quote_ts - x.quote_ts).total_seconds() >= seconds]
            return eligible[-1] if eligible else None

        minute = old(60)
        five = old(300)
        change = price_change(q.price, q.prev_close)
        # 没有证券级上下限则仅盘口证据，绝不依据名称ST推断。
        ask_empty = len(q.ask_volumes) == 5 and sum(q.ask_volumes) == 0
        bid_empty = len(q.bid_volumes) == 5 and sum(q.bid_volumes) == 0
        up = (q.limit_up is not None and q.price >= q.limit_up - 0.001) or (
            ask_empty and change > 0.045
        )
        down = (q.limit_down is not None and q.price <= q.limit_down + 0.001) or (
            bid_empty and change < -0.045
        )
        queue = (q.bid_prices[0] * q.bid_volumes[0]) if q.bid_prices and q.bid_volumes else 0
        old_queue = (
            (five.bid_prices[0] * five.bid_volumes[0])
            if five and five.bid_prices and five.bid_volumes
            else 0
        )
        # 使用真实交易分钟而非午休经过时长。
        hour = q.quote_ts.hour + q.quote_ts.minute / 60
        elapsed = max(1, min(120, max(0, (hour - 9.5) * 60)) + min(120, max(0, (hour - 13) * 60)))
        five_increase = max(0, q.amount - five.amount) if five else math.nan
        average = q.amount / elapsed
        return dict(
            change=change,
            vwap=vwap(q.amount, q.volume),
            turnover=turnover(q.volume, q.float_shares),
            imbalance=order_imbalance(q.bid_volumes, q.ask_volumes),
            speed60=price_change(q.price, minute.price) if minute else math.nan,
            limit_up=up,
            limit_down=down,
            queue=queue,
            queue_drop=(old_queue - queue) / old_queue if old_queue > 0 else math.nan,
            volume_spike=five_increase / 5 / average if average > 0 else math.nan,
            volume_ratio=volume_ratio(q.volume, []),
            break_count=self.break_count.get(q.code, 0),
        )

    def ingest(self, q: Quote, now: datetime) -> list[Alert]:
        if (
            q.price <= 0
            or q.prev_close <= 0
            or q.age(now) > self.stale_stop
            or (q.quote_ts - now).total_seconds() > 5
        ):
            return []
        window = self.windows.setdefault(q.code, deque(maxlen=400))
        if window and q.quote_ts <= window[-1].quote_ts:
            return []
        ind = self.indicators(q)
        previous = self.was_limit.get(q.code, False)
        break_event = previous and not ind["limit_up"]
        if break_event:
            previous_price = window[-1].price if window else q.price
            break_event = q.price <= previous_price - 0.01 + 1e-8
        if break_event and (q.quote_ts.timestamp() - self.last_break.get(q.code, 0) >= 10):
            self.break_count[q.code] = self.break_count.get(q.code, 0) + 1
            self.last_break[q.code] = q.quote_ts.timestamp()
        predicates = {
            "limit_up": lambda t: ind["limit_up"],
            "limit_down": lambda t: ind["limit_down"],
            "break_limit": lambda t: break_event,
            "surge": lambda t: ind["speed60"] >= t,
            "plunge": lambda t: ind["speed60"] <= t,
            "volume_spike": lambda t: ind["volume_spike"] >= t,
            "pullback": lambda t: q.price < ind["vwap"] * (1 - t) and ind["change"] > 0.03,
            "queue_drop": lambda t: ind["limit_up"] and ind["queue_drop"] > t,
        }
        alerts = []
        for kind, config in self.rules.items():
            if not config.get("enabled", True) or kind not in predicates:
                continue
            key = (q.code, kind)
            elapsed = q.quote_ts.timestamp() - self.last_alert.get(key, 0)
            if elapsed >= config.get("cooldown", 120) and predicates[kind](
                config.get("threshold", 0)
            ):
                alerts.append(
                    Alert(
                        q.code,
                        q.name,
                        kind,
                        q.quote_ts.isoformat(),
                        q.price,
                        ind["change"],
                        q.source,
                        "价格/五档盘口" if "limit" in kind else f"阈值={config.get('threshold')}",
                    )
                )
                self.last_alert[key] = q.quote_ts.timestamp()
        self.was_limit[q.code] = bool(ind["limit_up"])
        window.append(q)
        while window and (q.quote_ts - window[0].quote_ts).total_seconds() > 310:
            window.popleft()
        return alerts


def market_phase(now: datetime, open_dates: set[str]) -> str:
    if now.date().isoformat() not in open_dates:
        return "休市/日历未覆盖"
    t = now.hour * 60 + now.minute + now.second / 60
    if 555 <= t < 565:
        return "开盘集合竞价"
    if 570 <= t <= 690 or 780 <= t < 897:
        return "连续竞价"
    if 897 <= t <= 900:
        return "收盘集合竞价"
    if 690 < t < 780:
        return "午休"
    return "盘外"
