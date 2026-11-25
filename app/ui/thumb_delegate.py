"""Paints a mosaic tile: aspect-correct thumbnail in a square, caption below."""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

PADDING = 8
CAPTION_H = 20


class ThumbDelegate(QStyledItemDelegate):
    def __init__(self, thumb_size: int = 160, parent=None) -> None:
        super().__init__(parent)
        self.thumb_size = thumb_size

    def cell_size(self) -> QSize:
        return QSize(self.thumb_size + 2 * PADDING, self.thumb_size + 2 * PADDING + CAPTION_H)

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        return self.cell_size()

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        pal = option.palette
        cell = option.rect.adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)

        # background / selection
        if selected or hovered:
            colour = pal.color(QPalette.Highlight)
            colour.setAlpha(90 if selected else 35)
            path = QPainterPath()
            path.addRoundedRect(QRectF(cell), 6, 6)
            painter.fillPath(path, colour)

        # thumbnail, fitted into a square and centred
        s = self.thumb_size
        square = QRect(option.rect.x() + PADDING, option.rect.y() + PADDING, s, s)
        pm = index.data(Qt.DecorationRole)
        if isinstance(pm, QPixmap) and not pm.isNull():
            fitted = pm.size().scaled(square.size(), Qt.KeepAspectRatio)
            if pm.width() <= s and pm.height() <= s:
                fitted = pm.size()  # never upscale small images
            target = QRect(0, 0, fitted.width(), fitted.height())
            target.moveCenter(square.center())
            painter.drawPixmap(target, pm)
            if selected:
                pen = painter.pen()
                pen.setColor(pal.color(QPalette.Highlight))
                pen.setWidth(2)
                painter.setPen(pen)
                painter.drawRect(target.adjusted(-1, -1, 0, 0))

        # caption
        text_rect = QRect(cell.x() + 4, square.bottom() + 4, cell.width() - 8, CAPTION_H)
        name = index.data(Qt.DisplayRole) or ""
        elided = option.fontMetrics.elidedText(name, Qt.ElideMiddle, text_rect.width())
        painter.setPen(pal.color(QPalette.Text))
        painter.drawText(text_rect, Qt.AlignHCenter | Qt.AlignVCenter, elided)
        painter.restore()
