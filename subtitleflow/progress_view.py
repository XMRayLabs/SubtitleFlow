"""表格每行的进度：把后台事件折算成文字、比例和颜色，再由 ProgressDelegate 画成“文字 + 细进度条”。

RowProgress 不依赖 Qt，便于测试；ProgressDelegate 只读单元格里的 (比例, 颜色)，可以挂到任何表格的列上。
"""
from dataclasses import dataclass, field
import time

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

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
    first: int = -1               # 当前批在字幕里的起止位置（0 起）与第一句的时间、原文，给“正在翻译哪里”用
    last: int = 0
    at: int = 0
    preview: str = ""
    notice: str = ""            # 重试/拆批说明，要撑过紧随其后的新请求，直到这批有结果或换下一批
    kept: list = field(default_factory=list)   # 始终翻译失败、保留原文的条目位置（0 起）

    def start(self):
        self.__init__(state="running")

    def progress(self, stage, done, total):
        if stage != self.stage:
            self.batch = self.batches = 0
        if done != self.done and self.notice:
            self.notice = ""
            if self.state == "retry":
                self.state = "running"
        self.stage, self.done, self.total = stage, done, total

    def finish(self, state, note=""):
        self.state, self.note, self.started = state, note, None

    def apply(self, event, now=None):
        now = time.monotonic() if now is None else now
        kind = event.get("type")
        if kind == "batch":
            self.batch, self.batches = event["batch"], event["batches"]
            self.first, self.last = event.get("first", -1), event.get("last", -1)
            self.at, self.preview = event.get("at", 0), event.get("preview", "")
            self.state, self.note, self.attempt, self.notice = "running", "", 1, ""
        elif kind == "request":
            self.attempt, self.started, self.note = event["attempt"], now, ""
            self.state = "retry" if self.attempt > 1 or self.notice else "running"
        elif kind == "answer":
            self.started = None
        elif kind == "retry":
            self.state, self.started = "retry", None
            self.note = f"第 {event['attempt']} 次重试：{event['reason']}（{event['delay']:g}s 后）"
        elif kind == "invalid":
            self.state, self.started = "retry", None
            self.note = self.notice = f"译文格式错误，重试中：{event['message']}"
        elif kind == "split":
            self.started = None
            why = {"invalid": "多次格式错误", "timeout": "响应超时"}.get(event.get("reason"), "输出过长")
            self.note = self.notice = f"{why}，拆分为 " + "+".join(str(n) for n in event["sizes"])
        elif kind == "kept":
            self.kept.append(event["position"])

    def stamp(self):
        total = self.at // 1000
        return f"{total // 3600:02d}:{total // 60 % 60:02d}:{total % 60:02d}"

    def current(self):
        """“正在处理哪一句”：条目范围 + 本批第一句的时间和原文，用于状态栏和单元格提示。"""
        if not self.batches or self.first < 0:
            return ""
        where = f"第 {self.first + 1}–{self.last + 1} 条（{self.stamp()}）"
        return f"{where}：{self.preview}" if self.preview else where

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
            return " · ".join(filter(None, [self.note or (f"{self.total} 条" if self.total else ""), self.kept_text()]))
        parts = []
        if self.total:
            parts.append(f"{self.stage} {self.done}/{self.total}")
        elif self.stage:
            parts.append(self.stage)
        if self.note or self.notice:          # 重试、拆批等异常说明才值得挤进单元格；批次和计时放进详情
            parts.append(self.note or self.notice)
        if self.kept:
            parts.append(self.kept_text())
        return " · ".join(parts)

    def kept_text(self):
        return f"{len(self.kept)} 条保留原文" if self.kept else ""

    def kept_rows(self):
        if not self.kept:
            return []
        shown = "、".join(str(p + 1) for p in self.kept[:10])
        more = f" 等 {len(self.kept)} 条" if len(self.kept) > 10 else ""
        return [("保留原文", f"第 {shown} 条{more}", "warn")]

    def detail_rows(self, now=None):
        """展开面板的内容：[(标签, 内容, 色调)]。色调对应 DetailPanel.TONES。"""
        if self.state == "idle":
            return [("状态", self.note or "尚未开始", "error" if self.note else "muted")]
        if self.state == "failed":
            return [("进度", f"停在 {self.done}/{self.total} 条", "normal")] * bool(self.total) + [("错误", self.note, "error")]
        if self.state == "cancelled":
            return [("状态", self.note or "已取消", "muted")]
        if self.state == "done":
            rows = [("状态", f"已完成，共 {self.total} 条" if self.total else "已完成", "done")]
            return rows + [("结果", self.note, "normal")] * bool(self.note) + self.kept_rows()
        rows = [(self.stage or "处理", f"已完成 {self.done}/{self.total} 条" if self.total else "处理中", "normal")]
        if self.batches:
            rows.append(("当前批", f"第 {self.batch}/{self.batches} 批" +
                         (f"，第 {self.first + 1}–{self.last + 1} 条" if self.first >= 0 else ""), "normal"))
            if self.preview:
                rows.append(("起始位置", f"{self.stamp()}　{self.preview}", "normal"))
        if self.started is not None:
            seconds = int((time.monotonic() if now is None else now) - self.started)
            rows.append(("请求", f"请求中 {seconds}s" + (f"（第 {self.attempt} 次尝试）" if self.attempt > 1 else ""),
                         "warn" if self.state == "retry" else "accent"))
        if self.note or self.notice:
            rows.append(("提示", self.note or self.notice, "warn"))
        return rows + self.kept_rows()

    def detail(self, now=None):
        return chr(10).join(f"{key}：{value}" for key, value, _ in self.detail_rows(now))


