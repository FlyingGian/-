"""因子样本外评估与带标签间隔的时序机器学习。"""

import numpy as np
import pandas as pd
from domain.data import ResearchData
from domain.factors import compute


def factor_analysis(
    data: ResearchData,
    name: str,
    start: str,
    end: str,
    horizons=(1, 5, 10, 20),
    progress=None,
    cancelled=None,
) -> dict:
    dates = [d for d in data.dates() if start <= d <= end]
    prices = (
        data.daily.assign(hfq=data.daily.close * data.daily.adj_factor)
        .pivot(index="date", columns="code", values="hfq")
        .reindex(data.dates())
    )
    rows = []
    layers = []
    previous = set()
    turn = []
    exposures = []
    for i, date in enumerate(dates):
        if cancelled and cancelled():
            raise InterruptedError("因子分析已取消")
        values = compute(data, date, [name])[name].dropna()
        if len(values) < 5:
            continue
        allowed = data.universe(date)
        values = values.loc[values.index.isin(allowed)]
        position = prices.index.get_indexer([date])[0]
        groups = pd.qcut(values.rank(method="first"), 5, labels=False)
        for h in horizons:
            if position + h >= len(prices) or prices.index[position + h] > end:
                continue
            forward = prices.iloc[position + h] / prices.iloc[position] - 1
            pair = pd.DataFrame({"factor": values, "forward": forward}).dropna()
            if len(pair) < 5:
                continue
            rankic = pair.factor.corr(pair.forward, method="spearman")
            ic = pair.factor.corr(pair.forward)
            rows.append(dict(date=date, horizon=h, ic=ic, rank_ic=rankic))
            if h == 1:
                for g in range(5):
                    selected = pair.index.intersection(groups.index[groups == g])
                    layers.append(
                        dict(date=date, layer=g + 1, return_=pair.loc[selected, "forward"].mean())
                    )
                top = set(groups.index[groups == 4])
                turn.append(1 - len(top & previous) / max(1, len(top)))
                previous = top
                bar = data.daily.loc[data.daily.date == date].set_index("code")
                if "market_cap" in bar:
                    sizes = bar.market_cap.reindex(pair.index).dropna()
                    if len(sizes) >= 10:
                        buckets = pd.qcut(sizes.rank(method="first"), 3, labels=False)
                        for bucket in range(3):
                            sub = pair.loc[
                                pair.index.intersection(buckets.index[buckets == bucket])
                            ]
                            exposures.append(
                                dict(
                                    date=date,
                                    size_group=bucket + 1,
                                    rank_ic=sub.factor.corr(sub.forward, method="spearman"),
                                )
                            )
        if progress:
            progress(int((i + 1) / max(1, len(dates)) * 100), f"因子分析 {date}")
    series = pd.DataFrame(rows)
    layer = pd.DataFrame(layers)
    summary = {}
    if not series.empty:
        for horizon, g in series.groupby("horizon"):
            std = g.rank_ic.std(ddof=0)
            summary[int(horizon)] = {
                "rank_ic_mean": float(g.rank_ic.mean()),
                "ic_mean": float(g.ic.mean()),
                "icir": float(g.rank_ic.mean() / std) if std else 0,
                "win_rate": float((g.rank_ic > 0).mean()),
            }
    if not layer.empty:
        pivot = layer.pivot(index="date", columns="layer", values="return_")
        layer_nav = (1 + pivot).cumprod()
        ls = (1 + pivot[5] - pivot[1]).cumprod()
        monotonicity = float(
            pivot.mean().corr(pd.Series(range(1, 6), index=range(1, 6)), method="spearman")
        )
    else:
        layer_nav = pd.DataFrame()
        ls = pd.Series(dtype=float)
        monotonicity = None
    return {
        "series": series,
        "summary": summary,
        "layers": layer_nav,
        "long_short": ls,
        "turnover": float(np.mean(turn)) if turn else None,
        "monotonicity": monotonicity,
        "size_groups": pd.DataFrame(exposures),
        "warning": "分层与多空为因子统计诊断，未计交易费/限价约束，不能当可交易收益；标签仅用于事后评价。",
    }


def purged_splits(dates: pd.Series, n_splits: int = 3, horizon: int = 5, embargo: int = 2):
    unique = np.array(sorted(dates.unique()))
    if len(unique) < (horizon + embargo + 2) * (n_splits + 1):
        raise ValueError("时序样本太少，无法进行purge/embargo验证")
    blocks = np.array_split(unique, n_splits + 1)
    for i in range(1, len(blocks)):
        test_dates = blocks[i]
        first = np.searchsorted(unique, test_dates[0])
        train_dates = unique[: max(0, first - horizon - embargo)]
        train = np.flatnonzero(dates.isin(train_dates))
        test = np.flatnonzero(dates.isin(test_dates))
        if len(train) and len(test):
            yield train, test


def walk_forward_ml(
    features: pd.DataFrame, labels: pd.Series, dates: pd.Series, horizon: int = 5, embargo: int = 2
) -> dict:
    from sklearn.pipeline import make_pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.ensemble import HistGradientBoostingRegressor

    if not features.index.equals(labels.index) or not features.index.equals(dates.index):
        raise ValueError("特征标签索引不一致")
    valid = labels.notna()
    features = features.loc[valid]
    labels = labels.loc[valid]
    dates = dates.loc[valid]
    oos = pd.Series(np.nan, index=features.index)
    models = []
    for train, test in purged_splits(dates, horizon=horizon, embargo=embargo):
        pipe = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            HistGradientBoostingRegressor(max_iter=100, max_leaf_nodes=15, random_state=42),
        )
        pipe.fit(features.iloc[train], labels.iloc[train])
        oos.iloc[test] = pipe.predict(features.iloc[test])
        models.append(pipe)
    return {
        "predictions": oos,
        "models": models,
        "rank_ic": oos.corr(labels, method="spearman"),
        "warning": "禁止在最后两年样本外调参；标签需由调用者按未来持有区间构造。",
    }
