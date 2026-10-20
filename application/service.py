"""应用用例；UI调用本层，领域算法不直接接触I/O。"""

from pathlib import Path
from dataclasses import asdict, replace
import json
import os
import hashlib
import pandas as pd
import yaml
from domain.strategy import StrategyConfig, screen
from domain.backtest import BacktestConfig, CostConfig, run
from infrastructure.storage import LocalRepository, REQUIRED
from infrastructure.sources import AkShareSource, TushareSource, EastmoneySource

ROOT = Path(__file__).resolve().parents[1]


def read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}


class Service:
    def __init__(self, data_dir: str | Path | None = None):
        self.repo = LocalRepository(data_dir or os.environ.get("ASHAREQUANT_DATA", ROOT / "data"))
        self.settings_path = self.repo.root / "settings.yaml"
        self.config = read_yaml(ROOT / "configs/default.yaml")
        if self.settings_path.exists():
            saved = read_yaml(self.settings_path)
            for k, v in saved.items():
                if isinstance(v, dict) and isinstance(self.config.get(k), dict):
                    self.config[k].update(v)
                else:
                    self.config[k] = v
        self.rules = read_yaml(ROOT / "configs/rules.yaml")
        self.alert_rules = read_yaml(ROOT / "configs/alerts.yaml")
        self.last_screen = pd.DataFrame()
        self.last_result = None

    def save_settings(self, values: dict):
        # Token不写入明文配置，使用Windows凭据保险库/keyring。
        values = {k: v for k, v in values.items() if k != "token"}
        previous = json.dumps(self.config, sort_keys=True, ensure_ascii=False)
        self.config.update(values)
        text = yaml.safe_dump(self.config, allow_unicode=True, sort_keys=False)
        path = self.settings_path.with_suffix(".tmp")
        path.write_text(text, encoding="utf-8")
        path.replace(self.settings_path)
        self.repo.audit(
            "configuration",
            json.dumps(
                {
                    "before_hash": hashlib.sha256(previous.encode()).hexdigest(),
                    "after": self.config,
                },
                ensure_ascii=False,
            ),
        )

    def token(self) -> str:
        if os.environ.get("TUSHARE_TOKEN"):
            return os.environ["TUSHARE_TOKEN"]
        import keyring

        return keyring.get_password("AshareQuant", "tushare") or ""

    def store_token(self, value: str):
        import keyring

        if value:
            keyring.set_password("AshareQuant", "tushare", value)

    def strategy(self) -> StrategyConfig:
        c = self.config
        r = c["risk"]
        mapping = {
            "沪深300": "000300",
            "中证500": "000905",
            "中证1000": "000852",
            "中证2000": "932000",
        }
        return StrategyConfig(
            weights=c["weights"],
            holdings=c["holdings"],
            index_code=mapping.get(c["universe"]),
            min_amount=c["min_amount"],
            min_listing_days=c["min_listing_days"],
            exclude_st=c["exclude_st"],
            single=r["single"],
            industry=r["industry"],
            top5=r["top5"],
            blacklist=tuple(c.get("blacklist", [])),
            whitelist=tuple(c.get("whitelist", [])),
            rules=tuple(tuple(x) for x in c.get("filter_rules", [])),
        )

    def import_csv(self, paths: list[str], progress=lambda *a: None, cancelled=lambda: False):
        results = []
        for i, path in enumerate(paths):
            if cancelled():
                raise InterruptedError("导入已取消")
            kind = Path(path).stem
            if kind not in REQUIRED:
                raise ValueError(f"文件名必须是 {', '.join(REQUIRED)} 中的一种.csv")
            f = pd.read_csv(path, dtype={"code": str, "index_code": str}, encoding="utf-8-sig")
            issues = self.repo.ingest(kind, f, "local_csv")
            results.append({"table": kind, "rows": len(f), "issues": issues})
            progress(int((i + 1) / len(paths) * 100), f"已导入 {kind}: {len(f)} 行")
        return results

    def update(self, start: str, end: str, progress=lambda *a: None, cancelled=lambda: False):
        source = self.config["source"]
        if source == "local_csv":
            raise ValueError("当前选择本地CSV，请使用“导入CSV”或在设置中选择在线源")
        adapter = (
            TushareSource(self.token())
            if source == "tushare"
            else EastmoneySource() if source == "eastmoney" else AkShareSource()
        )
        data = self.repo.load()
        calendar = adapter.get_calendar(start, end)
        self.repo.ingest("calendar", calendar, source)
        if data.stocks.empty:
            self.repo.ingest("stocks", adapter.list_stocks(), source)
            data = self.repo.load()
        codes = data.stocks.code.tolist()
        for i, code in enumerate(codes):
            if cancelled():
                raise InterruptedError("更新已取消；已完成证券保留，下次增量续传")
            old = data.daily.loc[data.daily.code == code]
            last = old.date.max() if not old.empty else None
            begin = (
                max(start, (pd.Timestamp(last) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
                if last
                else start
            )
            if begin <= end:
                f = adapter.get_daily(begin, end, [code])
                if not f.empty:
                    self.repo.ingest("daily", f, source)
                if source == "tushare":
                    val = adapter.get("valuations", begin, end, [code])
                    if not val.empty:
                        self.repo.ingest("valuations", val, source)
                    # 单独更新公告/现金行动；原始报表版本保留，不回填未知历史状态。
                    financial = adapter.get_financials(start, end, [code])
                    if not financial.empty:
                        self.repo.ingest("financials", financial, source)
                    actions = adapter.get("actions", start, end, [code])
                    if not actions.empty:
                        self.repo.ingest("actions", actions, source)
            progress(int((i + 1) / max(1, len(codes)) * 100), f"更新 {code} ({i+1}/{len(codes)})")
        return self.coverage()

    def coverage(self) -> dict:
        d = self.repo.load()
        report = {
            "version": d.version,
            "stocks": len(d.stocks),
            "sessions": len(d.dates()),
            "bars": len(d.daily),
            "financials": len(d.financials),
            "last_date": d.daily.date.max() if not d.daily.empty else "尚未导入",
            "warnings": [],
        }
        if not d.daily.empty:
            report["missing_rates"] = d.daily.isna().mean().to_dict()
            last = d.daily.date.max()
            states = d.state_at(last)
            if len(states) < d.daily.loc[d.daily.date == last].code.nunique():
                report["warnings"].append("最新行情的状态覆盖不足")
            dates = set(d.dates())
            outside = set(d.daily.date) - dates
            if outside:
                report["warnings"].append(f"{len(outside)}个行情日期不在真实交易日历")
            counts = d.daily.groupby("date").code.nunique()
            if (counts.pct_change(fill_method=None).abs() > 0.2).any():
                report["warnings"].append("相邻日期证券数变化超过20%，请核对覆盖范围")
            report["warnings"].append("历史退市覆盖、证券特别限价、分红完整性需要与数据供应方核验")
        for kind in ("stocks", "calendar", "states", "financials", "members", "actions"):
            if getattr(d, kind).empty:
                report["warnings"].append(f"尚无 {kind} 数据")
        return report

    def run_screen(self, date: str, progress=lambda *a: None, cancelled=lambda: False):
        progress(5, "读取本地数据快照")
        data = self.repo.load()
        if cancelled():
            raise InterruptedError("选股已取消")
        if self.config["strict_data"]:
            data.check_history([date])
        previous = set(self.last_screen.code) if not self.last_screen.empty else set()
        result = screen(data, date, self.strategy(), previous)
        self.last_screen = result
        progress(100, f"已选出 {len(result)} 只证券")
        return result

    def run_backtest(
        self,
        start: str,
        end: str,
        progress=lambda *a: None,
        cancelled=lambda: False,
        sensitivity: bool = True,
    ):
        data = self.repo.load()
        strategy = self.strategy()
        c = self.config
        cfg = BacktestConfig(
            capital=c["capital"],
            frequency=c["rebalance"],
            strict=c["strict_data"],
            rules=self.rules,
            costs=CostConfig(**c["costs"]),
            daily_turnover=c["risk"]["daily_turnover"],
            warn_drawdown=c["risk"]["warn_drawdown"],
            reduce_drawdown=c["risk"]["reduce_drawdown"],
        )
        cache = {}

        def signal(date):
            if date not in cache:
                result = screen(data, date, strategy)
                cache[date] = dict(zip(result.code, result.weight)) if not result.empty else {}
            return cache[date]

        benchmark_code = c.get("benchmark", "000300")
        benchmark = (
            data.benchmarks.loc[data.benchmarks.code == benchmark_code].set_index("date").close
            if not data.benchmarks.empty
            else None
        )
        result = run(data, start, end, signal, cfg, progress, cancelled, benchmark)
        if benchmark is None:
            result.metadata["warnings"].append("未导入真实基准；不输出虚构超额收益或跟踪误差")
        result.metadata["parameters"] = asdict(cfg) | {"strategy": asdict(strategy)}
        result.metadata["config_hash"] = hashlib.sha256(
            json.dumps(result.metadata["parameters"], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        cutoff = (pd.Timestamp(end) - pd.DateOffset(years=2)).strftime("%Y-%m-%d")
        from domain.metrics import performance

        if start >= cutoff:
            result.metrics["oos_warning"] = "不足独立两年样本外；不能证明泛化能力"
        else:
            oos = result.equity.loc[result.equity.date >= cutoff]
            prior = result.equity.loc[result.equity.date < cutoff]
            initial = float(prior.equity.iloc[-1]) if not prior.empty else cfg.capital
            result.metrics["last_two_years"] = performance(oos, initial)
            result.metrics["oos_warning"] = (
                "最近两年单列是时间切片；仅在参数从未看过该区间时才能称真正样本外"
            )
        if sensitivity:
            sensitivity_results = {1: result.metrics["total_return"]}
            for multiple in (0, 2, 3):
                if cancelled():
                    raise InterruptedError("敏感度分析已取消")
                progress(90, f"成本敏感度 {multiple}x")
                alt = run(
                    data,
                    start,
                    end,
                    signal,
                    replace(cfg, costs=replace(cfg.costs, multiplier=multiple)),
                    cancelled=cancelled,
                )
                sensitivity_results[multiple] = alt.metrics["total_return"]
                if multiple == 0:
                    result.metrics["cost_erosion_amount"] = float(
                        alt.equity.equity.iloc[-1] - result.equity.equity.iloc[-1]
                    )
            result.metrics["cost_sensitivity"] = sensitivity_results
            if sensitivity_results[0] > 0 >= sensitivity_results[1]:
                result.metadata["warnings"].append("策略在真实成本后失效")
        # 两个真实压力区间只在本地数据完整覆盖时计算，不用合成值填补。
        stress = {}
        intervals = {
            "2018熊市": ("2018-01-02", "2018-12-28"),
            "2024年初小盘压力": ("2024-01-02", "2024-02-08"),
        }
        for label, (a, b) in intervals.items():
            part = result.equity.loc[(result.equity.date >= a) & (result.equity.date <= b)]
            if part.empty or result.equity.date.min() > a or result.equity.date.max() < b:
                stress[label] = {"status": "数据/回测区间不足，未验证"}
            else:
                prior = result.equity.loc[result.equity.date < a]
                stress[label] = performance(
                    part, float(prior.equity.iloc[-1]) if not prior.empty else cfg.capital
                )
        result.metrics["stress_intervals"] = stress
        if sensitivity:
            parameters = {}
            for ratio in (0.8, 1.2):
                altered = replace(strategy, holdings=max(1, round(strategy.holdings * ratio)))

                def alternate_signal(date):
                    selected = screen(data, date, altered)
                    return dict(zip(selected.code, selected.weight)) if not selected.empty else {}

                progress(95, f"持仓参数敏感度 {altered.holdings}只")
                alt = run(data, start, end, alternate_signal, cfg, cancelled=cancelled)
                parameters[str(altered.holdings)] = alt.metrics["total_return"]
            result.metrics["holdings_sensitivity"] = parameters
            spread = max(parameters.values()) - min(parameters.values()) if parameters else 0
            if spread > max(0.05, abs(result.metrics["total_return"]) * 0.5):
                result.metadata["warnings"].append("持仓数扰动敏感，存在过拟合风险")
        self.last_result = result
        return result