def overall(rows):
    """已结束的文件各算 1，进行中的文件按自身比例算，和转录页的总进度算法一致。"""
    if not rows:
        return 0.0
    finished = sum(r.state in ("done", "failed") for r in rows)
    current = sum(r.fraction() for r in rows if r.state in ("running", "retry"))
    return (finished + current) / len(rows)


def fill_background(painter, option, index):
    """按单元格的 BackgroundRole 铺底色。样式表会忽略条目自带的底色，所以分组交替色由各 delegate 自己画。"""
    brush = index.data(Qt.BackgroundRole)
    if brush is not None and not option.state & QStyle.State_Selected:
        painter.fillRect(option.rect, brush)


class TintDelegate(QStyledItemDelegate):
    """普通文字列（如“状态”）：先铺分组底色，再照常绘制。"""

    def paint(self, painter, option, index):
        fill_background(painter, option, index)
        super().paint(painter, option, index)


class DetailDelegate(QStyledItemDelegate):
    """文件名列：左侧 ▸/▾ 展开箭头，其余照常画文件名。OPEN 记录是否展开；点箭头发 toggled(表格行号)。
    详情本身不画在这一格里，而是由紧跟其后的 DetailPanel 行显示，文件行的文字与高度不变。"""
    OPEN, PANEL = Qt.UserRole + 2, Qt.UserRole + 4     # PANEL 标记详情面板行的占位格
    ARROW = 24
    toggled = Signal(int)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.state &= ~QStyle.State_HasFocus                  # 不画虚线焦点框
        fill_background(painter, opt, index)
        name, opt.text = opt.text, ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)   # 整格背景、分组底色、选中态
        if index.data(self.PANEL):                           # 详情面板行：只要底色，不画箭头和文字
            return
        # 文件名自己画：再调一次 drawControl 会在文件名外面多画一个选中框
        selected = bool(opt.state & QStyle.State_Selected)
        painter.save()
        painter.setFont(opt.font)
        text = QRect(option.rect.left() + self.ARROW + 10, option.rect.top(),
                     option.rect.width() - self.ARROW - 20, option.rect.height())
        painter.setPen(opt.palette.color(QPalette.HighlightedText if selected else QPalette.Text))
        painter.drawText(text, int(Qt.AlignLeft | Qt.AlignVCenter),
                         opt.fontMetrics.elidedText(name, Qt.ElideMiddle, text.width()))
        painter.setPen(QColor("#7a8498"))
        painter.drawText(QRect(option.rect.left() + 8, option.rect.top(), self.ARROW, option.rect.height()),
                         int(Qt.AlignLeft | Qt.AlignVCenter), "▾" if index.data(self.OPEN) else "▸")
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            if not index.data(self.PANEL) and event.position().x() - option.rect.left() < self.ARROW:
                self.toggled.emit(index.row())
                return True
        return super().editorEvent(event, model, option, index)


