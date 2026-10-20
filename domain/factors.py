"""因子注册、严格时点取数、截面处理与安全表达式。"""

from dataclasses import dataclass
import ast
import operator
import numpy as np
import pandas as pd
from domain.data import ResearchData


@dataclass(frozen=True)
class Factor:
    name: str
    label: str
    category: str
    direction: int
    lookback: int
    formula: str
    dependencies: str
    failure: str = "风格切换、拥挤与交易成本可能使因子失效；必须进行分区间样本外验证"
    environment: str = "A股日频截面研究；不预设持续有效"
    expected_turnover: str = "依股票池与调仓频率实测；无固定承诺"


SPECS = [
    ("ep", "盈利收益率", "价值", 1, 1, "1/pe", "估值"),
    ("bp", "账面市值比", "价值", 1, 1, "1/pb", "估值"),
    ("sp", "销售收益率", "价值", 1, 1, "1/ps", "估值"),
    ("cfp", "现金流收益率", "价值", 1, 1, "1/pcf", "估值"),
    ("dividend_yield", "股息率", "价值", 1, 1, "dividend_yield", "估值"),
    ("ev_ebitda", "企业价值倍数", "价值", -1, 1, "ev_ebitda", "估值"),
    ("roe", "净资产收益率", "质量", 1, 1, "net_profit/equity", "公告财务"),
    ("roa", "资产收益率", "质量", 1, 1, "net_profit/assets", "公告财务"),
    ("gross_margin", "毛利率", "质量", 1, 1, "gross_profit/revenue", "公告财务"),
    ("net_margin", "净利率", "质量", 1, 1, "net_profit/revenue", "公告财务"),
    ("asset_turnover", "资产周转率", "质量", 1, 1, "revenue/assets", "公告财务"),
    ("accruals", "应计利润", "质量", -1, 1, "(net_profit-operating_cf)/assets", "公告财务"),
    ("cash_quality", "现金利润比", "质量", 1, 1, "operating_cf/net_profit", "公告财务"),
    ("leverage", "财务杠杆", "质量", -1, 1, "assets/equity", "公告财务"),
    ("debt_ratio", "负债率", "质量", -1, 1, "debt/assets", "公告财务"),
    ("revenue_growth", "营收同比", "成长", 1, 1, "revenue_yoy", "公告财务"),
    ("profit_growth", "利润同比", "成长", 1, 1, "profit_yoy", "公告财务"),
    ("growth_acceleration", "营收增速改善", "成长", 1, 1, "revenue_acceleration", "公告财务"),
    ("rd_ratio", "研发费用率", "成长", 1, 1, "rd/revenue", "公告财务"),
    ("momentum_20", "20日动量", "动量反转", 1, 21, "hfq[-1]/hfq[-21]-1", "日线复权"),
    ("momentum_60", "60日动量", "动量反转", 1, 61, "hfq[-1]/hfq[-61]-1", "日线复权"),
    ("momentum_120", "120日动量", "动量反转", 1, 121, "hfq[-1]/hfq[-121]-1", "日线复权"),
    ("reversal_5", "5日反转", "动量反转", -1, 6, "hfq[-1]/hfq[-6]-1", "日线复权"),
    ("reversal_1", "1日反转", "动量反转", -1, 2, "hfq[-1]/hfq[-2]-1", "日线复权"),
    ("momentum_skip5", "剔除近5日动量", "动量反转", 1, 61, "hfq[-6]/hfq[-61]-1", "日线复权"),
    ("volatility_20", "20日波动", "波动风险", -1, 21, "std(ret[-20:])*sqrt(252)", "日线复权"),
    ("volatility_60", "60日波动", "波动风险", -1, 61, "std(ret[-60:])*sqrt(252)", "日线复权"),
    (
        "downside_vol",
        "下行波动",
        "波动风险",
        -1,
        21,
        "sqrt(mean(min(ret,0)^2))*sqrt(252)",
        "日线复权",
    ),
    ("max_drawdown_60", "60日回撤", "波动风险", -1, 60, "max(1-hfq/cummax(hfq))", "日线复权"),
    ("range_20", "20日振幅", "波动风险", -1, 20, "mean((high-low)/prev_close)", "日线"),
    (
        "beta_60",
        "60日市场Beta",
        "波动风险",
        -1,
        61,
        "cov(ret,market_ret)/var(market_ret)",
        "同期截面市场代理",
    ),
    (
        "ivol_60",
        "60日特质波动",
        "波动风险",
        -1,
        61,
        "std(ret-beta*market_ret)*sqrt(252)",
        "同期截面市场代理",
    ),
    ("turnover_20", "20日换手率", "流动情绪", -1, 20, "mean(volume/float_shares)", "日线流通股本"),
    (
        "abnormal_turnover",
        "异常换手",
        "流动情绪",
        -1,
        21,
        "turnover[-1]/mean(turnover[-21:-1])",
        "日线流通股本",
    ),
    ("amount_20", "20日成交额", "流动情绪", 1, 20, "mean(amount[-20:])", "日线"),
    (
        "volume_ratio_5",
        "日频5日量比",
        "流动情绪",
        -1,
        6,
        "volume[-1]/mean(volume[-6:-1])",
        "日线；不冒充盘中同时点量比",
    ),
    ("amihud_20", "Amihud非流动性", "流动情绪", -1, 21, "mean(abs(ret)/amount)", "日线"),
    (
        "turnover_change",
        "换手率变化",
        "流动情绪",
        -1,
        40,
        "mean(turn[-20:])/mean(turn[-40:-20])-1",
        "日线流通股本",
    ),
    (
        "amount_growth",
        "成交额变化",
        "流动情绪",
        1,
        40,
        "mean(amount[-20:])/mean(amount[-40:-20])-1",
        "日线",
    ),
    ("log_size", "对数市值", "规模其他", -1, 1, "log(market_cap)", "日线市值"),
    ("holders_change", "股东户数变化", "规模其他", -1, 1, "holders_change", "公告财务"),
    ("institutional_ratio", "机构持股比例", "规模其他", 1, 1, "institutional_ratio", "公告财务"),
    ("pledge_ratio", "股权质押比例", "规模其他", -1, 1, "pledge_ratio", "公告财务"),
    ("goodwill_ratio", "商誉净资产比", "规模其他", -1, 1, "goodwill/equity", "公告财务"),
    ("cash_ratio", "现金资产比", "质量", 1, 1, "cash/assets", "公告财务"),
    ("price_ma20", "价格均线偏离", "动量反转", -1, 20, "hfq[-1]/mean(hfq[-20:])-1", "日线复权"),
]
REGISTRY = {s[0]: Factor(*s) for s in SPECS}


