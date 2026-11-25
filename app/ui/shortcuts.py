"""Keyboard shortcut help dialog, built from the live QActions."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


def shortcut_text(action: QAction) -> str:
    return " / ".join(s.toString(QKeySequence.NativeText) for s in action.shortcuts())


class ShortcutsDialog(QDialog):
    def __init__(self, groups: list[tuple[str, list[QAction]]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.resize(520, 640)

        rows = []
        for group, actions in groups:
            rows.append((group, None))
            for a in actions:
                if a.shortcuts():
                    rows.append((a.text().replace("&", "").rstrip("…").strip(), shortcut_text(a)))

        self.table = QTableWidget(len(rows), 2)
        self.table.setHorizontalHeaderLabels(["Action", "Shortcut"])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 8)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for r, (label, keys) in enumerate(rows):
            item = QTableWidgetItem(label)
            if keys is None:  # group heading
                f = item.font()
                f.setBold(True)
                item.setFont(f)
                self.table.setSpan(r, 0, 1, 2)
            self.table.setItem(r, 0, item)
            if keys is not None:
                self.table.setItem(r, 1, QTableWidgetItem(keys))

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(buttons)

    def rows(self) -> list[tuple[str, str]]:
        out = []
        for r in range(self.table.rowCount()):
            keys = self.table.item(r, 1)
            if keys is not None:
                out.append((self.table.item(r, 0).text(), keys.text()))
        return out
