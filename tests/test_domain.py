from copy import deepcopy
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from domain.factors import REGISTRY, compute, process, expression
from domain.strategy import StrategyConfig, screen, risk_report
from domain.backtest import BacktestConfig, CostConfig, run, limits, lot_quantity, fee, tick
from domain.metrics import price_change, vwap, turnover, volume_ratio, order_imbalance, performance
from domain.analysis import purged_splits, factor_analysis


@pytest.mark.parametrize("name", list(REGISTRY))
def test_no_lookahead_in_factor(research, name):
    date = research.dates()[140]
    expected = compute(research, date, [name])
    future = deepcopy(research)
    future.daily.loc[future.daily.date > date, ["open", "close", "adj_factor", "market_cap"]] = (
        999999
    )
    future.financials = pd.concat(
        [
            future.financials,
            pd.DataFrame(
                [
                    dict(
                        code="600000",
                        report_period="2023-06-30",
                        announce_date="2023-12-01",
                        version=2,
                        revenue=999999999,
                    )
                ]
            ),
        ],
        ignore_index=True,
    )
    pd.testing.assert_frame_equal(expected, compute(future, date, [name]))


def test_financial_announcement_next_session_and_restatement(research):
    assert research.financial_at("2023-01-10").empty
    original = research.financial_at("2023-01-11").set_index("code").loc["600000", "net_profit"]
    new = research.financials.iloc[[0]].copy()
    new["announce_date"] = "2023-02-01"
    new["version"] = 2
    new["net_profit"] = 99
    research.financials = pd.concat([research.financials, new])
    assert (
        research.financial_at("2023-01-31").set_index("code").loc["600000", "net_profit"]
        == original
    )
    assert research.financial_at("2023-02-02").set_index("code").loc["600000", "net_profit"] == 99


def test_missing_announce_uses_conservative_deadline(research):
    research.financials["announce_date"] = None
    assert research.financial_at("2023-04-28").empty
    assert not research.financial_at("2023-05-02").empty


def test_historical_members_and_delisted(research):
    research.members = pd.DataFrame(
        [
            dict(
                index_code="000300", code="600000", start_date="2023-01-03", end_date="2023-03-01"
            ),
            dict(index_code="000300", code="600001", start_date="2023-03-02", end_date=None),
        ]
    )
    assert research.universe("2023-02-01", "000300") == {"600000"}
    assert research.universe("2023-04-01", "000300") == {"600001"}
    research.stocks.loc[research.stocks.code == "600000", "delist_date"] = "2023-03-01"
    assert "600000" in research.universe("2023-02-01")
    assert "600000" not in research.universe("2023-03-02")


def test_cross_section_and_missing_not_zero():
    raw = pd.DataFrame(
        {"ep": [1.0, 2.0, np.nan, 1000.0], "bp": [np.nan] * 4}, index=["a", "b", "c", "d"]
    )
    sector = pd.Series(["x", "x", "x", "y"], index=raw.index)
    size = pd.Series([1e6, 2e6, 3e6, 4e6], index=raw.index)
    result, missing, stages = process(raw, sector, size)
    assert missing.loc["c", "ep"]
    assert stages["filled"].loc["c", "ep"] == 1.5
    assert result.bp.isna().all()
    assert result.ep.notna().all()


def test_safe_expression():
    f = pd.DataFrame({"ep": [1.0, 2.0], "bp": [2.0, 3.0]})
    assert expression("ep+bp*0.5", f).tolist() == [2.0, 3.5]
    assert expression("-ep/2", f).tolist() == [-0.5, -1]
    with pytest.raises(ValueError):
        expression('__import__("os").system("whoami")', f)
    with pytest.raises(ValueError):
        expression("ep.__class__", f)


def cfg(rules, **kw):
    return BacktestConfig(
        capital=100000,
        frequency="daily",
        daily_turnover=2,
        strict=True,
        rules=rules,
        costs=CostConfig(multiplier=0, participation=1),
        **kw,
    )


def test_known_answer_three_stocks_daily_equity(research, rules):
    d = deepcopy(research)
    dates = d.dates()[:4]
    codes = ["600000", "600001", "600002"]
    d.daily = d.daily.loc[d.daily.date.isin(dates) & d.daily.code.isin(codes)].copy()
    for i, date in enumerate(dates):
        d.daily.loc[d.daily.date == date, ["open", "close", "high", "low", "prev_close"]] = [
            10,
            10 + i,
            14,
            9,
            10,
        ]
        d.daily.loc[d.daily.date == date, ["limit_up", "limit_down"]] = [20, 1]
    weights = dict.fromkeys(codes, 0.3)
    result = run(
        d, dates[0], dates[-1], lambda date: weights if date == dates[0] else {}, cfg(rules)
    )
    # 第一天现金；第二天各买3000股按10元，收盘11；第三天按10元全部卖出。
    np.testing.assert_allclose(result.equity.equity, [100000, 109000, 100000, 100000], atol=1e-6)
    assert result.trades.iloc[0].date == dates[1]
    assert (result.equity.cash >= 0).all()