def safe_ratio(a: float, b: float) -> float:
    return a / b if pd.notna(b) and b != 0 else np.nan


def compute(data: ResearchData, as_of_date: str, names: list[str] | None = None) -> pd.DataFrame:
    names = names or list(REGISTRY)
    unknown = set(names) - set(REGISTRY)
    if unknown:
        raise ValueError(f"未知因子: {unknown}")
    bars = data.bars(as_of_date)
    if bars.empty:
        return pd.DataFrame(columns=names)
    financial = data.financial_at(as_of_date).set_index("code")
    vals = data.valuations
    vals = (
        vals.loc[vals.date <= as_of_date]
        .sort_values("date")
        .drop_duplicates("code", keep="last")
        .set_index("code")
        if not vals.empty
        else pd.DataFrame()
    )
    # 截面等权市场代理，不冒充可投资基准指数。
    hist = bars.assign(hfq=bars.close * bars.adj_factor)
    hist["ret"] = hist.groupby("code").hfq.pct_change(fill_method=None)
    market = hist.groupby("date").ret.mean()
    results = {}
    ratios = {
        "roe": ("net_profit", "equity"),
        "roa": ("net_profit", "assets"),
        "gross_margin": ("gross_profit", "revenue"),
        "net_margin": ("net_profit", "revenue"),
        "asset_turnover": ("revenue", "assets"),
        "cash_quality": ("operating_cf", "net_profit"),
        "leverage": ("assets", "equity"),
        "debt_ratio": ("debt", "assets"),
        "rd_ratio": ("rd", "revenue"),
        "goodwill_ratio": ("goodwill", "equity"),
        "cash_ratio": ("cash", "assets"),
    }
    direct = {
        "revenue_growth": "revenue_yoy",
        "profit_growth": "profit_yoy",
        "growth_acceleration": "revenue_acceleration",
    }
    for code, g in hist.groupby("code", sort=True):
        g = g.sort_values("date").reset_index(drop=True)
        h = g.hfq
        ret = g.ret
        f = financial.loc[code] if code in financial.index else pd.Series(dtype=float)
        v = vals.loc[code] if code in vals.index else pd.Series(dtype=float)
        t = g.volume / g.get("float_shares", pd.Series(np.nan, index=g.index)).replace(0, np.nan)
        row = {}
        for name in names:
            meta = REGISTRY[name]
            value = np.nan
            if len(g) < meta.lookback or g.iloc[-1].date != as_of_date:
                row[name] = value
                continue
            if name in ("ep", "bp", "sp", "cfp"):
                field = {"ep": "pe", "bp": "pb", "sp": "ps", "cfp": "pcf"}[name]
                value = safe_ratio(1, v.get(field, np.nan))
            elif name in ("dividend_yield", "ev_ebitda"):
                value = v.get(name, np.nan)
            elif name in ratios:
                a, b = ratios[name]
                value = safe_ratio(f.get(a, np.nan), f.get(b, np.nan))
            elif name == "accruals":
                value = safe_ratio(
                    f.get("net_profit", np.nan) - f.get("operating_cf", np.nan),
                    f.get("assets", np.nan),
                )
            elif name in direct or name in (
                "holders_change",
                "institutional_ratio",
                "pledge_ratio",
            ):
                value = f.get(direct.get(name, name), np.nan)
            elif name.startswith("momentum_") and name != "momentum_skip5":
                n = int(name.split("_")[-1])
                value = safe_ratio(h.iloc[-1], h.iloc[-n - 1]) - 1
            elif name.startswith("reversal_"):
                n = int(name.split("_")[-1])
                value = safe_ratio(h.iloc[-1], h.iloc[-n - 1]) - 1
            elif name == "momentum_skip5":
                value = safe_ratio(h.iloc[-6], h.iloc[-61]) - 1
            elif name.startswith("volatility_"):
                n = int(name.split("_")[-1])
                value = ret.tail(n).std(ddof=0) * np.sqrt(252)
            elif name == "downside_vol":
                value = np.sqrt(np.square(ret.tail(20).clip(upper=0)).mean() * 252)
            elif name == "max_drawdown_60":
                x = h.tail(60)
                value = (1 - x / x.cummax()).max()
            elif name == "range_20":
                value = ((g.high - g.low) / g.prev_close.replace(0, np.nan)).tail(20).mean()
            elif name in ("beta_60", "ivol_60"):
                pair = (
                    pd.DataFrame({"r": ret.to_numpy(), "m": market.reindex(g.date).to_numpy()})
                    .tail(60)
                    .dropna()
                )
                beta = safe_ratio(pair.r.cov(pair.m), pair.m.var())
                value = (
                    beta
                    if name == "beta_60"
                    else (pair.r - beta * pair.m).std(ddof=0) * np.sqrt(252)
                )
            elif name == "turnover_20":
                value = t.tail(20).mean()
            elif name == "abnormal_turnover":
                value = safe_ratio(t.iloc[-1], t.iloc[-21:-1].mean())
            elif name == "amount_20":
                value = g.amount.tail(20).mean()
            elif name == "volume_ratio_5":
                value = safe_ratio(g.volume.iloc[-1], g.volume.iloc[-6:-1].mean())
            elif name == "amihud_20":
                value = (ret.abs() / g.amount.replace(0, np.nan)).tail(20).mean()
            elif name == "turnover_change":
                value = safe_ratio(t.tail(20).mean(), t.iloc[-40:-20].mean()) - 1
            elif name == "amount_growth":
                value = safe_ratio(g.amount.tail(20).mean(), g.amount.iloc[-40:-20].mean()) - 1
            elif name == "log_size":
                cap = g.iloc[-1].get("market_cap", np.nan)
                value = np.log(cap) if cap > 0 else np.nan
            elif name == "price_ma20":
                value = safe_ratio(h.iloc[-1], h.tail(20).mean()) - 1
            row[name] = value if np.isfinite(value) else np.nan
        results[code] = row
    return pd.DataFrame.from_dict(results, orient="index").rename_axis("code")


