"""带时间戳、完整参数与数据版本的本地报告。"""

from pathlib import Path
from datetime import datetime, date
from zoneinfo import ZoneInfo
import json
import html
import numpy as np
import pandas as pd
from domain.backtest import Result

NOTICE = "研究工具，不构成投资建议，不承诺收益。历史回测不代表未来表现。免费行情仅限个人研究；数据完整性、流动性、税费与模型误差可能影响结果。"


def timestamp() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")


def json_safe(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (tuple, list)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, (float, np.floating)) and not np.isfinite(obj):
        return None
    if isinstance(obj, np.floating):
        return float(obj)
    return obj


def export_table(frame: pd.DataFrame, path: str | Path, metadata: dict | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".xlsx":
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            frame.to_excel(writer, index=False, sheet_name="results")
            pd.DataFrame(
                {
                    "metadata": [json.dumps(json_safe(metadata or {}), ensure_ascii=False)],
                    "notice": [NOTICE],
                }
            ).to_excel(writer, index=False, sheet_name="parameters")
    else:
        frame.to_csv(path, index=False, encoding="utf-8-sig")
    path.with_suffix(path.suffix + ".json").write_text(
        json.dumps(json_safe(metadata or {}), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def export_report(result: Result, folder: str | Path) -> Path:
    folder = Path(folder) / f"report_{timestamp()}"
    folder.mkdir(parents=True, exist_ok=True)
    metadata = result.metadata | {"metrics": result.metrics, "notice": NOTICE}
    (folder / "snapshot.json").write_text(
        json.dumps(json_safe(metadata), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for name in ("equity", "trades", "holdings", "rejected"):
        export_table(getattr(result, name), folder / f"{name}.xlsx", result.metadata)
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    fonts = {f.name for f in font_manager.fontManager.ttflist}
    for candidate in ("Microsoft YaHei", "Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans"):
        if candidate in fonts:
            plt.rcParams["font.sans-serif"] = [candidate]
            break
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    dates = pd.to_datetime(result.equity.date)
    axes[0].plot(dates, result.equity.nav, color="#00aa88", label="Strategy NAV")
    axes[0].legend()
    axes[0].grid(alpha=0.2)
    axes[1].fill_between(dates, result.equity.drawdown, 0, color="#e06164", alpha=0.5)
    axes[1].set_ylabel("Drawdown")
    axes[1].grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(folder / "equity.png", dpi=160)
    fig.savefig(folder / "equity.pdf")
    plt.close(fig)
    month = result.metrics.get("monthly", {})
    if month:
        values = pd.Series(month)
        idx = pd.to_datetime(values.index)
        table = pd.DataFrame({"year": idx.year, "month": idx.month, "value": values.values}).pivot(
            index="year", columns="month", values="value"
        )
        fig, ax = plt.subplots(figsize=(10, max(2, len(table) * 0.5)))
        im = ax.imshow(table.to_numpy(), cmap="RdYlGn", aspect="auto", vmin=-0.1, vmax=0.1)
        ax.set_xticks(range(len(table.columns)), table.columns)
        ax.set_yticks(range(len(table.index)), table.index)
        fig.colorbar(im, ax=ax)
        fig.tight_layout()
        fig.savefig(folder / "monthly.png", dpi=160)
        plt.close(fig)
    metrics = pd.DataFrame(
        [
            {
                "metric": k,
                "value": (
                    json.dumps(json_safe(v), ensure_ascii=False)
                    if isinstance(v, (dict, list))
                    else v
                ),
            }
            for k, v in result.metrics.items()
        ]
    )
    body = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>AshareQuant 研究报告</title><style>body{{font:15px system-ui;max-width:1100px;margin:40px auto;padding:24px;background:#101824;color:#dce6f2}}table{{border-collapse:collapse;width:100%}}th,td{{padding:10px;border-bottom:1px solid #304252;text-align:left}}img{{width:100%}}pre{{white-space:pre-wrap}}.notice{{padding:18px;background:#463524;border-radius:8px}}</style><h1>AshareQuant 研究报告</h1><p class="notice">{NOTICE}</p><p>数据版本：{html.escape(result.metadata['data_version'])}</p><p>{html.escape('；'.join(result.metadata.get('warnings',[])))}</p><img src="equity.png"><h2>绩效</h2>{metrics.to_html(index=False,escape=True)}<h2>月度收益</h2><img src="monthly.png"><h2>参数快照</h2><pre>{html.escape(json.dumps(json_safe(result.metadata),ensure_ascii=False,indent=2))}</pre></html>"""
    (folder / "report.html").write_text(body, encoding="utf-8")
    # PDF 使用中文 CID 字体；图与参数摘要均落入报告。
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Image, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    style = ParagraphStyle("cn", fontName="STSong-Light", fontSize=10, leading=16, wordWrap="CJK")
    flow = [
        Paragraph("AshareQuant 研究报告", style),
        Spacer(1, 12),
        Paragraph(NOTICE, style),
        Image(str(folder / "equity.png"), width=480, height=305),
    ]
    for k, v in result.metrics.items():
        flow.append(
            Paragraph(html.escape(f"{k}: {json.dumps(json_safe(v),ensure_ascii=False)}"), style)
        )
    flow.append(Paragraph("数据版本：" + result.metadata["data_version"], style))
    flow.append(
        Paragraph(html.escape(json.dumps(json_safe(result.metadata), ensure_ascii=False)), style)
    )
    SimpleDocTemplate(str(folder / "report.pdf")).build(flow)
    return folder / "report.html"