def test_limit_up_no_buy_down_no_sell(research, rules):
    d = deepcopy(research)
    dates = d.dates()[:4]
    d.daily = d.daily.loc[d.daily.date.isin(dates) & (d.daily.code == "600000")].copy()
    d.daily[["open", "close", "prev_close"]] = 10.0
    d.daily[["high", "low"]] = [12.0, 8.0]
    d.daily[["limit_up", "limit_down"]] = [11.0, 9.0]
    d.daily.loc[d.daily.date == dates[1], "open"] = 11
    blocked = run(d, dates[0], dates[-1], lambda date: {"600000": 1}, cfg(rules))
    assert dates[1] not in blocked.trades.date.tolist()
    assert blocked.rejected.reason.str.contains("涨停").any()
    d.daily.loc[d.daily.date == dates[1], "open"] = 10
    d.daily.loc[d.daily.date >= dates[2], ["open", "close"]] = 9
    forced = run(
        d, dates[0], dates[-1], lambda date: {"600000": 1} if date == dates[0] else {}, cfg(rules)
    )
    assert not (forced.trades.side == "sell").any()
    assert forced.holdings.quantity.iloc[-1] == 10000
    assert forced.equity.equity.iloc[-1] == 90000


def test_suspension_and_tplusone(research, rules):
    dates = research.dates()[:5]
    research.daily.loc[
        (research.daily.code == "600000") & (research.daily.date >= dates[2]), "suspended"
    ] = True
    result = run(
        research,
        dates[0],
        dates[-1],
        lambda d: {"600000": 0.5} if d == dates[0] else {},
        cfg(rules),
    )
    assert result.holdings.loc[result.holdings.date == dates[1], "sellable"].iloc[0] == 0
    assert not (result.trades.side == "sell").any()
    assert result.equity.suspended_weight.iloc[-1] > 0


def test_corporate_actions_not_double_counted(research, rules):
    d = deepcopy(research)
    dates = d.dates()[:4]
    d.daily = d.daily.loc[d.daily.date.isin(dates) & (d.daily.code == "600000")].copy()
    d.daily[["open", "high", "low", "close", "prev_close"]] = 10.0
    d.daily[["limit_up", "limit_down"]] = [20, 1]
    d.daily.loc[d.daily.date >= dates[2], ["open", "high", "low", "close", "prev_close"]] = 4.5
    d.daily.loc[d.daily.date >= dates[2], "adj_factor"] = 10 / 4.5
    d.actions = pd.DataFrame(
        [dict(code="600000", ex_date=dates[2], cash_per_share=1, share_ratio=1, rights_ratio=0)]
    )
    result = run(d, dates[0], dates[-1], lambda date: {"600000": 1}, cfg(rules))
    np.testing.assert_allclose(result.equity.equity, [100000] * 4, atol=1e-6)


def test_cash_shortfall_and_costs(research, rules):
    dates = research.dates()[:5]
    c = replace(cfg(rules), costs=CostConfig(participation=1))
    result = run(research, dates[0], dates[-1], lambda d: {"600000": 1}, c)
    assert result.equity.cash.min() >= -1e-8
    assert result.metrics["total_fees"] > 0
    assert fee(1000, "buy", "2024-01-01", CostConfig(), rules)["commission"] == 5
    assert fee(10000, "sell", "2023-08-25", CostConfig(), rules)["stamp"] == 10
    assert fee(10000, "sell", "2023-08-28", CostConfig(), rules)["stamp"] == 5


def test_lots_rules_and_half_up(rules):
    assert lot_quantity(199, "star", rules) == 0
    assert lot_quantity(201, "star", rules) == 201
    assert lot_quantity(199, "main", rules) == 100
    assert lot_quantity(101, "bse", rules) == 101
    assert tick(10.005) == 10.01
    assert limits("chinext", True, "2023-01-01", 10, 100, rules) == (12, 8)
    assert limits("main", False, "2023-05-01", 10, 1, rules, "2023-05-01") == (None, None)
    assert limits("main", False, "2022-05-01", 10, 1, rules, "2022-05-01") == (14.4, 6.4)


def test_strict_rejects_missing_states_adjustment_and_realtime(research, rules):
    dates = research.dates()[:3]
    research.states = research.states.iloc[1:]
    with pytest.raises(ValueError, match="历史状态"):
        run(research, dates[0], dates[-1], lambda d: {}, cfg(rules))
    research.provenance = "realtime"
    with pytest.raises(ValueError, match="实时"):
        run(research, dates[0], dates[-1], lambda d: {}, cfg(rules))