class DetailPanel(QWidget):
    """展开后出现在文件行下方的深色小卡片：左侧一条状态色细条，左列标签、右列内容，与文件行分开。
    内容由 set_rows 给出 [(标签, 内容, 色调)]；每行单行显示，过长以“…”收尾，行高固定，高度可直接算出。"""
    # 表格给单元格控件四周留了样式表里的 10px 内边距（QTableWidget::item padding）；卡片铺满这片区域，横跨整行
    CELL_PADDING, INSET_LEFT, INSET_RIGHT, GAP_BOTTOM = 10, 0, 0, 2
    PAD_X, PAD_Y, LINE, KEY = 16, 10, 22, 72
    TONES = {"normal": "#e4e8f1", "muted": "#8a93a8", "accent": "#8f9cff", "warn": "#f0b75a",
             "error": "#ff8a96", "done": "#5fd3a8"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.rows, self.stripe = [], IDLE

    def set_rows(self, rows, stripe):
        self.rows, self.stripe = list(rows), stripe
        self.setToolTip(self.text())
        self.update()

    def text(self):
        return chr(10).join(f"{key}：{value}" for key, value, _ in self.rows)

    def height_for(self, width=None):
        return self.card_height() + self.GAP_BOTTOM + 2 * self.CELL_PADDING

    def card_height(self):
        return max(1, len(self.rows)) * self.LINE + 2 * self.PAD_Y

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        card = QRectF(self.INSET_LEFT, 0, self.width() - self.INSET_LEFT - self.INSET_RIGHT, self.card_height())
        path = QPainterPath()
        path.addRoundedRect(card, 8, 8)
        painter.fillPath(path, QColor("#1d2130"))
        painter.save()
        painter.setClipPath(path)                                   # 状态色细条贴着圆角左边
        painter.fillRect(QRectF(card.left(), card.top(), 3, card.height()), QColor(self.stripe))
        painter.restore()
        font = QFont(self.font())
        font.setPixelSize(12)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        left = int(card.left()) + self.PAD_X
        width = int(card.width()) - self.PAD_X * 2 - self.KEY
        for number, (key, value, tone) in enumerate(self.rows):
            top = int(card.top()) + self.PAD_Y + number * self.LINE
            painter.setPen(QColor(self.TONES["muted"]))
            painter.drawText(QRect(left, top, self.KEY, self.LINE), int(Qt.AlignLeft | Qt.AlignVCenter), key)
            painter.setPen(QColor(self.TONES.get(tone, self.TONES["normal"])))
            painter.drawText(QRect(left + self.KEY, top, width, self.LINE), int(Qt.AlignLeft | Qt.AlignVCenter),
                             metrics.elidedText(value, Qt.ElideRight, width))
        painter.end()


class ProgressDelegate(QStyledItemDelegate):
    """单元格上方画文字，下方画一条圆角细进度条。数据放在 ROLE：(比例 0–1, 颜色)。"""
    ROLE = Qt.UserRole + 1

    def paint(self, painter, option, index):
        value = index.data(self.ROLE)
        if not value:
            return super().paint(painter, option, index)
        fraction, color = value
        fill_background(painter, option, index)
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
