import pandas as pd
import numpy as np
import pytest
from infrastructure.storage import LocalRepository, normalize, validate_data, REQUIRED
from infrastructure.sources import LocalCSVSource, AkShareSource, TushareSource, board_of
from application.service import Service
from application.reports import export_table, export_report
from application.diagnostics import check_environment


def seed(repo, data):
    for kind in REQUIRED:
        frame = getattr(data, kind)
        if not frame.empty:
            repo.ingest(kind, frame, "synthetic-test-only")


def test_repository_roundtrip_and_snapshot(tmp_path, research):
    repo = LocalRepository(tmp_path)
    seed(repo, research)
    loaded = repo.load()
    before = loaded.version
    assert len(loaded.daily) == len(research.daily)
    assert loaded.stocks.code.iloc[0] == "600000"
    repo.audit("test", "does not change data version")
    assert repo.version() == before
    repo.ingest("daily", research.daily.iloc[:1].assign(close=research.daily.iloc[0].close + 0.01))
    assert repo.version() != before
    assert len(repo.load().daily) == len(research.daily)
    assert len(repo.query("SELECT code,count(*) AS n FROM daily GROUP BY code")) == 15


def test_validation_and_atomic_failure(tmp_path, research):
    repo = LocalRepository(tmp_path)
    repo.ingest("daily", research.daily)
    before = repo.version()
    with pytest.raises(ValueError):
        repo.ingest("daily", research.daily.iloc[:1].assign(close=-1))
    assert repo.version() == before
    issues = validate_data("daily", research.daily.iloc[:2].assign(volume=0, amount=2))
    assert any("零成交" in x["message"] for x in issues)
    with pytest.raises(ValueError):
        repo.ingest("states", pd.DataFrame({"code": ["600000"]}))


def test_financial_immutable_versions(tmp_path, research):
    repo = LocalRepository(tmp_path)
    repo.ingest("financials", research.financials)
    with pytest.raises(ValueError, match="禁止覆写"):
        repo.ingest("financials", research.financials.iloc[:1].assign(net_profit=99))
    repo.ingest(
        "financials",
        research.financials.iloc[:1].assign(net_profit=99, version=2, announce_date="2023-03-01"),
    )
    assert len(repo.load().financials) == 16


def test_price_adjustments_and_bom(tmp_path, research):
    repo = LocalRepository(tmp_path)
    f = research.daily.iloc[:10].copy()
    f["adj_factor"] = np.arange(1, len(f) + 1)
    repo.ingest("daily", f)
    raw = repo.get_price("600000", "2023-01-01", "2024-01-01", "none")
    hfq = repo.get_price("600000", "2023-01-01", "2024-01-01", "hfq")
    qfq = repo.get_price("600000", "2023-01-01", "2024-01-01", "qfq")
    assert hfq.close.iloc[-1] == pytest.approx(raw.close.iloc[-1] * 10)
    assert qfq.close.iloc[-1] == raw.close.iloc[-1]
    raw.to_csv(tmp_path / "daily.csv", encoding="utf-8-sig", index=False)
    loaded = LocalCSVSource(tmp_path).get("daily", "2023-01-01", "2024-01-01", ["600000"])
    assert len(loaded) == 10
    assert normalize(pd.DataFrame({"is_st": ["true", "0"]})).is_st.tolist() == [True, False]


def test_service_selection_backtest_export(tmp_path, research):
    s = Service(tmp_path / "data")
    seed(s.repo, research)
    s.save_settings(
        {
            "weights": {"momentum_20": 0.5, "reversal_5": 0.5},
            "min_listing_days": 20,
            "min_amount": 0,
            "holdings": 10,
        }
    )
    result = s.run_screen(research.dates()[100])
    assert not result.empty
    export_table(result, tmp_path / "selection.xlsx", {"data_version": s.repo.version()})
    assert (tmp_path / "selection.xlsx.json").exists()
    bt = s.run_backtest(research.dates()[90], research.dates()[105])
    assert len(bt.metrics["cost_sensitivity"]) == 4
    assert bt.metadata["config_hash"]
    report = export_report(bt, tmp_path / "reports")
    assert report.exists()
    assert (report.parent / "report.pdf").stat().st_size > 1000
    assert (report.parent / "equity.png").exists()
    assert (report.parent / "snapshot.json").exists()
    coverage = s.coverage()
    assert coverage["stocks"] == 15
    diagnostics = check_environment(s.repo.root)
    assert any(x["项目"] == "数据目录可写" and x["状态"] == "通过" for x in diagnostics)