def test_risk_and_screen(research):
    c = StrategyConfig(
        weights={"momentum_20": 1, "reversal_5": 1},
        min_listing_days=20,
        min_amount=0,
        holdings=10,
        neutralize=False,
    )
    result = screen(research, research.dates()[-1], c)
    assert 0 < len(result) <= 10
    assert result.weight.sum() <= 1
    assert result.weight.nlargest(5).sum() <= 0.4 + 1e-10
    assert result.groupby("industry").weight.sum().max() <= 0.25 + 1e-10
    report = risk_report(result, pd.DataFrame(), c)
    assert not report["issues"]
    assert any(col.startswith("contribution:") for col in result)
    assert "status" in result
    with pytest.raises(ValueError):
        StrategyConfig(weights={"ep": -1})


def test_shared_metrics():
    assert price_change(11, 10) == pytest.approx(0.1)
    assert vwap(100, 10) == 10
    assert turnover(100, 1000) == 0.1
    assert volume_ratio(200, [100] * 5) == 2
    assert order_imbalance([100], [50]) == pytest.approx(1 / 3)
    assert np.isnan(vwap(100, 0))


def test_purge_embargo_no_future_label():
    dates = pd.Series(np.repeat(pd.bdate_range("2020-01-01", periods=100).strftime("%Y-%m-%d"), 3))
    unique = sorted(dates.unique())
    for train, test in purged_splits(dates, horizon=5, embargo=2):
        assert unique.index(dates.iloc[test].min()) - unique.index(dates.iloc[train].max()) >= 8


def test_factor_analysis_output(research):
    dates = research.dates()
    output = factor_analysis(research, "momentum_20", dates[100], dates[125])
    assert not output["series"].empty
    assert set(output["layers"].columns) == {1, 2, 3, 4, 5}
    assert output["series"].date.max() <= dates[124]


def test_ml_train_only_and_embargo():
    from domain.analysis import walk_forward_ml

    dates = pd.Series(np.repeat(pd.bdate_range("2020-01-01", periods=70).strftime("%Y-%m-%d"), 3))
    features = pd.DataFrame({"a": np.sin(np.arange(len(dates))), "b": np.arange(len(dates)) % 5})
    labels = pd.Series(features.a * 0.02 + features.b * 0.001)
    output = walk_forward_ml(features, labels, dates, horizon=2, embargo=1)
    assert output["predictions"].notna().sum() > 0
    assert output["predictions"].iloc[:50].isna().all()
    with pytest.raises(ValueError):
        list(purged_splits(dates.iloc[:5]))


def test_reject_rights_and_unexplained_adjustment(research, rules):
    research.daily.loc[research.daily.date >= research.dates()[2], "adj_factor"] = 2
    with pytest.raises(ValueError, match="现金行动"):
        research.check_history(research.dates()[:5])
    research.actions = pd.DataFrame(
        [dict(code="600000", ex_date=research.dates()[2], rights_ratio=0.2)]
    )
    with pytest.raises(ValueError, match="配股"):
        run(
            research,
            research.dates()[0],
            research.dates()[2],
            lambda d: {},
            replace(cfg(rules), strict=False),
        )


def test_benchmark_metrics_and_risk_warning():
    dates = ["2023-01-03", "2023-01-04", "2023-01-05"]
    equity = pd.DataFrame({"date": dates, "equity": [100.0, 80.0, 90.0]})
    m = performance(equity, 100, pd.Series([100.0, 90.0, 95.0], index=dates))
    assert m["max_drawdown"] == pytest.approx(0.2)
    assert m["tracking_error"] > 0
    holdings = pd.DataFrame({"code": ["600000"], "weight": [0.9], "industry": ["bank"]})
    assert risk_report(holdings, equity, StrategyConfig())["issues"]


def test_rules_after_verified_date_require_security_limits(research, rules):
    d = deepcopy(research)
    d.daily[["limit_up", "limit_down"]] = np.nan
    rules["verified_through"] = "2022-12-31"
    dates = d.dates()[:3]
    with pytest.raises(ValueError, match="规则未核验"):
        run(d, dates[0], dates[-1], lambda date: {"600000": 1}, cfg(rules))


def test_missing_daily_and_empty_not_silent_cash_curve(research):
    d = deepcopy(research)
    d.daily = d.daily.loc[d.daily.date != d.dates()[1]]
    with pytest.raises(ValueError, match="缺失交易日"):
        d.check_history(d.dates()[:5])
    d.daily = d.daily.iloc[:0]
    with pytest.raises(ValueError, match="没有日线"):
        d.check_history(d.dates()[:5])


def test_recovery_metric_from_trough():
    m = performance(
        pd.DataFrame(
            {
                "date": ["2023-01-03", "2023-01-04", "2023-01-05", "2023-01-06"],
                "equity": [100.0, 80.0, 90.0, 100.0],
            }
        ),
        100,
    )
    assert m["recovery_days"] == 2
    assert m["drawdown_trough_date"] == "2023-01-04"
