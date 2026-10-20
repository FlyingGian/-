"""适配器不隐造缺失字段。公网免费源不保证历史完整性。"""

from abc import ABC, abstractmethod
from pathlib import Path
import time
import pandas as pd
import numpy as np
from infrastructure.storage import normalize


def retry(call, attempts: int = 3):
    for n in range(attempts):
        try:
            return call()
        except Exception:
            if n == attempts - 1:
                raise
            time.sleep(min(2**n, 8))


class BaseDataSource(ABC):
    @abstractmethod
    def get(
        self, kind: str, start: str, end: str, codes: list[str] | None = None
    ) -> pd.DataFrame: ...
    def list_stocks(self):
        return self.get("stocks", "", "")

    def get_daily(self, start, end, codes):
        return self.get("daily", start, end, codes)

    def get_calendar(self, start, end):
        return self.get("calendar", start, end)

    def get_financials(self, start, end, codes):
        return self.get("financials", start, end, codes)

    def get_index_members(self, start, end, codes):
        return self.get("members", start, end, codes)

    def get_st_flags(self, start, end, codes):
        return self.get("states", start, end, codes)


def board_of(code: str) -> str:
    return (
        "star"
        if code.startswith("688")
        else (
            "chinext"
            if code.startswith(("300", "301"))
            else "bse" if code.startswith(("4", "8", "92")) else "main"
        )
    )


class LocalCSVSource(BaseDataSource):
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def get(self, kind, start, end, codes=None):
        f = normalize(
            pd.read_csv(
                self.root / f"{kind}.csv",
                dtype={"code": str, "index_code": str},
                encoding="utf-8-sig",
            )
        )
        if "date" in f and start:
            f = f.loc[(f.date >= start) & (f.date <= end)]
        if codes and "code" in f:
            f = f.loc[f.code.isin(codes)]
        return f