def test_csv_import_cancel_and_resume(tmp_path, research):
    s = Service(tmp_path / "store")
    file = tmp_path / "stocks.csv"
    research.stocks.to_csv(file, index=False)
    with pytest.raises(InterruptedError):
        s.import_csv([str(file)], cancelled=lambda: True)
    s.import_csv([str(file)])
    assert len(s.repo.load().stocks) == 15
    with pytest.raises(ValueError):
        s.update("2023-01-01", "2023-02-01")


def test_datasource_units_mocked(monkeypatch):
    class API:
        def stock_zh_a_hist(self, **kwargs):
            return pd.DataFrame(
                {
                    "日期": ["2023-01-03"],
                    "开盘": [10.0],
                    "最高": [11.0],
                    "最低": [9.0],
                    "收盘": [10.0 if kwargs["adjust"] == "" else 20.0],
                    "成交量": [1000],
                    "成交额": [1e6],
                    "涨跌幅": [0.0],
                }
            )

    source = object.__new__(AkShareSource)
    source.api = API()
    monkeypatch.setattr("infrastructure.sources.time.sleep", lambda _: None)
    f = source.get_daily("2023-01-03", "2023-01-03", ["600000"])
    assert f.volume.iloc[0] == 100000
    assert f.adj_factor.iloc[0] == 2
    assert board_of("688001") == "star"
    assert board_of("920001") == "bse"


def test_tushare_raw_financial_pit_and_units(monkeypatch):

    source = object.__new__(TushareSource)

    def call(method, **params):
        if method == "income":
            return pd.DataFrame(
                [
                    dict(
                        end_date="20221231",
                        ann_date="20230301",
                        f_ann_date="20230301",
                        revenue=100,
                        n_income=10,
                        oper_cost=60,
                    )
                ]
            )
        if method == "balancesheet":
            return pd.DataFrame(
                [
                    dict(
                        end_date="20221231",
                        ann_date="20230302",
                        f_ann_date="20230302",
                        total_assets=200,
                        total_liab=100,
                        total_hldr_eqy_inc_min_int=100,
                    )
                ]
            )
        if method == "cashflow":
            return pd.DataFrame(
                [
                    dict(
                        end_date="20221231",
                        ann_date="20230303",
                        f_ann_date="20230303",
                        n_cashflow_act=9,
                    )
                ]
            )
        if method == "dividend":
            return pd.DataFrame([dict(ex_date="20230601", cash_div_tax=0.5, stk_div=0.2)])
        if method == "daily":
            return pd.DataFrame(
                [
                    dict(
                        trade_date="20230103",
                        open=10,
                        high=11,
                        low=9,
                        close=10,
                        pre_close=10,
                        vol=100,
                        amount=1000,
                    )
                ]
            )
        if method == "adj_factor":
            return pd.DataFrame([dict(trade_date="20230103", adj_factor=2)])
        if method == "daily_basic":
            return pd.DataFrame([dict(trade_date="20230103", total_mv=1000, float_share=100)])
        return pd.DataFrame()

    monkeypatch.setattr(source, "call", call)
    f = source.get_financials("2023-01-01", "2023-12-31", ["600000"])
    assert f.announce_date.tolist() == ["2023-03-01", "2023-03-02", "2023-03-03"]
    assert pd.isna(f.iloc[0].assets)
    assert f.iloc[-1].operating_cf == 9
    actions = source.get("actions", "2023-01-01", "2023-12-31", ["600000"])
    assert actions.cash_per_share.iloc[0] == 0.5
    daily = source.get_daily("2023-01-01", "2023-01-10", ["600000"])
    assert daily.volume.iloc[0] == 10000 and daily.amount.iloc[0] == 1e6
    assert daily.market_cap.iloc[0] == 1e7


def test_eastmoney_lightweight_no_akshare_dependency(monkeypatch):
    import httpx
    from infrastructure.sources import EastmoneySource

    def get(self, url, params):
        close = "10" if params["fqt"] == 0 else "20"
        payload = {"data": {"klines": [f"2023-01-03,10,{close},11,9,100,100000,20,0,0,1"]}}
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", get)
    monkeypatch.setattr("infrastructure.sources.time.sleep", lambda x: None)
    f = EastmoneySource().get_daily("2023-01-01", "2023-01-05", ["600000"])
    assert f.volume.iloc[0] == 10000 and f.adj_factor.iloc[0] == 2
    with pytest.raises(ValueError):
        EastmoneySource().get_calendar("2023-01-01", "2023-01-10")
