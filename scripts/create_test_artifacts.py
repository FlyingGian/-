"""仅开发验收使用：显式合成夹具，不会写入产品默认data目录。"""

from pathlib import Path
import sys
import runpy
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dataclasses import asdict
from application.service import Service, ROOT
from application.reports import export_report, export_table
import application.reports as reports
from infrastructure.storage import REQUIRED


def main():
    data = runpy.run_path(str(ROOT / "tests/conftest.py"))["research"].__wrapped__()
    folder = ROOT / "examples/SYNTHETIC_TEST_ONLY"
    inputs = folder / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    for kind in REQUIRED:
        frame = getattr(data, kind)
        if not frame.empty:
            frame.to_csv(inputs / f"{kind}.csv", index=False, encoding="utf-8-sig")
    # 独立临时测试库：不进入源码包默认运行数据。
    import tempfile

    with tempfile.TemporaryDirectory(prefix="aq-acceptance-") as tmp:
        s = Service(tmp)
        for kind in REQUIRED:
            frame = getattr(data, kind)
            if not frame.empty:
                s.repo.ingest(kind, frame, "synthetic-test-only")
        s.save_settings(
            {
                "weights": {"momentum_20": 0.5, "reversal_5": 0.5},
                "min_listing_days": 20,
                "min_amount": 0,
                "holdings": 10,
            }
        )
        end = data.dates()[125]
        start = data.dates()[90]
        selected = s.run_screen(end)
        result = s.run_backtest(start, end)
        result.metadata["data_provenance"] = "synthetic-test-only"
        result.metadata["warnings"].insert(
            0, "合成测试样例：不是真实市场数据、真实回测业绩或今日荐股。测试日历也为合成夹具。"
        )
        reports.NOTICE = "合成测试样例：非真实行情、非荐股、非投资业绩。" + reports.NOTICE
        report = export_report(result, folder)
        export_table(
            selected,
            folder / "SYNTHETIC_selection.xlsx",
            {
                "notice": reports.NOTICE,
                "data_version": result.metadata["data_version"],
                "parameters": s.config,
            },
        )
        # 生成异动归档样例，独立存放，不进入日频库。
        from domain.realtime import Quote, AlertEngine
        from infrastructure.realtime import Archive, SH
        from datetime import datetime

        t = datetime(2026, 10, 20, 10, 0, tzinfo=SH)
        q = Quote(
            "600000",
            "合成测试证券",
            11,
            10,
            10,
            11,
            10,
            10000,
            110000,
            t,
            t,
            [11] * 5,
            [100] * 5,
            [11.01] * 5,
            [0] * 5,
            11,
            9,
            1e6,
            "synthetic-test-only",
        )
        e = AlertEngine(s.alert_rules)
        alerts = e.ingest(q, t)
        Archive(folder).write([q], alerts, {"600000"})
        replay, notice = Archive(folder).replay(t.date().isoformat(), s.alert_rules)
        (folder / "replay_validation.json").write_text(
            json.dumps(
                {
                    "scope": "仅一个合成场景，不代表真实交易日一致率",
                    "recorded": len(alerts),
                    "replayed": len(replay),
                    "matching": len(replay) == len(alerts),
                    "notice": notice,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        import pandas as pd

        export_table(
            pd.DataFrame([asdict(a) for a in alerts]),
            folder / "SYNTHETIC_alerts.csv",
            {"notice": reports.NOTICE},
        )
        (folder / "README.md").write_text(
            "# 合成测试样例\n\n这里所有行情、名称、财务、日历、选股、回测与异动均为自动化测试夹具，不是真实市场数据、真实投资业绩或今日荐股。不会自动导入产品默认数据库。\n\n用于检验CSV→时点选股→撮合→敏感度→报告→回放的完整流程。输入是15只合成证券、180个合成交易日。样例不足两年，样本外明确标记不满足；历史压力区间亦未满足。\n\n如要复现，主动导入inputs下CSV，将股票池上市日阈值改20、流动性阈值改0，选择momentum_20与reversal_5权重各0.5，日期选2023-05至06月覆盖范围内，再运行。此设置仅为软件验收。\n",
            encoding="utf-8",
        )
        print(report)


if __name__ == "__main__":
    main()
