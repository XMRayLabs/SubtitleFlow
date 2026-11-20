"""表格每行的进度：把后台事件折算成文字、比例和颜色，再由 ProgressDelegate 画成“文字 + 细进度条”。

RowProgress 不依赖 Qt，便于测试；ProgressDelegate 只读单元格里的 (比例, 颜色)，可以挂到任何表格的列上。
"""
from dataclasses import dataclass
import time

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

RUNNING, RETRY, FAILED, DONE = "#5264e8", "#b7791f", "#d14d61", "#16856b"
IDLE = "#c3cad8"
TRACK = "#e8ecf5"


@dataclass
class RowProgress:
    state: str = "idle"          # idle / running / retry / failed / done / cancelled
    stage: str = ""              # “翻译”“AI 断句”
    done: int = 0
    total: int = 0
    batch: int = 0
    batches: int = 0
    note: str = ""               # 重试、拆批等临时说明；失败时是错误信息
    started: float | None = None  # 当前请求开始的 monotonic 时间，用来跳秒
    attempt: int = 1

    def start(self):
        self.__init__(state="running")

    def progress(self, stage, done, total):
        if stage != self.stage:
            self.batch = self.batches = 0
        self.stage, self.done, self.total = stage, done, total

    def finish(self, state, note=""):
        self.state, self.note, self.started = state, note, None

    def apply(self, event, now=None):
        now = time.monotonic() if now is None else now
        kind = event.get("type")
        if kind == "batch":
            self.batch, self.batches = event["batch"], event["batches"]
            self.state, self.note, self.attempt = "running", "", 1
        elif kind == "request":
            self.attempt, self.started, self.note = event["attempt"], now, ""
            self.state = "retry" if self.attempt > 1 else "running"
        elif kind == "answer":
            self.started = None
        elif kind == "retry":
            self.state, self.started = "retry", None
            self.note = f"第 {event['attempt']} 次重试：{event['reason']}（{event['delay']:g}s 后）"
        elif kind == "invalid":
            self.state, self.started = "retry", None
            self.note = f"译文格式错误，重试中：{event['message']}"
        elif kind == "split":
            self.started = None
            self.note = "输出过长，拆分为 " + "+".join(str(n) for n in event["sizes"])

    def fraction(self):
        if self.state == "done":
            return 1.0
        return min(1.0, self.done / self.total) if self.total else 0.0

    def color(self):
        return {"running": RUNNING, "retry": RETRY, "failed": FAILED, "done": DONE}.get(self.state, IDLE)

    def text(self, now=None):
        if self.state in ("idle", "failed", "cancelled"):
            return self.note
        if self.state == "done":
            return f"{self.total} 条" if self.total else ""
        parts = []
        if self.total:
            parts.append(f"{self.stage} {self.done}/{self.total}")
        elif self.stage:
            parts.append(self.stage)
        if self.batches:
            parts.append(f"第 {self.batch}/{self.batches} 批")
        if self.note:
            parts.append(self.note)
        elif self.started is not None:
            seconds = int((time.monotonic() if now is None else now) - self.started)
            parts.append(f"请求中 {seconds}s" + (f"（第 {self.attempt} 次尝试）" if self.attempt > 1 else ""))
        return " · ".join(parts)


def overall(rows):
    """已结束的文件各算 1，进行中的文件按自身比例算，和转录页的总进度算法一致。"""
    if not rows:
        return 0.0
    finished = sum(r.state in ("done", "failed") for r in rows)
    current = sum(r.fraction() for r in rows if r.state in ("running", "retry"))
    return (finished + current) / len(rows)


class ProgressDelegate(QStyledItemDelegate):
    """单元格上方画文字，下方画一条圆角细进度条。数据放在 ROLE：(比例 0–1, 颜色)。"""
    ROLE = Qt.UserRole + 1

    def paint(self, painter, option, index):
        value = index.data(self.ROLE)
        if not value:
            return super().paint(painter, option, index)
        fraction, color = value
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text, opt.text = opt.text, ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)  # 背景、交替行色、选中态

        area = option.rect.adjusted(8, 5, -14, -9)
        bar = QRectF(area.left(), area.bottom() - 4, area.width(), 5)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(TRACK))
        painter.drawRoundedRect(bar, 2.5, 2.5)
        if fraction > 0:
            filled = QRectF(bar)
            filled.setWidth(max(bar.height(), bar.width() * fraction))
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(filled, 2.5, 2.5)
        if text:
            label = QRect(area.left(), area.top(), area.width(), area.height() - 8)
            painter.setPen(QColor(FAILED if color == FAILED else "#5b6578"))
            painter.setFont(opt.font)
            elided = opt.fontMetrics.elidedText(text, Qt.ElideRight, label.width())
            painter.drawText(label, int(Qt.AlignLeft | Qt.AlignVCenter), elided)
        painter.restore()
