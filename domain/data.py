"""无 I/O 的研究数据契约与严格时点切片。"""

from dataclasses import dataclass, field
import pandas as pd


@dataclass
class ResearchData:
    daily: pd.DataFrame
    stocks: pd.DataFrame
    calendar: pd.DataFrame
    states: pd.DataFrame = field(default_factory=pd.DataFrame)
    financials: pd.DataFrame = field(default_factory=pd.DataFrame)
    valuations: pd.DataFrame = field(default_factory=pd.DataFrame)
    members: pd.DataFrame = field(default_factory=pd.DataFrame)
    actions: pd.DataFrame = field(default_factory=pd.DataFrame)
    benchmarks: pd.DataFrame = field(default_factory=pd.DataFrame)
    version: str = "unversioned"
    provenance: str = "user-import"

    def dates(self) -> list[str]:
        return sorted(self.calendar.loc[self.calendar.is_open.astype(bool), "date"].astype(str))

    def bars(self, as_of_date: str) -> pd.DataFrame:
        return self.daily.loc[self.daily.date <= as_of_date].sort_values(["code", "date"]).copy()

    def state_at(self, as_of_date: str) -> pd.DataFrame:
        if self.states.empty:
            return pd.DataFrame(columns=["code", "date", "is_st", "industry", "delisting"])
        # 必须是当天状态，不以最近一次状态冒充完整每日快照。
        return self.states.loc[self.states.date == as_of_date].copy()

    def financial_at(self, as_of_date: str) -> pd.DataFrame:
        if self.financials.empty:
            return pd.DataFrame(columns=["code"])
        f = self.financials.copy()
        sessions = self.dates()

        def available(row: pd.Series) -> str:
            ann = row.get("announce_date")
            if pd.isna(ann) or not str(ann).strip():
                period = pd.Timestamp(row.report_period)
                y, m = period.year, period.month
                ann = (
                    f"{y + (m == 12)}-04-30"
                    if m in (3, 12)
                    else f"{y}-{'08-31' if m == 6 else '10-31'}"
                )
            # 公告日即使是交易日也只能次一交易日使用。
            candidates = [d for d in sessions if d > str(ann)[:10]]
            return candidates[0] if candidates else "9999-12-31"

        f["_available"] = f.apply(available, axis=1)
        f = f.loc[f._available <= as_of_date]
        if f.empty:
            return pd.DataFrame(columns=["code"])
        return (
            f.sort_values(["code", "report_period", "_available", "version"])
            .drop_duplicates("code", keep="last")
            .drop(columns="_available")
        )

    def universe(self, as_of_date: str, index_code: str | None = None) -> set[str]:
        s = self.stocks
        mask = s.list_date <= as_of_date
        if "delist_date" in s:
            mask &= s.delist_date.isna() | (s.delist_date == "") | (s.delist_date >= as_of_date)
        result = set(s.loc[mask, "code"])
        if index_code:
            if self.members.empty:
                raise ValueError("缺少历史指数成分；不能用当前成分回测")
            m = self.members
            end = m.end_date.isna() | (m.end_date == "") | (m.end_date >= as_of_date)
            result &= set(
                m.loc[(m.index_code == index_code) & (m.start_date <= as_of_date) & end, "code"]
            )
        return result

    def check_history(self, dates: list[str], codes: set[str] | None = None) -> None:
        if not dates:
            raise ValueError("没有真实交易日历或所选日期无行情")
        if self.provenance == "realtime":
            raise ValueError("回测禁止读取实时快照")
        bars = self.daily.loc[self.daily.date.isin(dates)]
        if codes is not None:
            bars = bars.loc[bars.code.isin(codes)]
        if bars.empty:
            raise ValueError("所选区间没有日线行情")
        missing_sessions = set(dates) - set(bars.date)
        if missing_sessions:
            raise ValueError(f"缺失交易日行情：{sorted(missing_sessions)[:5]}")
        required = set(zip(bars.code, bars.date))
        present = set(zip(self.states.get("code", []), self.states.get("date", [])))
        if required - present:
            raise ValueError(f"缺少历史状态快照：{len(required - present)} 个证券交易日")
        if bars.adj_factor.isna().any() or (bars.adj_factor <= 0).any():
            raise ValueError("缺少有效复权因子，禁止完整回测")
        # 复权变化必须有显式现金行动，防止分红遗漏或重复。
        ordered = bars.sort_values(["code", "date"])
        changed = ordered.groupby("code").adj_factor.pct_change(fill_method=None).abs() > 1e-8
        events = set(zip(self.actions.get("code", []), self.actions.get("ex_date", [])))
        unexplained = set(zip(ordered.loc[changed, "code"], ordered.loc[changed, "date"])) - events
        if unexplained:
            raise ValueError("复权因子发生变化但缺少现金行动记录")
