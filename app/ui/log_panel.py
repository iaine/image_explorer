"""Collapsible log panel for job messages."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QDockWidget, QPlainTextEdit, QWidget

LEVEL_COLOURS = {
    "info": None,
    "success": QColor("#2e7d32"),
    "failure": QColor("#b26a00"),
    "error": QColor("#c62828"),
}


class LogPanel(QDockWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Log", parent)
        self.setObjectName("LogPanel")
        self.view = QPlainTextEdit(readOnly=True)
        self.view.setMaximumBlockCount(5000)
        self.setWidget(self.view)

    def append(self, text: str, level: str = "info") -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        fmt = QTextCharFormat()
        colour = LEVEL_COLOURS.get(level)
        if colour is not None:
            fmt.setForeground(colour)
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.End)
        if not self.view.document().isEmpty():
            cursor.insertBlock()
        cursor.insertText(f"[{stamp}] {text}", fmt)
        self.view.setTextCursor(cursor)
        self.view.ensureCursorVisible()

    def lines(self) -> list[str]:
        return self.view.toPlainText().splitlines()
