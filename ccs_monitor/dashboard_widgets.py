"""Presentation helpers for the command dashboard; no device or map state."""
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QLabel, QLayout, QSizePolicy


class ElidingLabel(QLabel):
    """Keep the full value available to callers and in the tooltip."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setToolTip(text)

    def setText(self, text):  # noqa: N802
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.contentsRect().width())
        painter.drawText(self.contentsRect(), self.alignment() | Qt.AlignmentFlag.AlignVCenter, text)


class BalancedDashboardHeader(QFrame):
    """Center the title on the entire bar, independent of side content widths."""

    def set_sections(self, left, title, right):
        self.sections = (left, title, right)
        for widget in self.sections:
            widget.setParent(self)
        self._arrange()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._arrange()

    def _arrange(self):
        if not hasattr(self, "sections"):
            return
        left, title, right = self.sections
        lh, th, rh = (widget.sizeHint() for widget in self.sections)
        side = max(lh.width(), rh.width())
        wide = self.width() >= th.width() + side * 2 + 48
        height = max(lh.height(), th.height(), rh.height()) + 16 if wide else th.height() + max(lh.height(), rh.height()) + 20
        if self.height() != height:
            self.setFixedHeight(height)
        title.setGeometry((self.width() - th.width()) // 2, (height - th.height()) // 2 if wide else 6, th.width(), th.height())
        y = (height - lh.height()) // 2 if wide else height - lh.height() - 6
        left.setGeometry(12, y, lh.width(), lh.height())
        y = (height - rh.height()) // 2 if wide else height - rh.height() - 6
        right.setGeometry(self.width() - rh.width() - 12, y, rh.width(), rh.height())


class WrappingLayout(QLayout):
    """Wrap intact groups of existing controls without changing their state."""

    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self._items = []
        self.align_last_right = False
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, width):  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect, measure):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0
        visible = [item for item in self._items if not item.isEmpty()]
        for item in visible:
            if item.isEmpty():
                continue
            size = item.sizeHint()
            if row_height and x + size.width() > area.right() + 1:
                x = area.x()
                y += row_height + self.spacing()
                row_height = 0
            if self.align_last_right and item is visible[-1]:
                x = max(x, area.right() + 1 - size.width())
            if not measure:
                item.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + self.spacing()
            row_height = max(row_height, size.height())
        return y + row_height - rect.y() + margins.bottom()
