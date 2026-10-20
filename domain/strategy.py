"""选股、风险预算、因子贡献及人工名单。"""

from dataclasses import dataclass, field
import numpy as np
import pandas as pd
from domain.data import ResearchData
from domain.factors import compute, process


@dataclass
class StrategyConfig:
    weights: dict[str, float] = field(
        default_factory=lambda: {"momentum_20": 0.5, "reversal_5": 0.5}
    )
    holdings: int = 20
    index_code: str | None = None
    exclude_st: bool = True
    min_listing_days: int = 60
    min_amount: float = 20_000_000
    single: float = 0.10
    industry: float = 0.25
    top5: float = 0.40
    neutralize: bool = True
    blacklist: tuple[str, ...] = ()
    whitelist: tuple[str, ...] = ()
    rules: tuple[tuple[str, str, float], ...] = ()

    def __post_init__(self):
        if (
            not self.weights
            or any(not np.isfinite(v) or v < 0 for v in self.weights.values())
            or sum(self.weights.values()) <= 0
        ):
            raise ValueError("因子权重必须非负，且总和大于0")
        if self.holdings < 1 or not (
            0 < self.single <= 1 and 0 < self.industry <= 1 and 0 < self.top5 <= 1
        ):
            raise ValueError("持仓数或风险约束无效")


def allocate(candidates: pd.DataFrame, config: StrategyConfig) -> pd.Series:
    """保守等权：约束不足留现金，不为满仓而突破限制。"""
    result = pd.Series(0.0, index=candidates.index)
    industry_used = {}
    top_used = 0.0
    for i, (code, row) in enumerate(candidates.iterrows()):
        sector = str(row.get("industry", "未知"))
        w = min(
            1 / config.holdings,
            config.single,
            config.top5 / 5,
            config.industry - industry_used.get(sector, 0),
        )
        if i < 5:
            w = min(w, max(0, (config.top5 - top_used) / (5 - i)))
        w = max(0, w)
        result.loc[code] = w
        industry_used[sector] = industry_used.get(sector, 0) + w
        if i < 5:
            top_used += w
    return result


def screen(
    data: ResearchData, as_of_date: str, config: StrategyConfig, previous: set[str] | None = None
) -> pd.DataFrame:
    names = list(dict.fromkeys(list(config.weights) + [r[0] for r in config.rules]))
    raw = compute(data, as_of_date, names)
    if raw.empty:
        return raw
    bars = data.bars(as_of_date)
    last = bars.loc[bars.date == as_of_date].set_index("code")
    states = data.state_at(as_of_date).set_index("code")
    stocks = data.stocks.set_index("code")
    eligible = data.universe(as_of_date, config.index_code) - set(config.blacklist)
    # 白名单仅允许进入候选，不绕过 ST、停牌、流动性和风险约束。
    eligible |= set(config.whitelist) & data.universe(as_of_date)
    eligible &= set(states.index)
    eligible &= set(last.index)
    if config.exclude_st:
        eligible -= set(states.loc[states.is_st.astype(bool), :].index)
    if "delisting" in states:
        eligible -= set(states.loc[states.delisting.astype(bool)].index)
    dates = data.dates()
    eligible = {
        c
        for c in eligible
        if len([d for d in dates if stocks.loc[c, "list_date"] <= d <= as_of_date])
        >= config.min_listing_days
    }
    amounts = bars.groupby("code").amount.apply(
        lambda s: s.tail(20).mean() if len(s) >= 20 else np.nan
    )
    eligible = {
        c
        for c in eligible
        if amounts.get(c, 0) >= config.min_amount and not bool(last.loc[c].get("suspended", False))
    }
    raw = raw.loc[raw.index.intersection(sorted(eligible))]
    if raw.empty:
        return raw
    industries = states.industry.reindex(raw.index).fillna("未知")
    size = last.get("market_cap", pd.Series(np.nan, index=last.index)).reindex(raw.index)
    processed, missing, _ = process(raw, industries, size, config.neutralize)
    for factor_name, op, threshold in config.rules:
        ops = {
            ">": raw[factor_name] > threshold,
            "<": raw[factor_name] < threshold,
            ">=": raw[factor_name] >= threshold,
            "<=": raw[factor_name] <= threshold,
        }
        if op not in ops:
            raise ValueError("不支持的条件运算符")
        processed = processed.loc[ops[op]]
    required = list(config.weights)
    processed = processed.dropna(subset=required)
    if processed.empty:
        return processed
    weights = pd.Series(config.weights) / sum(config.weights.values())
    contributions = processed[required] * weights
    score = contributions.sum(axis=1)
    out = pd.DataFrame(
        {
            "score": score,
            "name": stocks.name.reindex(score.index),
            "industry": industries.reindex(score.index),
        }
    )
    out = (
        out.sort_index().sort_values("score", ascending=False, kind="stable").head(config.holdings)
    )
    out["rank"] = range(1, len(out) + 1)
    out["weight"] = allocate(out, config)
    out["status"] = ["保留" if previous and c in previous else "新进" for c in out.index]
    out["missing_fields"] = missing.reindex(out.index).sum(axis=1)
    for c in required:
        out[c] = raw[c].reindex(out.index)
        out[f"contribution:{c}"] = contributions[c].reindex(out.index)
    out.attrs["removed"] = sorted((previous or set()) - set(out.index))
    out.attrs["as_of_date"] = as_of_date
    return out.rename_axis("code").reset_index()


def risk_report(
    holdings: pd.DataFrame,
    equity: pd.DataFrame,
    config: StrategyConfig,
    volatility_limit: float = 0.30,
) -> dict:
    issues = []
    if holdings.empty:
        return {"issues": ["当前无持仓"], "industry": {}, "top5": 0, "hhi": 0}
    w = holdings.set_index("code").weight
    sectors = holdings.groupby("industry").weight.sum()
    if w.max() > config.single + 1e-9:
        issues.append("单票权重超限")
    if sectors.max() > config.industry + 1e-9:
        issues.append("行业权重超限")
    top = float(w.nlargest(5).sum())
    if top > config.top5 + 1e-9:
        issues.append("前五大持仓集中度超限")
    if not equity.empty:
        nav = equity.equity
        dd = float(1 - nav.iloc[-1] / max(nav.cummax().iloc[-1], nav.iloc[0]))
        vol = float(nav.pct_change(fill_method=None).std(ddof=0) * np.sqrt(252))
        if dd >= 0.15:
            issues.append("回撤超过15%警戒线")
        if vol > volatility_limit:
            issues.append("组合波动率超限")
    return {
        "issues": issues,
        "industry": sectors.to_dict(),
        "top5": top,
        "hhi": float((w * w).sum()),
    }
