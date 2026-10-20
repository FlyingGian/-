"""批量HTTP、双源故障转移、限流、异步归档和回放。"""

from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from collections import defaultdict
import asyncio
import re
import time
import uuid
import httpx
import pandas as pd
from domain.realtime import Quote, Alert, AlertEngine, market_phase

SH = ZoneInfo("Asia/Shanghai")


def symbol(code: str) -> str:
    return (
        "sh" if code.startswith("6") else "bj" if code.startswith(("4", "8", "92")) else "sz"
    ) + code


def parse_sina(text: str, recv: datetime) -> list[Quote]:
    out = []
    for code, body in re.findall(r'hq_str_(?:sh|sz|bj)(\d{6})="([^"]*)"', text):
        f = body.split(",")
        if len(f) < 32:
            continue
        try:
            q = Quote(
                code,
                f[0],
                float(f[3]),
                float(f[2]),
                float(f[1]),
                float(f[4]),
                float(f[5]),
                float(f[8]),
                float(f[9]),
                datetime.fromisoformat(f[30] + "T" + f[31]).replace(tzinfo=SH),
                recv,
                [float(f[i]) for i in (11, 13, 15, 17, 19)],
                [float(f[i]) for i in (10, 12, 14, 16, 18)],
                [float(f[i]) for i in (21, 23, 25, 27, 29)],
                [float(f[i]) for i in (20, 22, 24, 26, 28)],
                source="新浪",
            )
            if q.price > 0:
                out.append(q)
        except (ValueError, IndexError):
            continue
    return out


def parse_tencent(text: str, recv: datetime) -> list[Quote]:
    out = []
    for code, body in re.findall(r'v_(?:sh|sz|bj)(\d{6})="([^"]*)"', text):
        f = body.split("~")
        if len(f) < 46:
            continue
        try:
            q = Quote(
                code,
                f[1],
                float(f[3]),
                float(f[4]),
                float(f[5]),
                float(f[33]),
                float(f[34]),
                float(f[6]) * 100,
                float(f[37]) * 10000,
                datetime.strptime(f[30], "%Y%m%d%H%M%S").replace(tzinfo=SH),
                recv,
                [float(f[i]) for i in (9, 11, 13, 15, 17)],
                [float(f[i]) * 100 for i in (10, 12, 14, 16, 18)],
                [float(f[i]) for i in (19, 21, 23, 25, 27)],
                [float(f[i]) * 100 for i in (20, 22, 24, 26, 28)],
                limit_up=float(f[47]) if len(f) > 48 and float(f[47]) > 0 else None,
                limit_down=float(f[48]) if len(f) > 48 and float(f[48]) > 0 else None,
                source="腾讯",
            )
            if q.price > 0:
                out.append(q)
        except (ValueError, IndexError):
            continue
    return out


class TokenBucket:
    def __init__(self, rate: float = 6, capacity: float = 6):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            if self.tokens < 1:
                await asyncio.sleep((1 - self.tokens) / self.rate)
                self.updated = time.monotonic()
                self.tokens = 0
            else:
                self.tokens -= 1


class RealtimeSource(ABC):
    batch_size = 500
    min_interval = 1
    name = "unknown"

    @abstractmethod
    async def fetch(self, codes: list[str]) -> list[Quote]: ...
    def capabilities(self):
        return {"batch_size": self.batch_size, "five_levels": True, "volume_unit": "shares"}


class BatchSource(RealtimeSource):
    def __init__(self, client: httpx.AsyncClient, batch_size: int = 500, concurrency: int = 6):
        self.client = client
        self.batch_size = min(batch_size, 500)
        self.sem = asyncio.Semaphore(min(concurrency, 8))
        self.limiter = TokenBucket(6, 6)
        self.errors = []

    async def fetch(self, codes):
        self.errors = []

        async def one(batch):
            async with self.sem:
                await self.limiter.acquire()
                try:
                    response = await self.client.get(
                        self.url + ",".join(map(symbol, batch)), headers=self.headers, timeout=5
                    )
                    response.raise_for_status()
                    return self.parser(
                        response.content.decode("gb18030", errors="replace"), datetime.now(SH)
                    )
                except (httpx.HTTPError, ValueError) as exc:
                    self.errors.append(type(exc).__name__ + ": " + str(exc)[:180])
                    return []

        groups = [codes[i : i + self.batch_size] for i in range(0, len(codes), self.batch_size)]
        results = await asyncio.gather(*(one(g) for g in groups))
        return [q for group in results for q in group]


class SinaBatchSource(BatchSource):
    name = "新浪"
    url = "https://hq.sinajs.cn/list="
    headers = {
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "AshareQuant/1.0 personal-research",
    }
    parser = staticmethod(parse_sina)


class TencentBatchSource(BatchSource):
    name = "腾讯"
    url = "https://qt.gtimg.cn/q="
    headers = {"User-Agent": "AshareQuant/1.0 personal-research"}
    parser = staticmethod(parse_tencent)


