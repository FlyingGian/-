import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
import pandas as pd
import numpy as np
from domain.data import ResearchData
from application.service import read_yaml, ROOT


@pytest.fixture
def rules():
    return read_yaml(ROOT / "configs/rules.yaml")


@pytest.fixture
def research():
    # 仅测试用确定性夹具，不写入产品数据库，不冒充真实行情。
    dates = pd.bdate_range("2023-01-03", periods=180).strftime("%Y-%m-%d").tolist()
    codes = [f"{600000+i:06d}" for i in range(15)]
    rows = []
    states = []
    valuations = []
    for i, c in enumerate(codes):
        price = 10 + i
        for j, d in enumerate(dates):
            prev = price
            price = prev * (1 + 0.0004 * (i - 6) + 0.002 * np.sin(j / 7 + i))
            rows.append(
                dict(
                    code=c,
                    date=d,
                    open=prev,
                    close=price,
                    high=max(prev, price) * 1.01,
                    low=min(prev, price) * 0.99,
                    prev_close=prev,
                    volume=10_000_000,
                    amount=price * 10_000_000,
                    adj_factor=1.0,
                    float_shares=100_000_000 + i * 1e7,
                    market_cap=price * (100_000_000 + i * 1e7),
                    suspended=False,
                    limit_up=round(prev * 1.1, 2),
                    limit_down=round(prev * 0.9, 2),
                )
            )
            states.append(
                dict(code=c, date=d, is_st=False, industry=f"sector{i%3}", delisting=False)
            )
            valuations.append(
                dict(
                    code=c,
                    date=d,
                    pe=10 + i,
                    pb=1 + i / 5,
                    ps=2 + i / 5,
                    pcf=8 + i,
                    dividend_yield=0.01 + i * 0.001,
                    ev_ebitda=8 + i,
                )
            )
    stocks = pd.DataFrame(
        [
            dict(code=c, name=f"测试证券{i}", board="main", list_date=dates[0], delist_date=None)
            for i, c in enumerate(codes)
        ]
    )
    financials = pd.DataFrame(
        [
            dict(
                code=c,
                report_period="2022-12-31",
                announce_date="2023-01-10",
                version=1,
                revenue=1e9 + i * 1e8,
                net_profit=1e8 + i * 1e7,
                equity=1e9,
                assets=2e9,
                operating_cf=8e7,
                gross_profit=3e8,
                ebitda=2e8,
                debt=1e9,
                cash=3e8,
                rd=1e7,
                goodwill=2e7,
                revenue_yoy=0.1 + i * 0.01,
                profit_yoy=0.1 + i * 0.01,
                revenue_acceleration=0.01 * i,
                institutional_ratio=0.1 + i * 0.01,
                pledge_ratio=0.01 * i,
                holders_change=-0.01 * i,
            )
            for i, c in enumerate(codes)
        ]
    )
    return ResearchData(
        pd.DataFrame(rows),
        stocks,
        pd.DataFrame({"date": dates, "is_open": True}),
        pd.DataFrame(states),
        financials,
        pd.DataFrame(valuations),
        version="test-fixture",
        provenance="synthetic-test-only",
    )