def process(
    raw: pd.DataFrame, industry: pd.Series, size: pd.Series, neutralize: bool = True
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    missing = raw.isna()
    x = raw.replace([np.inf, -np.inf], np.nan).copy()
    for c in x:
        x[c] = x[c].fillna(x[c].groupby(industry).transform("median"))
        x[c] = x[c].fillna(x[c].median())  # 全缺失保留 NaN，不能用0伪造信息。
    stages = {"raw": raw.copy(), "filled": x.copy()}
    med = x.median()
    mad = (x - med).abs().median()
    for c in x:
        if pd.notna(mad[c]) and mad[c] > 0:
            x[c] = x[c].clip(med[c] - 3 * mad[c], med[c] + 3 * mad[c])
    stages["winsorized"] = x.copy()
    std = x.std(ddof=0)
    x = (x - x.mean()) / std.replace(0, np.nan)
    stages["standardized"] = x.copy()
    if neutralize:
        x = x - x.groupby(industry).transform("mean")
        logsize = np.log(size.where(size > 0)).reindex(x.index)
        for c in x:
            valid = x[c].notna() & logsize.notna()
            if valid.sum() >= 3:
                design = np.column_stack([np.ones(valid.sum()), logsize[valid]])
                x.loc[valid, c] = (
                    x.loc[valid, c]
                    - design @ np.linalg.lstsq(design, x.loc[valid, c], rcond=None)[0]
                )
    stages["neutralized"] = x.copy()
    for c in x:
        x[c] *= REGISTRY[c].direction if c in REGISTRY else 1
    return x, missing, stages


OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def expression(text: str, fields: pd.DataFrame) -> pd.Series:
    """只接受列名、数字与四则运算；不执行 eval、函数或属性访问。"""
    tree = ast.parse(text, mode="eval")
    if len(list(ast.walk(tree))) > 100:
        raise ValueError("表达式过长")

    def visit(n):
        if isinstance(n, ast.Name) and n.id in fields:
            return fields[n.id]
        if isinstance(n, ast.Constant) and type(n.value) in (int, float):
            return n.value
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -visit(n.operand)
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            return OPS[type(n.op)](visit(n.left), visit(n.right))
        raise ValueError("仅支持字段、数字和四则运算")

    result = visit(tree.body)
    return pd.Series(result, index=fields.index).replace([np.inf, -np.inf], np.nan)