class Archive:
    def __init__(self, root: Path):
        self.root = Path(root) / "realtime"

    def write(self, quotes: list[Quote], alerts: list[Alert], watchlist: set[str]):
        if not quotes and not alerts:
            return
        now = datetime.now(SH).isoformat()
        bydate = defaultdict(list)
        for q in quotes:
            bydate[q.quote_ts.date().isoformat()].append(q)
        for date, group in bydate.items():
            path = self.root / date
            path.mkdir(parents=True, exist_ok=True)
            rows = []
            aggregate = {}
            for q in group:
                r = q.row()
                r["write_ts"] = now
                if q.code in watchlist:
                    r["kind"] = "watch"
                    rows.append(r)
                minute = q.quote_ts.replace(second=0, microsecond=0).isoformat()
                key = (q.code, minute)
                if key not in aggregate:
                    a = r.copy()
                    a.update(
                        kind="minute",
                        minute=minute,
                        minute_open=q.price,
                        minute_high=q.price,
                        minute_low=q.price,
                        minute_close=q.price,
                    )
                    aggregate[key] = a
                else:
                    a = aggregate[key]
                    a["minute_high"] = max(a["minute_high"], q.price)
                    a["minute_low"] = min(a["minute_low"], q.price)
                    a["minute_close"] = q.price
                    for k in ("volume", "amount", "quote_ts", "recv_ts", "write_ts"):
                        a[k] = r[k]
            rows.extend(aggregate.values())
            pd.DataFrame(rows).to_parquet(
                path / f"quotes-{uuid.uuid4().hex}.parquet", index=False, compression="snappy"
            )
        if alerts:
            path = self.root / datetime.fromisoformat(alerts[0].quote_ts).date().isoformat()
            path.mkdir(parents=True, exist_ok=True)
            pd.DataFrame([asdict(a) | {"write_ts": now} for a in alerts]).to_parquet(
                path / f"alerts-{uuid.uuid4().hex}.parquet", index=False
            )

    def replay(self, date: str, rules: dict, exact: bool = True) -> tuple[list[dict], str]:
        path = self.root / date
        files = sorted(path.glob("quotes-*.parquet"))
        if not files:
            raise ValueError("该日没有行情归档")
        frame = pd.concat([pd.read_parquet(p) for p in files], ignore_index=True)
        if exact:
            frame = frame.loc[frame.kind == "watch"]
        engine = AlertEngine(rules)
        out = []
        for _, row in (
            frame.sort_values(["quote_ts", "code"]).drop_duplicates(["code", "quote_ts"]).iterrows()
        ):
            fields = {k: row[k] for k in Quote.__dataclass_fields__ if k in row}
            for c in ("quote_ts", "recv_ts"):
                fields[c] = datetime.fromisoformat(fields[c])
            for c in ("bid_prices", "bid_volumes", "ask_prices", "ask_volumes"):
                fields[c] = list(fields.get(c, []))
            for c in ("limit_up", "limit_down"):
                if c in fields and pd.isna(fields[c]):
                    fields[c] = None
            q = Quote(**fields)
            out.extend(asdict(a) for a in engine.ingest(q, q.quote_ts))
        return out, "自选逐笔可精确回放；分钟聚合不能验证全市场逐秒异动"


class Collector:
    def __init__(
        self,
        sources: list[RealtimeSource],
        engine: AlertEngine,
        archive: Archive,
        codes: list[str],
        watchlist: set[str],
        open_dates: set[str],
        interval: float = 3,
    ):
        self.sources = sources
        self.engine = engine
        self.archive = archive
        self.codes = codes
        self.watchlist = watchlist
        self.open_dates = open_dates
        self.interval = max(1, min(5, interval))
        self.store = {}
        self.index = 0
        self.failures = 0
        self.degraded = False
        self.gap = False

    async def cycle(self) -> dict:
        start = time.monotonic()
        codes = sorted(self.watchlist) if self.degraded and self.watchlist else self.codes
        source = self.sources[self.index]
        quotes = await source.fetch(codes)
        expected = set(codes)
        quotes = [q for q in quotes if q.code in expected]
        failure = 1 - len({q.code for q in quotes}) / max(1, len(codes))
        alerts = []
        now = datetime.now(SH)
        for q in quotes:
            self.store[q.code] = q
            alerts.extend(self.engine.ingest(q, now))
        self.failures = self.failures + 1 if failure > 0.3 else 0
        recovered = self.gap and failure <= 0.3
        self.gap = failure > 0.3
        if failure > 0.3:
            self.degraded = True
        elif self.degraded:
            self.degraded = False  # 恢复后下一轮探测全市场。
        if self.failures == 3 and len(self.sources) > 1:
            self.index = (self.index + 1) % len(self.sources)
        backoff = (
            min(60, self.interval * 2 ** min(5, max(0, self.failures - 4)))
            if self.failures >= 5
            else self.interval
        )
        return dict(
            quotes=quotes,
            alerts=alerts,
            source=source.name,
            failure=failure,
            elapsed=time.monotonic() - start,
            degraded=self.degraded,
            backoff=backoff,
            recovered=recovered,
            notice="恢复连接：需要日线回补，实时快照不回写历史库" if recovered else "",
        )

    async def run(self, on_batch, cancelled, once: bool = False):
        pending = []
        alerts = []
        last_write = time.monotonic()
        try:
            while not cancelled():
                phase = market_phase(datetime.now(SH), self.open_dates)
                if not once and phase not in ("连续竞价", "开盘集合竞价", "收盘集合竞价"):
                    on_batch(
                        {
                            "phase": phase,
                            "quotes": [],
                            "alerts": [],
                            "source": self.sources[self.index].name,
                        }
                    )
                    await asyncio.sleep(0.5)
                    continue
                batch = await self.cycle()
                on_batch(batch)
                pending.extend(batch["quotes"])
                alerts.extend(batch["alerts"])
                if time.monotonic() - last_write >= 60 or once:
                    await asyncio.to_thread(self.archive.write, pending, alerts, self.watchlist)
                    pending = []
                    alerts = []
                    last_write = time.monotonic()
                if once:
                    break
                deadline = time.monotonic() + max(0.1, batch["backoff"] - batch["elapsed"])
                while time.monotonic() < deadline and not cancelled():
                    await asyncio.sleep(0.2)
        finally:
            if pending or alerts:
                await asyncio.to_thread(self.archive.write, pending, alerts, self.watchlist)
