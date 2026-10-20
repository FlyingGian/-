from threading import Event
import numpy as np
import pandas as pd
from PySide6.QtCore import (
    Qt,
    QAbstractTableModel,
    QModelIndex,
    QObject,
    Signal,
    QRunnable,
    QSortFilterProxyModel,
)
from PySide6.QtGui import QKeySequence, QAction, QColor, QPainter, QPicture
from PySide6.QtWidgets import (
    QApplication,
    QTableView,
    QAbstractItemView,
    QMenu,
    QFileDialog,
    QHeaderView,
)
import pyqtgraph as pg


class DataFrameModel(QAbstractTableModel):
    def __init__(self, frame=None, parent=None):
        super().__init__(parent)
        self.frame = frame if frame is not None else pd.DataFrame()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.frame)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.frame.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        value = self.frame.iat[index.row(), index.column()]
        if role == Qt.DisplayRole:
            if isinstance(value, (list, dict, tuple)):
                return str(value)
            if pd.isna(value):
                return "—"
            if isinstance(value, (float, np.floating)):
                return f"{value:,.4f}" if abs(value) < 10 else f"{value:,.2f}"
            return str(value)
        if role == Qt.UserRole:
            if isinstance(value, (float, int, np.number)):
                return float(value) if pd.notna(value) else float("-inf")
            return str(value)
        if role == Qt.TextAlignmentRole:
            return (
                int(Qt.AlignRight | Qt.AlignVCenter)
                if isinstance(value, (float, int, np.number))
                else int(Qt.AlignLeft | Qt.AlignVCenter)
            )
        if role == Qt.ForegroundRole and self.frame.columns[index.column()] in (
            "change",
            "drawdown",
            "realized_pnl",
        ):
            if isinstance(value, (int, float, np.number)):
                return QColor("#ef797d" if value > 0 else "#43c6ac")
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return (
                str(self.frame.columns[section])
                if orientation == Qt.Horizontal
                else str(section + 1)
            )
        return None

    def set_frame(self, frame):
        same = list(frame.columns) == list(self.frame.columns) and len(frame) == len(self.frame)
        self.frame = frame.reset_index(drop=True)
        if same and not frame.empty:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(frame) - 1, len(frame.columns) - 1)
            )
        else:
            self.beginResetModel()
            self.endResetModel()


class Table(QTableView):
    def __init__(self, frame=None):
        super().__init__()
        self.source = DataFrameModel(frame, self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.source)
        self.proxy.setSortRole(Qt.UserRole)
        self.proxy.setFilterKeyColumn(-1)
        self.setModel(self.proxy)
        self.setSortingEnabled(True)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(34)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setDefaultSectionSize(140)
        self.horizontalHeader().setStretchLastSection(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.menu)
        action = QAction(self)
        action.setShortcut(QKeySequence.Copy)
        action.triggered.connect(self.copy)
        self.addAction(action)

    def set_frame(self, frame):
        self.source.set_frame(frame)

    def copy(self):
        rows = sorted({i.row() for i in self.selectedIndexes()})
        text = "\n".join(
            "\t".join(
                str(self.proxy.data(self.proxy.index(r, c)))
                for c in range(self.proxy.columnCount())
            )
            for r in rows
        )
        QApplication.clipboard().setText(text)

    def menu(self, pos):
        menu = QMenu(self)
        menu.addAction("复制所选行", self.copy)
        menu.addAction("导出CSV", self.export)
        menu.exec(self.viewport().mapToGlobal(pos))

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出", "table.csv", "CSV (*.csv)")
        if path:
            self.source.frame.to_csv(path, index=False, encoding="utf-8-sig")


class WorkerSignals(QObject):
    progress = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str)
    done = Signal()


class Worker(QRunnable):
    def __init__(self, call):
        super().__init__()
        self.call = call
        self.signals = WorkerSignals()
        self.stop = Event()

    def run(self):
        try:
            value = self.call(self.signals.progress.emit, self.stop.is_set)
            self.signals.finished.emit(value)
        except InterruptedError as e:
            self.signals.failed.emit(str(e))
        except Exception as e:
            import logging

            logging.exception("后台任务失败")
            self.signals.failed.emit(f"{type(e).__name__}: {e}")
        finally:
            self.signals.done.emit()


class Chart(pg.PlotWidget):
    def __init__(self, title=""):
        super().__init__(title=title, background="#111c2a")
        self.showGrid(x=True, y=True, alpha=0.12)
        self.setMinimumHeight(180)
        self.getPlotItem().getAxis("left").setTextPen("#8da2bb")
        self.getPlotItem().getAxis("bottom").setTextPen("#8da2bb")

    def line(self, values, name="", color="#3dd6b0"):
        self.plot(
            np.arange(len(values)),
            np.asarray(values, dtype=float),
            pen=pg.mkPen(color, width=2),
            name=name,
        )


class Candles(pg.GraphicsObject):
    def __init__(self, frame):
        super().__init__()
        self.picture = QPicture()
        p = QPainter(self.picture)
        for i, row in frame.reset_index(drop=True).iterrows():
            color = QColor("#ef797d" if row.close >= row.open else "#43c6ac")
            p.setPen(color)
            p.setBrush(color)
            from PySide6.QtCore import QPointF, QRectF

            p.drawLine(QPointF(i, row.low), QPointF(i, row.high))
            p.drawRect(
                QRectF(i - 0.3, min(row.open, row.close), 0.6, max(abs(row.close - row.open), 0.01))
            )
        p.end()

    def paint(self, painter, *args):
        painter.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        from PySide6.QtCore import QRectF

        return QRectF(self.picture.boundingRect())
