"""原生桌面工作站。计算、更新、导出和网络均通过后台任务。"""

from pathlib import Path
from datetime import datetime
from dataclasses import asdict
import asyncio
import json
import pandas as pd
import httpx
from PySide6.QtCore import QTimer, QThreadPool, QSettings, QDate, Signal
from PySide6.QtGui import QKeySequence, QAction, QIcon, QColor, QPixmap
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QStackedWidget,
    QFrame,
    QLineEdit,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QTextEdit,
    QPlainTextEdit,
    QFileDialog,
    QMessageBox,
    QProgressBar,
    QDateEdit,
    QFormLayout,
    QTabWidget,
    QSystemTrayIcon,
    QMenu,
    QWizard,
    QWizardPage,
)
from application.service import Service, ROOT
from application.reports import export_table, export_report, timestamp, NOTICE
from application.diagnostics import check_environment
from domain.factors import REGISTRY, compute, expression
from domain.analysis import factor_analysis
from domain.strategy import risk_report
from domain.realtime import AlertEngine, price_change
from infrastructure.realtime import SinaBatchSource, TencentBatchSource, Collector, Archive, SH
from presentation.widgets import Table, Worker, Chart, Candles

DARK = """
QWidget { background:#0d1521; color:#dce7f4; font-family:"Microsoft YaHei","Noto Sans CJK SC","DejaVu Sans"; font-size:13px; }
QMainWindow {background:#0d1521;}
QLabel#brand {font-size:25px;font-weight:700;color:#eef7ff;}
QLabel#subtitle {color:#7f96b1;font-size:11px;}
QLabel#heading {font-size:25px;font-weight:600;}
QLabel#muted {color:#8ba0ba;}
QLabel#number {font-size:30px;font-weight:600;color:#47dbb4;}
QFrame#card {background:#142031;border:1px solid #24354a;border-radius:10px;}
QFrame#card QLabel {background:transparent;}
QListWidget {background:#101b2a;border:0;padding:10px;outline:0;}
QListWidget::item {padding:12px 14px;border-radius:7px;margin:2px 0;color:#96abc4;}
QListWidget::item:selected {background:#17413f;color:#5de0bc;border-left:3px solid #4cd8af;}
QPushButton {background:#193344;border:1px solid #2b4a61;border-radius:6px;padding:8px 16px;min-height:20px;}
QPushButton:hover {background:#245067;border-color:#4ecfaf;}
QPushButton:disabled {background:#17212e;color:#596c82;}
QPushButton#primary {background:#31bd98;border:0;color:#09271f;font-weight:600;}
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QDateEdit {background:#142235;border:1px solid #2a3d55;border-radius:5px;padding:7px;min-height:20px;}
QTextEdit,QPlainTextEdit {background:#111c2a;border:1px solid #273b50;border-radius:6px;padding:9px;}
QTableView {background:#111c2a;alternate-background-color:#152235;border:1px solid #27394d;border-radius:6px;gridline-color:#203045;selection-background-color:#204d57;}
QHeaderView::section {background:#1b2a3d;color:#92adc8;border:0;padding:10px;font-weight:600;}
QTableView::item {padding:5px;border-bottom:1px solid #1b2b3e;}
QTabWidget::pane {border:1px solid #23384c;}
QTabBar::tab {background:#142235;padding:10px 18px;color:#91a8c2;}
QTabBar::tab:selected {color:#5be0ba;border-bottom:2px solid #43d4ae;}
QProgressBar {border:0;background:#1d2a3d;height:5px;border-radius:3px;}
QProgressBar::chunk {background:#3dcba4;}
QStatusBar {background:#101d2d;color:#8ba4be;}
QToolTip {background:#20334a;border:1px solid #45657d;color:white;}
QScrollBar:vertical {background:#142235;width:9px;}
QScrollBar::handle:vertical {background:#344d64;min-height:25px;border-radius:4px;}
"""
LIGHT = """QWidget{background:#f3f6fa;color:#20354b;font-family:"Microsoft YaHei";font-size:13px;}QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QDateEdit,QTextEdit,QPlainTextEdit,QTableView{background:white;border:1px solid #c8d5e2;padding:6px;}QPushButton{background:#d9eee8;border:1px solid #b1d4c9;padding:8px;border-radius:5px;}QListWidget::item{padding:12px;}QListWidget::item:selected{background:#cfebe1;}QLabel#heading{font-size:25px;font-weight:600;}QLabel#number{font-size:28px;color:#15866c;}QHeaderView::section{background:#e1e9f2;padding:9px;}QFrame#card{border:1px solid #c5d5e2;border-radius:8px;}"""


def button(text, call, primary=False):
    b = QPushButton(text)
    b.clicked.connect(call)
    if primary:
        b.setObjectName("primary")
    return b


def spin(value, minimum=0, maximum=1e12, decimals=2):
    s = QDoubleSpinBox()
    s.setRange(minimum, maximum)
    s.setDecimals(decimals)
    s.setValue(value)
    return s


def date_edit(value):
    d = QDateEdit(QDate.fromString(value, "yyyy-MM-dd"))
    d.setCalendarPopup(True)
    d.setDisplayFormat("yyyy-MM-dd")
    return d