class AkShareSource(BaseDataSource):
    def __init__(self):
        import akshare

        self.api = akshare

    def get(self, kind, start, end, codes=None):
        ak = self.api
        if kind == "calendar":
            f = retry(ak.tool_trade_date_hist_sina)
            f = f.rename(columns={"trade_date": "date"})
            f["is_open"] = True
            f = normalize(f)
            return f.loc[(f.date >= start) & (f.date <= end)]
        if kind == "stocks":
            # 该接口只有当前代码名称，不能构造上市日和退市日冒充完整基础信息。
            raise ValueError(
                "AkShare当前名单缺少完整上市/退市历史，请先导入stocks.csv或使用Tushare"
            )
        if kind != "daily":
            raise ValueError(
                f"AkShare未提供可靠的{kind}历史适配，请使用带公告日/历史时点的CSV或Tushare"
            )
        frames = []
        for code in codes or []:
            raw = retry(
                lambda: ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start.replace("-", ""),
                    end_date=end.replace("-", ""),
                    adjust="",
                )
            )
            hfq = retry(
                lambda: ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start.replace("-", ""),
                    end_date=end.replace("-", ""),
                    adjust="hfq",
                )
            )
            raw = raw.rename(
                columns={
                    "日期": "date",
                    "开盘": "open",
                    "收盘": "close",
                    "最高": "high",
                    "最低": "low",
                    "成交量": "volume",
                    "成交额": "amount",
                    "涨跌幅": "pct",
                }
            )
            if raw.empty:
                continue
            # 东财历史接口的成交量为手，转换为股；不得套用到新浪股单位。
            raw["volume"] *= 100
            mapping = hfq.set_index(hfq["日期"].astype(str))["收盘"]
            raw["adj_factor"] = raw.date.astype(str).map(mapping) / raw.close
            raw["prev_close"] = raw.close / (1 + raw.pct / 100)
            raw["code"] = code
            raw["suspended"] = False
            raw["market_cap"] = np.nan
            raw["float_shares"] = np.nan
            frames.append(
                raw[
                    [
                        "code",
                        "date",
                        "open",
                        "high",
                        "low",
                        "close",
                        "prev_close",
                        "volume",
                        "amount",
                        "adj_factor",
                        "suspended",
                        "market_cap",
                        "float_shares",
                    ]
                ]
            )
            time.sleep(0.35)
        return normalize(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame()


class TushareSource(BaseDataSource):
    def __init__(self, token: str):
        if not token:
            raise ValueError("请先在设置中提供Tushare Token")
        import tushare

        self.api = tushare.pro_api(token)
        self.last = 0.0

    def call(self, method: str, **params):
        delay = 0.6 - (time.monotonic() - self.last)
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()
        return retry(lambda: getattr(self.api, method)(**params))

    def get(self, kind, start, end, codes=None):
        begin = start.replace("-", "")
        finish = end.replace("-", "")
        if kind == "stocks":
            frames = [
                self.call(
                    "stock_basic",
                    exchange="",
                    list_status=s,
                    fields="ts_code,symbol,name,list_date,delist_date",
                )
                for s in ("L", "D", "P")
            ]
            f = pd.concat(frames).drop_duplicates("symbol").rename(columns={"symbol": "code"})
            f["board"] = f.code.map(board_of)
            return normalize(f[["code", "name", "list_date", "delist_date", "board"]])
        if kind == "calendar":
            f = self.call("trade_cal", exchange="SSE", start_date=begin, end_date=finish).rename(
                columns={"cal_date": "date"}
            )
            return normalize(f[["date", "is_open"]])
        if kind == "financials":
            return self._financials(begin, finish, codes or [])
        if kind == "actions":
            return self._actions(begin, finish, codes or [])
        if kind not in ("daily", "valuations", "states"):
            raise ValueError(f"{kind}需导入经过核验的历史CSV；不自动把当前成分转历史区间")
        frames = []
        for code in codes or []:
            suffix = "SH" if code.startswith("6") else "BJ" if board_of(code) == "bse" else "SZ"
            ts = f"{code}.{suffix}"
            if kind == "states":
                raise ValueError("历史ST、行业与退市整理标记需完整按日快照，请导入states.csv")
            f = self.call(
                "daily" if kind == "daily" else "daily_basic",
                ts_code=ts,
                start_date=begin,
                end_date=finish,
            )
            if f.empty:
                continue
            f = f.rename(
                columns={
                    "trade_date": "date",
                    "pre_close": "prev_close",
                    "vol": "volume",
                    "pe_ttm": "pe",
                    "ps_ttm": "ps",
                    "dv_ttm": "dividend_yield",
                }
            )
            f["code"] = code
            if kind == "daily":
                adj = self.call("adj_factor", ts_code=ts, start_date=begin, end_date=finish).rename(
                    columns={"trade_date": "date"}
                )
                f = f.merge(adj[["date", "adj_factor"]], on="date", how="left")
                security_limits = self.call(
                    "stk_limit", ts_code=ts, start_date=begin, end_date=finish
                )
                if not security_limits.empty:
                    security_limits = security_limits.rename(
                        columns={
                            "trade_date": "date",
                            "up_limit": "limit_up",
                            "down_limit": "limit_down",
                        }
                    )
                    f = f.merge(
                        security_limits[["date", "limit_up", "limit_down"]], on="date", how="left"
                    )
                f["volume"] *= 100
                f["amount"] *= 1000
                f["suspended"] = False
                basic = self.call(
                    "daily_basic",
                    ts_code=ts,
                    start_date=begin,
                    end_date=finish,
                    fields="trade_date,total_mv,float_share",
                )
                if not basic.empty:
                    basic = basic.rename(
                        columns={
                            "trade_date": "date",
                            "total_mv": "market_cap",
                            "float_share": "float_shares",
                        }
                    )
                    basic["market_cap"] *= 10000
                    basic["float_shares"] *= 10000
                    f = f.merge(
                        basic[["date", "market_cap", "float_shares"]], on="date", how="left"
                    )
                f = f.drop(
                    columns=[
                        c
                        for c in f
                        if c
                        not in (
                            "code",
                            "date",
                            "open",
                            "high",
                            "low",
                            "close",
                            "prev_close",
                            "volume",
                            "amount",
                            "adj_factor",
                            "suspended",
                            "market_cap",
                            "float_shares",
                            "limit_up",
                            "limit_down",
                        )
                    ]
                )
            else:
                if "dividend_yield" in f:
                    f["dividend_yield"] /= 100
                f = f[[c for c in ("code", "date", "pe", "pb", "ps", "dividend_yield") if c in f]]
            frames.append(f)
        return normalize(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame()

    def _financials(self, start, end, codes):
        output = []
        mapping = {
            "income": {
                "revenue": "revenue",
                "n_income": "net_profit",
                "oper_cost": "cost",
                "ebitda": "ebitda",
                "rd_exp": "rd",
            },
            "balancesheet": {
                "total_hldr_eqy_inc_min_int": "equity",
                "total_assets": "assets",
                "total_liab": "debt",
                "money_cap": "cash",
                "goodwill": "goodwill",
            },
            "cashflow": {"n_cashflow_act": "operating_cf"},
        }
        for code in codes:
            suffix = "SH" if code.startswith("6") else "BJ" if board_of(code) == "bse" else "SZ"
            reports = []
            for method, fields in mapping.items():
                f = self.call(method, ts_code=f"{code}.{suffix}", start_date=start, end_date=end)
                if f.empty:
                    continue
                for _, row in f.iterrows():
                    ann = str(row.get("f_ann_date") or row.get("ann_date") or "")
                    if ann in ("nan", "None", ""):
                        continue
                    report = {
                        "_kind": method,
                        "announce_date": ann,
                        "report_period": str(row.end_date),
                    }
                    report.update(
                        {target: row.get(source, np.nan) for source, target in fields.items()}
                    )
                    reports.append(report)
            if not reports:
                continue
            f = pd.DataFrame(reports)
            for period, g in f.groupby("report_period"):
                for version, event in enumerate(sorted(g.announce_date.unique()), 1):
                    # 合并截至该公告事件已知的三张报表，不用以后报表回填。
                    known = (
                        g.loc[g.announce_date <= event]
                        .sort_values("announce_date")
                        .drop_duplicates("_kind", keep="last")
                    )
                    merged = {
                        "code": code,
                        "report_period": period,
                        "announce_date": event,
                        "version": version,
                    }
                    for _, row in known.iterrows():
                        for c in row.index:
                            if c not in ("_kind", "announce_date", "report_period") and pd.notna(
                                row[c]
                            ):
                                merged[c] = row[c]
                    merged["gross_profit"] = merged.get("revenue", np.nan) - merged.pop(
                        "cost", np.nan
                    )
                    output.append(merged)
        return normalize(pd.DataFrame(output)) if output else pd.DataFrame()

    def _actions(self, start, end, codes):
        output = []
        for code in codes:
            suffix = "SH" if code.startswith("6") else "BJ" if board_of(code) == "bse" else "SZ"
            frame = self.call("dividend", ts_code=f"{code}.{suffix}")
            if frame.empty:
                continue
            for _, row in frame.iterrows():
                ex = row.get("ex_date")
                if pd.isna(ex) or not start <= str(ex) <= end:
                    continue
                cash = row.get("cash_div_tax", 0)
                shares = row.get("stk_div", 0)
                output.append(
                    dict(
                        code=code,
                        ex_date=str(ex),
                        cash_per_share=float(cash) if pd.notna(cash) else 0,
                        share_ratio=float(shares) if pd.notna(shares) else 0,
                        rights_ratio=0.0,
                        rights_price=0.0,
                    )
                )
        return normalize(pd.DataFrame(output)) if output else pd.DataFrame()


class EastmoneySource(BaseDataSource):
    """轻量日线适配，不依赖完整AkShare及其无wheel间接包。"""

    def get(self, kind, start, end, codes=None):
        if kind != "daily":
            raise ValueError(f"东方财富轻量接口不提供完整{kind}历史，请先导入对应CSV或选择Tushare")
        import httpx

        frames = []
        with httpx.Client(
            timeout=20, headers={"User-Agent": "AshareQuant/1.0 personal-research"}
        ) as client:
            for code in codes or []:
                if board_of(code) == "bse":
                    raise ValueError("北交所日线请使用Tushare或CSV，避免错误的市场编号映射")
                params = {
                    "secid": ("1." if code.startswith("6") else "0.") + code,
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "klt": 101,
                    "beg": start.replace("-", ""),
                    "end": end.replace("-", ""),
                }

                def fetch(adjust):
                    response = client.get(
                        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                        params=params | {"fqt": adjust},
                    )
                    response.raise_for_status()
                    payload = response.json().get("data")
                    if not payload:
                        return pd.DataFrame()
                    values = [x.split(",") for x in payload.get("klines", [])]
                    columns = [
                        "date",
                        "open",
                        "close",
                        "high",
                        "low",
                        "volume",
                        "amount",
                        "amplitude",
                        "pct",
                        "change",
                        "turnover_pct",
                    ]
                    return pd.DataFrame(values, columns=columns)

                raw = retry(lambda: fetch(0))
                hfq = retry(lambda: fetch(2))
                if raw.empty:
                    continue
                for c in raw.columns[1:]:
                    raw[c] = pd.to_numeric(raw[c])
                mapping = (
                    hfq.set_index("date")["close"].astype(float)
                    if not hfq.empty
                    else pd.Series(dtype=float)
                )
                raw["adj_factor"] = raw.date.map(mapping) / raw.close
                raw["prev_close"] = raw.close / (1 + raw.pct / 100)
                raw["volume"] *= 100
                raw["code"] = code
                raw["suspended"] = False
                raw["float_shares"] = raw.volume / (raw.turnover_pct / 100).replace(0, np.nan)
                raw["market_cap"] = np.nan
                frames.append(
                    raw[
                        [
                            "code",
                            "date",
                            "open",
                            "high",
                            "low",
                            "close",
                            "prev_close",
                            "volume",
                            "amount",
                            "adj_factor",
                            "float_shares",
                            "market_cap",
                            "suspended",
                        ]
                    ]
                )
                time.sleep(0.35)
        return normalize(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame()
