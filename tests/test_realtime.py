from datetime import datetime, timedelta
from dataclasses import replace
import asyncio
import pytest
import httpx
from domain.realtime import Quote, AlertEngine, market_phase
from domain.metrics import price_change, vwap, turnover
from infrastructure.realtime import (
    parse_sina,
    parse_tencent,
    SinaBatchSource,
    Collector,
    Archive,
    SH,
)
from application.service import read_yaml, ROOT


@pytest.fixture
def quote():
    t = datetime(2026, 10, 20, 10, 0, 0, tzinfo=SH)
    return Quote(
        "600000",
        "测试证券",
        10,
        10,
        10,
        10,
        10,
        10000,
        100000,
        t,
        t,
        [10] * 5,
        [100] * 5,
        [10.01] * 5,
        [100] * 5,
        11,
        9,
        1e6,
        "fixture",
    )


def test_sina_and_tencent_units(quote):
    f = ["测试", "10", "10", "11", "11", "9", "10", "11", "12300", "135300"]
    f += ["100", "10"] * 5 + ["200", "11"] * 5 + ["2026-10-20", "10:00:00", "00"]
    text = 'var hq_str_sh600000="' + ",".join(f) + '";'
    q = parse_sina(text, quote.recv_ts)[0]
    assert q.volume == 12300
    assert q.bid_volumes[0] == 100
    assert q.age(quote.recv_ts) == 0
    f = ["0"] * 50
    for i, v in {
        1: "测试",
        3: "11",
        4: "10",
        5: "10",
        6: "123",
        30: "20261020100000",
        33: "11",
        34: "9",
        37: "13.53",
        47: "11",
        48: "9",
    }.items():
        f[i] = v
    q2 = parse_tencent('v_sh600000="' + "~".join(f) + '";', quote.recv_ts)[0]
    assert q2.volume == 12300 and q2.amount == pytest.approx(135300)
    assert q2.limit_up == 11
    assert parse_sina("broken", quote.recv_ts) == []


def test_stale_stop_debounce_and_dual_evidence(quote):
    rules = read_yaml(ROOT / "configs/alerts.yaml")
    e = AlertEngine(rules)
    q = replace(quote, price=11)
    assert any(a.kind == "limit_up" for a in e.ingest(q, q.quote_ts))
    assert not e.ingest(q, q.quote_ts)
    q2 = replace(q, quote_ts=q.quote_ts + timedelta(seconds=3))
    assert not e.ingest(q2, q2.quote_ts)
    stale = replace(q, code="600001")
    assert not e.ingest(stale, stale.quote_ts + timedelta(seconds=16))
    # 名称含ST不能自动推断5%；使用卖盘空+涨幅的独立证据。
    q3 = replace(
        quote, code="600002", name="ST测试", price=10.6, limit_up=None, ask_volumes=[0] * 5
    )
    assert any(a.kind == "limit_up" for a in e.ingest(q3, q3.quote_ts))
    broken = replace(q, price=10.9, quote_ts=q.quote_ts + timedelta(seconds=6))
    assert any(a.kind == "break_limit" for a in e.ingest(broken, broken.quote_ts))
    assert e.break_count["600000"] == 1


def test_surge_and_metrics_consistency(quote):
    e = AlertEngine(read_yaml(ROOT / "configs/alerts.yaml"))
    e.ingest(quote, quote.quote_ts)
    q = replace(quote, price=10.2, quote_ts=quote.quote_ts + timedelta(seconds=60))
    events = e.ingest(q, q.quote_ts)
    assert any(a.kind == "surge" for a in events)
    ind = e.indicators(q)
    assert ind["change"] == price_change(q.price, q.prev_close)
    assert ind["vwap"] == vwap(q.amount, q.volume)
    assert ind["turnover"] == turnover(q.volume, q.float_shares)


def test_market_calendar_and_lunch(quote):
    dates = {"2026-10-20"}
    assert market_phase(quote.quote_ts, dates) == "连续竞价"
    assert market_phase(quote.quote_ts.replace(hour=12), dates) == "午休"
    assert market_phase(quote.quote_ts.replace(hour=14, minute=58), dates) == "收盘集合竞价"
    assert "休市" in market_phase(quote.quote_ts, set())


def test_batch_fetch_mocked_and_backoff(tmp_path, quote):
    async def main():
        calls = []

        async def handler(request):
            calls.append(str(request.url))
            return httpx.Response(503, text="offline")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = SinaBatchSource(client)
            codes = [str(600000 + i) for i in range(1001)]
            assert await source.fetch(codes) == []
            assert len(calls) == 3
            assert source.errors

        class Failed:
            name = "failed"

            async def fetch(self, codes):
                return []

        class Recovered:
            name = "recovered"

            async def fetch(self, codes):
                return [replace(quote, quote_ts=datetime.now(SH))]

        collector = Collector(
            [Failed(), Failed()],
            AlertEngine({}),
            Archive(tmp_path),
            ["600000"],
            {"600000"},
            {"2026-10-20"},
        )
        for _ in range(5):
            batch = await collector.cycle()
        assert batch["backoff"] >= 6
        assert collector.degraded
        collector.sources = [Recovered()]
        collector.index = 0
        batch = await collector.cycle()
        assert batch["recovered"] and not collector.degraded

    asyncio.run(main())


def test_archive_replay_exact_watchlist(tmp_path, quote):
    rules = read_yaml(ROOT / "configs/alerts.yaml")
    e = AlertEngine(rules)
    q = replace(quote, price=11)
    alerts = e.ingest(q, q.quote_ts)
    archive = Archive(tmp_path)
    archive.write([q], alerts, {"600000"})
    replay, notice = archive.replay("2026-10-20", rules)
    assert len(replay) == len(alerts)
    assert replay[0]["kind"] == alerts[0].kind
    assert list(tmp_path.glob("realtime/2026-10-20/alerts-*.parquet"))
    assert "分钟" in notice


def test_all_remaining_alert_types(quote):
    rules = read_yaml(ROOT / "configs/alerts.yaml")
    engine = AlertEngine(rules)
    q = replace(
        quote, price=11, bid_prices=[11] * 5, bid_volumes=[10000] * 5, volume=10000, amount=100000
    )
    engine.ingest(q, q.quote_ts)
    later = replace(
        q,
        quote_ts=q.quote_ts + timedelta(seconds=300),
        bid_volumes=[100] * 5,
        amount=20000000,
        volume=2000000,
    )
    kinds = {a.kind for a in engine.ingest(later, later.quote_ts)}
    assert "queue_drop" in kinds and "volume_spike" in kinds
    engine2 = AlertEngine(rules)
    high = replace(quote, price=10.6, amount=110000, volume=10000)
    assert "pullback" in {a.kind for a in engine2.ingest(high, high.quote_ts)}
    low = replace(high, price=9, quote_ts=high.quote_ts + timedelta(seconds=60))
    kinds = {a.kind for a in engine2.ingest(low, low.quote_ts)}
    assert "plunge" in kinds and "limit_down" in kinds


def test_collector_once_flush_and_archive(tmp_path, quote):
    async def check():
        class Source:
            name = "test"

            async def fetch(self, codes):
                return [replace(quote, quote_ts=datetime.now(SH), recv_ts=datetime.now(SH))]

        collector = Collector(
            [Source()], AlertEngine({}), Archive(tmp_path), ["600000"], {"600000"}, set()
        )
        batches = []
        await collector.run(batches.append, lambda: False, once=True)
        assert batches and batches[0]["failure"] == 0
        assert list((tmp_path / "realtime").glob("*/*.parquet"))

    asyncio.run(check())