class MainWindow(QMainWindow):
    realtime_batch = Signal(object)

    def __init__(self, service=None, first_run=True):
        super().__init__()
        self.service = service or Service()
        self.setWindowTitle("AshareQuant · A股量化研究工作站")
        self.resize(1440, 960)
        self.setMinimumSize(1100, 760)
        self.setStyleSheet(DARK)
        self.settings = QSettings("AshareQuant", "Desktop")
        self.jobs = []
        self.job = None
        self.live_job = None
        self.live_store = {}
        self.live_alerts = []
        self.latest_batch = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.closing = False
        self.tables = []
        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QWidget()
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 24, 12, 16)
        brand = QLabel("AshareQuant")
        brand.setObjectName("brand")
        side.addWidget(brand)
        sub = QLabel("A 股 量 化 研 究 工 作 站")
        sub.setObjectName("subtitle")
        side.addWidget(sub)
        side.addSpacing(28)
        self.nav = QListWidget()
        side.addWidget(self.nav)
        side.addWidget(QLabel("LOCAL RESEARCH  /  v1.0.1"))
        outer.addWidget(sidebar)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 22, 28, 16)
        layout.setSpacing(16)
        top = QHBoxLayout()
        self.heading = QLabel("研究总览")
        self.heading.setObjectName("heading")
        top.addWidget(self.heading)
        top.addStretch()
        self.clock = QLabel()
        self.clock.setObjectName("muted")
        top.addWidget(self.clock)
        layout.addLayout(top)
        self.source_status = QLabel("离线研究就绪  ·  实时监控尚未启动")
        self.source_status.setObjectName("muted")
        layout.addWidget(self.source_status)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        status = QHBoxLayout()
        self.task_label = QLabel("请导入经核验的数据，或配置在线数据源")
        self.task_label.setObjectName("muted")
        status.addWidget(self.task_label, 1)
        self.cancel = button("取消当前任务", self.cancel_job)
        self.cancel.hide()
        status.addWidget(self.cancel)
        layout.addLayout(status)
        notice = QLabel(
            "研究工具，不构成投资建议。历史表现不代表未来收益；数据来源与完整性须独立核验。"
        )
        notice.setObjectName("muted")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        outer.addWidget(content, 1)
        builders = [
            ("研究总览", self.dashboard_page),
            ("实时盯盘", self.monitor_page),
            ("数据管理", self.data_page),
            ("股票池", self.universe_page),
            ("因子研究", self.factors_page),
            ("策略配置", self.strategy_page),
            ("选股清单", self.results_page),
            ("个股详情", self.detail_page),
            ("回测验证", self.backtest_page),
            ("风险监控", self.risk_page),
            ("系统设置", self.settings_page),
            ("环境自检", self.diagnostics_page),
        ]
        for name, builder in builders:
            self.nav.addItem(name)
            self.stack.addWidget(builder())
        self.nav.currentRowChanged.connect(self.change_page)
        self.nav.setCurrentRow(0)
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        for i, table in enumerate(self.tables):
            state = self.settings.value(f"table/{i}")
            if state:
                table.horizontalHeader().restoreState(state)
        for shortcut, call in (
            ("Ctrl+R", self.update_data),
            ("Ctrl+B", self.backtest),
            ("F5", self.refresh),
        ):
            action = QAction(self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(call)
            self.addAction(action)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.realtime_batch.connect(self.receive_batch)
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            pix = QPixmap(32, 32)
            pix.fill(QColor("#37c99f"))
            self.tray = QSystemTrayIcon(QIcon(pix), self)
            menu = QMenu()
            menu.addAction("显示工作站", self.showNormal)
            menu.addAction("退出", self.close)
            self.tray.setContextMenu(menu)
            self.tray.setToolTip("AshareQuant")
            self.tray.show()
        self.refresh()
        if first_run and not self.service.config.get("onboarded"):
            QTimer.singleShot(300, self.wizard)

    def page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        return w, v

    def table(self, frame=None):
        t = Table(frame)
        self.tables.append(t)
        return t

    def note(self, text):
        n = QLabel(text)
        n.setWordWrap(True)
        n.setObjectName("muted")
        return n

    def change_page(self, index):
        self.stack.setCurrentIndex(index)
        self.heading.setText(self.nav.item(index).text())

    def go(self, index):
        self.nav.setCurrentRow(index)

    def start_task(self, call, finished=None):
        if self.job:
            QMessageBox.information(self, "任务运行中", "请等待当前任务结束或点击取消。")
            return
        job = Worker(call)
        self.job = job
        self.jobs.append(job)
        self.progress.setValue(0)
        self.progress.show()
        self.cancel.show()
        job.signals.progress.connect(self.on_progress)
        if finished:
            job.signals.finished.connect(finished)
        job.signals.failed.connect(self.error)
        job.signals.done.connect(lambda: self.task_done(job))
        self.pool.start(job)

    def on_progress(self, value, message):
        self.progress.setValue(value)
        self.task_label.setText(message)

    def task_done(self, job):
        if self.job is job:
            self.job = None
            self.progress.hide()
            self.cancel.hide()
        if job in self.jobs:
            self.jobs.remove(job)

    def error(self, text):
        self.task_label.setText(text)
        self.log.appendPlainText(text)
        if not self.closing:
            QMessageBox.warning(self, "操作未完成", text + "\n\n详细诊断已记录到日志。")

    def cancel_job(self):
        if self.job:
            self.job.stop.set()
            self.task_label.setText("正在安全取消，请等待当前请求结束…")

    def dashboard_page(self):
        w, v = self.page()
        row = QHBoxLayout()
        self.cards = {}
        for key, title in (
            ("stocks", "证券覆盖"),
            ("last_date", "行情最新日期"),
            ("selected", "当前选股"),
            ("version", "数据快照"),
        ):
            frame = QFrame()
            frame.setObjectName("card")
            box = QVBoxLayout(frame)
            box.setContentsMargins(18, 18, 18, 18)
            box.addWidget(self.note(title))
            label = QLabel("—")
            label.setObjectName("number")
            box.addWidget(label)
            self.cards[key] = label
            row.addWidget(frame)
        v.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button("导入研究数据", self.import_csv, True))
        row.addWidget(button("运行选股", self.run_screen))
        row.addWidget(button("配置策略", lambda: self.go(5)))
        row.addStretch()
        v.addLayout(row)
        self.home_chart = Chart("组合净值 · 运行回测后显示")
        v.addWidget(self.home_chart, 2)
        v.addWidget(QLabel("数据质量与研究状态"))
        self.home_warnings = QTextEdit()
        self.home_warnings.setReadOnly(True)
        v.addWidget(self.home_warnings, 1)
        return w

    def monitor_page(self):
        w, v = self.page()
        row = QHBoxLayout()
        self.live_mode = QComboBox()
        self.live_mode.addItems(["全市场（按已导入基础信息）", "当前选股清单"])
        row.addWidget(self.live_mode)
        row.addWidget(button("开始监控", self.start_live, True))
        row.addWidget(button("停止", self.stop_live))
        row.addWidget(button("盘外单次快照", lambda: self.start_live(True)))
        row.addWidget(button("归档回放", self.replay))
        v.addLayout(row)
        self.sentiment = QLabel("上涨 —   下跌 —   涨停 —   跌停 —   成交额 —")
        v.addWidget(self.sentiment)
        self.live_filter = QComboBox()
        self.live_filter.addItems(
            [
                "全部异动",
                "limit_up",
                "break_limit",
                "limit_down",
                "surge",
                "plunge",
                "volume_spike",
                "pullback",
                "queue_drop",
            ]
        )
        self.live_filter.currentIndexChanged.connect(self.refresh_alerts)
        v.addWidget(self.live_filter)
        tabs = QTabWidget()
        self.quote_table = self.table()
        self.alert_table = self.table()
        tabs.addTab(self.quote_table, "行情快照")
        tabs.addTab(self.alert_table, "异动流")
        v.addWidget(tabs)
        v.addWidget(
            self.note(
                "延迟以行情自带时间计算。超过15秒停止新信号；实时目录独立于日频研究库。分钟归档仅用于概览，自选逐笔支持精确回放。"
            )
        )
        return w

    def data_page(self):
        w, v = self.page()
        row = QHBoxLayout()
        row.addWidget(button("导入CSV", self.import_csv, True))
        row.addWidget(button("导出CSV模板", self.templates))
        row.addWidget(button("一键增量更新", self.update_data))
        row.addWidget(button("覆盖与质量检查", self.refresh))
        row.addStretch()
        v.addLayout(row)
        dates = QHBoxLayout()
        dates.addWidget(QLabel("更新起始"))
        self.data_start = date_edit("2010-01-01")
        dates.addWidget(self.data_start)
        dates.addWidget(QLabel("更新结束"))
        self.data_end = date_edit(datetime.now(SH).strftime("%Y-%m-%d"))
        dates.addWidget(self.data_end)
        dates.addStretch()
        v.addLayout(dates)
        self.coverage_table = self.table()
        v.addWidget(self.coverage_table, 1)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1500)
        v.addWidget(self.log, 1)
        v.addWidget(
            self.note(
                "批量导入文件名：stocks、calendar、daily、states、financials、valuations、members、actions、benchmarks.csv。先校验后落盘，缺失字段不会自动伪造。"
            )
        )
        return w

    def universe_page(self):
        w, v = self.page()
        form = QFormLayout()
        self.universe = QComboBox()
        self.universe.addItems(["全A", "沪深300", "中证500", "中证1000", "中证2000"])
        self.universe.setCurrentText(self.service.config["universe"])
        self.exclude = QCheckBox("排除历史当日ST")
        self.exclude.setChecked(self.service.config["exclude_st"])
        self.listing = QSpinBox()
        self.listing.setRange(0, 1000)
        self.listing.setValue(self.service.config["min_listing_days"])
        self.amount = spin(self.service.config["min_amount"], 0, 1e12, 0)
        self.black = QLineEdit(",".join(self.service.config.get("blacklist", [])))
        self.white = QLineEdit(",".join(self.service.config.get("whitelist", [])))
        for k, c in (
            ("预设股票池", self.universe),
            ("风险过滤", self.exclude),
            ("最少上市交易日", self.listing),
            ("20日均成交额下限（元）", self.amount),
            ("黑名单（逗号分隔）", self.black),
            ("白名单（仍服从风控）", self.white),
        ):
            form.addRow(k, c)
        v.addLayout(form)
        v.addWidget(button("保存股票池与名单", self.save_universe, True))
        self.universe_table = self.table()
        v.addWidget(self.universe_table)
        v.addWidget(
            self.note(
                "指数研究按历史成分区间取值；没有历史成分数据时停止，不用当前名单回溯。人工名单变更写入审计。"
            )
        )
        return w

    def factors_page(self):
        w, v = self.page()
        row = QHBoxLayout()
        self.factor = QComboBox()
        for f in REGISTRY.values():
            self.factor.addItem(f"{f.label} · {f.name}", f.name)
        row.addWidget(self.factor, 1)
        row.addWidget(button("计算最新截面", self.factor_snapshot))
        row.addWidget(button("分析IC与分层", self.analyze_factor, True))
        v.addLayout(row)
        self.factor_expression = QLineEdit()
        self.factor_expression.setPlaceholderText(
            "自定义表达式，例如 ep + bp * 0.5（只允许四则运算）"
        )
        r = QHBoxLayout()
        r.addWidget(self.factor_expression)
        r.addWidget(button("计算表达式", self.custom_factor))
        v.addLayout(r)
        tabs = QTabWidget()
        self.factor_table = self.table(pd.DataFrame([asdict(f) for f in REGISTRY.values()]))
        tabs.addTab(self.factor_table, "因子定义 / 截面")
        self.ic_chart = Chart("IC / RankIC")
        tabs.addTab(self.ic_chart, "IC时序")
        self.layer_chart = Chart("五层净值 · 未计交易费用的统计诊断")
        tabs.addTab(self.layer_chart, "分层收益")
        self.factor_summary = QTextEdit()
        self.factor_summary.setReadOnly(True)
        tabs.addTab(self.factor_summary, "分析摘要")
        self.corr_table = self.table()
        tabs.addTab(self.corr_table, "因子相关性")
        v.addWidget(tabs)
        return w

    def strategy_page(self):
        w, v = self.page()
        form = QFormLayout()
        self.count = QSpinBox()
        self.count.setRange(1, 200)
        self.count.setValue(self.service.config["holdings"])
        self.frequency = QComboBox()
        self.frequency.addItems(["daily", "weekly", "monthly"])
        self.frequency.setCurrentText(self.service.config["rebalance"])
        self.weights = QPlainTextEdit(
            json.dumps(self.service.config["weights"], ensure_ascii=False, indent=2)
        )
        self.weights.setMaximumHeight(220)
        self.filter_rules = QPlainTextEdit(
            json.dumps(self.service.config.get("filter_rules", []), ensure_ascii=False)
        )
        self.filter_rules.setMaximumHeight(100)
        self.filter_rules.setPlaceholderText(
            '[["roe", ">", 0.10], ["pe", "<", 20]]（字段必须为已注册因子）'
        )
        form.addRow("目标持仓数量", self.count)
        form.addRow("调仓频率", self.frequency)
        form.addRow("因子权重JSON", self.weights)
        form.addRow("筛选条件JSON", self.filter_rules)
        v.addLayout(form)
        v.addWidget(button("验证并保存策略", self.save_strategy, True))
        v.addWidget(
            self.note(
                "先处理缺失、MAD去极值、截面标准化、行业与市值中性化，再统一方向。全缺失或常数因子不能凭空形成信息。剩余权重留现金。"
            )
        )
        v.addStretch()
        return w

    def results_page(self):
        w, v = self.page()
        row = QHBoxLayout()
        self.screen_date = date_edit(datetime.now(SH).strftime("%Y-%m-%d"))
        row.addWidget(QLabel("信号日期（收盘后）"))
        row.addWidget(self.screen_date)
        row.addWidget(button("运行选股", self.run_screen, True))
        row.addWidget(button("导出Excel / CSV", self.export_screen))
        self.result_search = QLineEdit()
        self.result_search.setPlaceholderText("搜索代码、名称、行业")
        row.addWidget(self.result_search, 1)
        v.addLayout(row)
        self.results = self.table()
        self.result_search.textChanged.connect(self.results.proxy.setFilterFixedString)
        self.results.doubleClicked.connect(self.show_stock)
        v.addWidget(self.results)
        self.result_note = self.note(
            "信号最早下一交易日执行；双击证券查看K线。每个contribution列解释因子对总分的贡献。"
        )
        v.addWidget(self.result_note)
        return w

    def detail_page(self):
        w, v = self.page()
        r = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("六位股票代码")
        r.addWidget(self.code)
        self.adjust = QComboBox()
        self.adjust.addItems(["hfq", "qfq", "none"])
        r.addWidget(self.adjust)
        r.addWidget(button("查看历史", self.stock_detail, True))
        v.addLayout(r)
        self.candle_chart = Chart("K线 · 按所选复权方式")
        self.volume_chart = Chart("成交量（股）")
        v.addWidget(self.candle_chart, 2)
        v.addWidget(self.volume_chart, 1)
        self.financial_table = self.table()
        v.addWidget(self.financial_table, 1)
        return w

    def backtest_page(self):
        w, v = self.page()
        r = QHBoxLayout()
        self.bt_start = date_edit("2020-01-01")
        self.bt_end = date_edit(datetime.now(SH).strftime("%Y-%m-%d"))
        r.addWidget(QLabel("起始"))
        r.addWidget(self.bt_start)
        r.addWidget(QLabel("结束"))
        r.addWidget(self.bt_end)
        r.addWidget(button("运行回测与成本敏感度", self.backtest, True))
        r.addWidget(button("导出完整报告", self.export_backtest))
        v.addLayout(r)
        tabs = QTabWidget()
        self.nav_chart = Chart("累计净值")
        self.dd_chart = Chart("回撤")
        tabs.addTab(self.nav_chart, "净值")
        tabs.addTab(self.dd_chart, "回撤")
        self.metric_table = self.table()
        self.holdings_table = self.table()
        self.trades_table = self.table()
        self.rejected_table = self.table()
        self.month_table = self.table()
        for table, title in (
            (self.metric_table, "绩效与敏感度"),
            (self.month_table, "月度收益"),
            (self.holdings_table, "每日持仓"),
            (self.trades_table, "成交明细"),
            (self.rejected_table, "拒单记录"),
        ):
            tabs.addTab(table, title)
        v.addWidget(tabs)
        v.addWidget(
            self.note(
                "严格模式校验历史状态、日历与复权/行动一致性。报告保留数据与配置哈希；独立样本外需要至少最后两年从未用于调参。"
            )
        )
        return w

    def risk_page(self):
        w, v = self.page()
        r = QHBoxLayout()
        self.risk_inputs = {}
        for key, label in (
            ("single", "单票上限"),
            ("industry", "行业上限"),
            ("top5", "前五上限"),
            ("daily_turnover", "日换手上限"),
        ):
            r.addWidget(QLabel(label))
            s = spin(self.service.config["risk"][key], 0.01, 1, 2)
            r.addWidget(s)
            self.risk_inputs[key] = s
        v.addLayout(r)
        row = QHBoxLayout()
        row.addWidget(button("保存风险约束", self.save_risk, True))
        row.addWidget(button("检查最近组合", self.check_risk))
        row.addStretch()
        v.addLayout(row)
        self.risk_text = QTextEdit()
        self.risk_text.setReadOnly(True)
        v.addWidget(self.risk_text)
        v.addWidget(
            self.note(
                "15%回撤预警、20%回撤减半目标；受涨跌停及流动性约束，实际减仓可能无法完成。真实压力区间需要完整历史数据，不用合成行情冒充历史。"
            )
        )
        return w

    def settings_page(self):
        w, v = self.page()
        form = QFormLayout()
        self.source = QComboBox()
        self.source.addItems(["local_csv", "eastmoney", "tushare"])
        self.source.setCurrentText(self.service.config["source"])
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("留空保留；写入操作系统凭据库")
        self.capital = spin(self.service.config["capital"], 10000, 1e10, 0)
        self.strict = QCheckBox("严格数据质量闸门（正式研究推荐）")
        self.strict.setChecked(self.service.config["strict_data"])
        self.theme = QComboBox()
        self.theme.addItems(["深色", "浅色"])
        self.theme.currentIndexChanged.connect(
            lambda i: self.setStyleSheet(DARK if i == 0 else LIGHT)
        )
        form.addRow("数据源", self.source)
        form.addRow("Tushare Token", self.token_edit)
        form.addRow("研究资金（元）", self.capital)
        form.addRow("数据质量", self.strict)
        form.addRow("主题", self.theme)
        self.cost_edit = QPlainTextEdit(json.dumps(self.service.config["costs"], indent=2))
        self.cost_edit.setMaximumHeight(220)
        form.addRow("费用参数JSON", self.cost_edit)
        form.addRow("数据目录", QLabel(str(self.service.repo.root)))
        form.addRow("规则版本", QLabel(self.service.rules["rule_version"]))
        v.addLayout(form)
        v.addWidget(button("验证并保存设置", self.save_settings, True))
        v.addWidget(
            self.note(
                "交易规则来源与生效日期见configs/rules.yaml。变更规则后旧回测应重新运行；实际证券上下限优先于通用板块规则。"
            )
        )
        v.addStretch()
        return w

    def diagnostics_page(self):
        w, v = self.page()
        r = QHBoxLayout()
        r.addWidget(button("运行完整自检（含网络）", self.diagnostics, True))
        r.addWidget(button("查看日志目录", self.open_logs))
        r.addStretch()
        v.addLayout(r)
        self.diag_table = self.table()
        v.addWidget(self.diag_table)
        return w

    def refresh(self):
        def done(coverage):
            for key in ("stocks", "last_date", "version"):
                text = str(coverage[key])
                self.cards[key].setText(text[:10] if key == "version" else text)
            self.cards["selected"].setText(str(len(self.service.last_screen)))
            self.home_warnings.setPlainText(
                "\n".join("• " + x for x in coverage["warnings"]) or "质量检查未发现告警"
            )
            self.coverage_table.set_frame(
                pd.DataFrame(
                    [{"项目": k, "值": str(v)} for k, v in coverage.items() if k != "warnings"]
                )
            )
            if coverage["last_date"] != "尚未导入":
                self.screen_date.setDate(QDate.fromString(coverage["last_date"], "yyyy-MM-dd"))
            self.task_label.setText("数据覆盖检查完成；研究结果须结合质量告警判断")

        self.start_task(lambda p, c: self.service.coverage(), done)

    def import_csv(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择研究数据CSV", str(ROOT), "CSV (*.csv)")
        if files:
            self.start_task(
                lambda p, c: self.service.import_csv(files, p, c),
                lambda x: (
                    self.log.appendPlainText(json.dumps(x, ensure_ascii=False, indent=2)),
                    QTimer.singleShot(50, self.refresh),
                ),
            )

    def templates(self):
        folder = QFileDialog.getExistingDirectory(self, "选择模板保存目录")
        if folder:
            from infrastructure.storage import REQUIRED

            for name, cols in REQUIRED.items():
                pd.DataFrame(columns=cols).to_csv(
                    Path(folder) / f"{name}.csv", index=False, encoding="utf-8-sig"
                )
            self.task_label.setText("已导出9张空白模板；字段单位见数据字典")

    def update_data(self):
        start = self.data_start.date().toString("yyyy-MM-dd")
        end = self.data_end.date().toString("yyyy-MM-dd")
        self.start_task(
            lambda p, c: self.service.update(start, end, p, c),
            lambda x: (
                self.log.appendPlainText(json.dumps(x, ensure_ascii=False, default=str)),
                QTimer.singleShot(50, self.refresh),
            ),
        )

    def save_universe(self):
        values = dict(
            universe=self.universe.currentText(),
            exclude_st=self.exclude.isChecked(),
            min_listing_days=self.listing.value(),
            min_amount=self.amount.value(),
            blacklist=[x.strip() for x in self.black.text().split(",") if x.strip()],
            whitelist=[x.strip() for x in self.white.text().split(",") if x.strip()],
        )
        self.service.save_settings(values)
        self.task_label.setText("股票池已保存，人工名单已留痕")

    def save_strategy(self):
        try:
            from domain.strategy import StrategyConfig

            weights = json.loads(self.weights.toPlainText())
            rules = json.loads(self.filter_rules.toPlainText() or "[]")
            if set(weights) - set(REGISTRY):
                raise ValueError("存在未注册的因子")
            for r in rules:
                if len(r) != 3 or r[0] not in REGISTRY or r[1] not in (">", "<", ">=", "<="):
                    raise ValueError("筛选规则格式无效")
            StrategyConfig(weights=weights, holdings=self.count.value())
            self.service.save_settings(
                dict(
                    weights=weights,
                    holdings=self.count.value(),
                    rebalance=self.frequency.currentText(),
                    filter_rules=rules,
                )
            )
            self.task_label.setText("策略参数已保存，已有回测应重新运行")
        except (ValueError, TypeError) as e:
            self.error(str(e))

    def run_screen(self):
        date = self.screen_date.date().toString("yyyy-MM-dd")

        def done(frame):
            self.results.set_frame(frame)
            self.cards["selected"].setText(str(len(frame)))
            self.go(6)
            self.result_note.setText(
                f"信号日期 {date} · 最早下一交易日执行 · 本次剔除 {frame.attrs.get('removed',[])}"
            )

        self.start_task(lambda p, c: self.service.run_screen(date, p, c), done)

    def export_screen(self):
        if self.service.last_screen.empty:
            self.error("请先生成选股清单")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出选股清单", f"selection_{timestamp()}.xlsx", "Excel (*.xlsx);;CSV (*.csv)"
        )
        if path:
            self.start_task(
                lambda p, c: export_table(
                    self.service.last_screen,
                    path,
                    {
                        "parameters": self.service.config,
                        "date": self.screen_date.date().toString("yyyy-MM-dd"),
                        "data_version": self.service.repo.version(),
                    },
                ),
                lambda x: self.task_label.setText(f"已导出 {x}"),
            )

    def show_stock(self, index):
        source = self.results.proxy.mapToSource(index)
        frame = self.results.source.frame
        if "code" in frame:
            self.code.setText(str(frame.iloc[source.row()].code))
            self.go(7)
            self.stock_detail()

    def stock_detail(self):
        code = self.code.text().strip()
        adjust = self.adjust.currentText()

        def call(p, c):
            data = self.service.repo.load()
            bars = self.service.repo.get_price(code, "1900-01-01", "9999-12-31", adjust).tail(180)
            return bars, (
                data.financial_at(bars.date.max()).loc[lambda x: x.code == code]
                if not bars.empty
                else pd.DataFrame()
            )

        def done(result):
            bars, f = result
            self.candle_chart.clear()
            self.volume_chart.clear()
            if not bars.empty:
                self.candle_chart.addItem(Candles(bars))
                self.volume_chart.line(bars.volume)
            self.financial_table.set_frame(f)

        self.start_task(call, done)

    def factor_snapshot(self):
        date = self.screen_date.date().toString("yyyy-MM-dd")
        name = self.factor.currentData()
        self.start_task(
            lambda p, c: compute(self.service.repo.load(), date, [name]).reset_index(),
            self.factor_table.set_frame,
        )

    def custom_factor(self):
        date = self.screen_date.date().toString("yyyy-MM-dd")
        text = self.factor_expression.text()

        def call(p, c):
            raw = compute(self.service.repo.load(), date)
            raw["custom"] = expression(text, raw)
            return raw.reset_index()

        self.start_task(call, self.factor_table.set_frame)

    def analyze_factor(self):
        name = self.factor.currentData()
        start = self.bt_start.date().toString("yyyy-MM-dd")
        end = self.bt_end.date().toString("yyyy-MM-dd")

        def done(result):
            self.ic_chart.clear()
            self.layer_chart.clear()
            series = result["series"]
            if not series.empty:
                for h, g in series.groupby("horizon"):
                    if h == 1:
                        self.ic_chart.line(g.ic, color="#6e9ee9")
                        self.ic_chart.line(g.rank_ic)
            colors = ["#6a8caf", "#8aa6be", "#c7cdd6", "#82ceb0", "#3cd9ad"]
            for i, col in enumerate(result["layers"]):
                self.layer_chart.line(result["layers"][col], color=colors[i])
            self.factor_summary.setPlainText(
                json.dumps(
                    {
                        k: v
                        for k, v in result.items()
                        if k in ("summary", "turnover", "monotonicity", "warning")
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

        self.start_task(
            lambda p, c: factor_analysis(
                self.service.repo.load(), name, start, end, progress=p, cancelled=c
            ),
            done,
        )

    def backtest(self):
        start = self.bt_start.date().toString("yyyy-MM-dd")
        end = self.bt_end.date().toString("yyyy-MM-dd")

        def done(r):
            self.nav_chart.clear()
            self.dd_chart.clear()
            self.home_chart.clear()
            self.nav_chart.line(r.equity.nav)
            self.dd_chart.line(r.equity.drawdown, color="#ef797d")
            self.home_chart.line(r.equity.nav)
            self.metric_table.set_frame(
                pd.DataFrame([{"指标": k, "值": str(v)} for k, v in r.metrics.items()])
            )
            self.holdings_table.set_frame(r.holdings)
            self.trades_table.set_frame(r.trades)
            self.rejected_table.set_frame(r.rejected)
            self.month_table.set_frame(
                pd.DataFrame(
                    [{"月份": k, "收益率": v} for k, v in r.metrics.get("monthly", {}).items()]
                )
            )
            self.go(8)
            self.check_risk()

        self.start_task(lambda p, c: self.service.run_backtest(start, end, p, c), done)

    def export_backtest(self):
        if self.service.last_result is None:
            self.error("请先运行回测")
            return
        folder = QFileDialog.getExistingDirectory(self, "选择报告目录")
        if folder:
            self.start_task(
                lambda p, c: export_report(self.service.last_result, folder),
                lambda x: self.task_label.setText(
                    f"HTML / PDF / Excel / PNG报告已导出：{x.parent}"
                ),
            )

    def save_risk(self):
        risk = self.service.config["risk"].copy()
        risk.update({k: s.value() for k, s in self.risk_inputs.items()})
        self.service.save_settings({"risk": risk})
        self.task_label.setText("风险约束已保存，请重新运行回测")

    def check_risk(self):
        r = self.service.last_result
        if r is None:
            self.risk_text.setPlainText("尚无回测持仓。请先完成数据检查与回测。")
            return
        held = (
            r.holdings.loc[r.holdings.date == r.equity.date.iloc[-1]]
            if not r.holdings.empty
            else pd.DataFrame()
        )
        report = risk_report(held, r.equity, self.service.strategy())
        self.risk_text.setPlainText(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n\n" + NOTICE
        )

    def save_settings(self):
        try:
            from domain.backtest import CostConfig

            costs = json.loads(self.cost_edit.toPlainText())
            CostConfig(**costs)
            if any(v < 0 for v in costs.values()) or not 0 < costs["participation"] <= 1:
                raise ValueError("费率须非负，参与率须在(0,1]")
            if self.token_edit.text():
                self.service.store_token(self.token_edit.text())
                self.token_edit.clear()
            self.service.save_settings(
                dict(
                    source=self.source.currentText(),
                    capital=self.capital.value(),
                    strict_data=self.strict.isChecked(),
                    costs=costs,
                )
            )
            self.task_label.setText("设置已保存；规则或费用变更后请重跑回测")
        except Exception as e:
            self.error("设置未保存：" + str(e))

    def diagnostics(self):
        self.start_task(
            lambda p, c: check_environment(self.service.repo.root, True),
            lambda r: self.diag_table.set_frame(pd.DataFrame(r)),
        )

    def open_logs(self):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        path = self.service.repo.root / "logs"
        path.mkdir(exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def start_live(self, once=False):
        if self.live_job:
            return
        # clicked(bool) 的 false 参数与手工once区分。
        once = bool(once)
        mode = self.live_mode.currentIndex()
        watch = set(self.service.last_screen.code) if not self.service.last_screen.empty else set()
        selected_codes = sorted(watch) if mode == 1 else None

        async def work(stop):
            data = await asyncio.to_thread(self.service.repo.load)
            codes = selected_codes if selected_codes is not None else data.stocks.code.tolist()
            if not codes:
                raise ValueError("没有证券代码；请先导入stocks.csv或生成选股清单")
            if not once and not data.dates():
                raise ValueError("请先导入真实交易日历；盘外可使用单次快照")
            async with httpx.AsyncClient(follow_redirects=True) as client:
                c = self.service.config["realtime"]
                sources = [
                    SinaBatchSource(client, c["batch_size"], c["concurrency"]),
                    TencentBatchSource(client, c["batch_size"], c["concurrency"]),
                ]
                collector = Collector(
                    sources,
                    AlertEngine(self.service.alert_rules, c["stale_stop"]),
                    Archive(self.service.repo.root),
                    codes,
                    watch,
                    set(data.dates()),
                    c["interval"],
                )
                await collector.run(self.realtime_batch.emit, stop, once=once)

        job = Worker(lambda p, c: asyncio.run(work(c)))
        self.live_job = job
        self.jobs.append(job)
        job.signals.failed.connect(self.error)
        job.signals.done.connect(lambda: self.live_done(job))
        self.pool.start(job)
        self.source_status.setText("正在连接批量行情源…")

    def live_done(self, job):
        self.live_job = None
        if job in self.jobs:
            self.jobs.remove(job)

    def stop_live(self):
        if self.live_job:
            self.live_job.stop.set()
            self.source_status.setText("监控停止中，正在归档缓冲区…")

    def receive_batch(self, batch):
        self.latest_batch = batch
        for q in batch.get("quotes", []):
            self.live_store[q.code] = q
        self.live_alerts.extend(asdict(a) for a in batch.get("alerts", []))
        self.live_alerts = self.live_alerts[-5000:]
        self.refresh_alerts()
        if batch.get("notice"):
            self.log.appendPlainText(batch["notice"])
        if self.tray and batch.get("alerts"):
            alerts = batch["alerts"]
            self.tray.showMessage(
                "AshareQuant 异动",
                f"{alerts[0].name} · {alerts[0].kind}（共{len(alerts)}条）",
                QSystemTrayIcon.Information,
                5000,
            )

    def refresh_alerts(self):
        if not hasattr(self, "alert_table"):
            return
        f = pd.DataFrame(self.live_alerts[::-1])
        kind = self.live_filter.currentText()
        if not f.empty and kind != "全部异动":
            f = f.loc[f.kind == kind]
        self.alert_table.set_frame(f)

    def tick(self):
        now = datetime.now(SH)
        self.clock.setText(now.strftime("%Y-%m-%d  %H:%M:%S  北京时间"))
        if self.live_store:
            rows = []
            ages = []
            up = down = 0
            amount = 0
            lu = ld = 0
            watch = (
                set(self.service.last_screen.code) if not self.service.last_screen.empty else set()
            )
            for q in self.live_store.values():
                age = q.age(now)
                ages.append(age)
                change = price_change(q.price, q.prev_close)
                up += change > 0
                down += change < 0
                amount += q.amount
                lu += q.limit_up is not None and q.price >= q.limit_up - 0.001
                ld += q.limit_down is not None and q.price <= q.limit_down + 0.001
                rows.append(
                    dict(
                        code=q.code,
                        name=q.name,
                        price=q.price,
                        change=change,
                        amount=q.amount,
                        age_seconds=age,
                        watch="★ 我的清单" if q.code in watch else "",
                        source=q.source,
                    )
                )
            self.quote_table.set_frame(pd.DataFrame(rows))
            age = max(ages)
            batch = self.latest_batch
            self.source_status.setText(
                f"数据延迟 {age:.1f}s | 源 {batch.get('source','—')} | 失败率 {batch.get('failure',0):.1%} | {'过期，已停发信号' if age>15 else '行情有效'} {batch.get('phase','')}"
            )
            self.source_status.setStyleSheet(
                "color:" + ("#ec7279" if age > 15 else "#e7bd68" if age > 5 else "#4cdbb4")
            )
            self.sentiment.setText(
                f"上涨 {up}    下跌 {down}    涨停 {lu}    跌停 {ld}    全市场成交额 {amount/1e8:,.1f} 亿元"
            )
        elif self.latest_batch:
            self.source_status.setText(
                f"{self.latest_batch.get('phase','未获得有效行情')} | 源 {self.latest_batch.get('source','—')} | 失败率 {self.latest_batch.get('failure',1):.0%}"
            )

    def replay(self):
        date = self.screen_date.date().toString("yyyy-MM-dd")

        def done(result):
            rows, notice = result
            self.alert_table.set_frame(pd.DataFrame(rows))
            self.task_label.setText(notice)

        self.start_task(
            lambda p, c: Archive(self.service.repo.root).replay(date, self.service.alert_rules),
            done,
        )

    def wizard(self):
        wizard = QWizard(self)
        wizard.setWindowTitle("AshareQuant · 首次研究配置")
        wizard.resize(640, 450)
        p1 = QWizardPage()
        p1.setTitle("1 / 3 选择数据源")
        wizard_layout = QVBoxLayout(p1)
        wizard_layout.addWidget(
            self.note("建议先用可核验的CSV完成闭环。在线源权限与完整性须自行核对。")
        )
        source = QComboBox()
        source.addItems(["local_csv", "eastmoney", "tushare"])
        wizard_layout.addWidget(source)
        wizard.addPage(p1)
        p2 = QWizardPage()
        p2.setTitle("2 / 3 研究资金与交易成本")
        wizard_layout = QFormLayout(p2)
        capital = spin(self.service.config["capital"], 10000, 1e10, 0)
        wizard_layout.addRow("研究资金（元）", capital)
        wizard_layout.addRow(QLabel("默认佣金万2.5、最低5元；卖出印花税按历史生效日期。"))
        wizard.addPage(p2)
        p3 = QWizardPage()
        p3.setTitle("3 / 3 股票池与风险边界")
        wizard_layout = QVBoxLayout(p3)
        pool = QComboBox()
        pool.addItems(["全A", "沪深300", "中证500", "中证1000", "中证2000"])
        wizard_layout.addWidget(pool)
        wizard_layout.addWidget(self.note(NOTICE))
        wizard.addPage(p3)
        if wizard.exec():
            self.source.setCurrentText(source.currentText())
            self.capital.setValue(capital.value())
            self.universe.setCurrentText(pool.currentText())
            self.service.save_settings(
                {
                    "source": source.currentText(),
                    "capital": capital.value(),
                    "universe": pool.currentText(),
                    "onboarded": True,
                }
            )

    def closeEvent(self, event):
        self.closing = True
        for job in self.jobs:
            job.stop.set()
        if self.jobs:
            self.task_label.setText("正在保存并停止后台任务…")
            event.ignore()
            QTimer.singleShot(300, self.close)
            return
        self.settings.setValue("geometry", self.saveGeometry())
        for i, table in enumerate(self.tables):
            self.settings.setValue(f"table/{i}", table.horizontalHeader().saveState())
        if self.tray:
            self.tray.hide()
        self.timer.stop()
        event.accept()
