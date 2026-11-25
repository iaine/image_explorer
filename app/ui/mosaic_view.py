"""Mosaic view: a thumbnail grid with size, sort, filter and multi-select."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..models.image_model import ImageModel, ImageSortFilterProxy, PathRole
from .thumb_delegate import ThumbDelegate

MIN_THUMB, MAX_THUMB, DEFAULT_THUMB = 64, 256, 160


class MosaicView(QWidget):
    #: Paths chosen by the user for the stack view.
    send_to_stack = Signal(list)

    def __init__(self, model: ImageModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self.proxy = ImageSortFilterProxy(self)
        self.proxy.setSourceModel(model)

        # --- toolbar
        self.filter_edit = QLineEdit(placeholderText="Filter by filename…", clearButtonEnabled=True)
        self.filter_edit.setAcceptDrops(False)  # file drops go to the main window
        self.filter_edit.textChanged.connect(self.proxy.setFilterFixedString)
        self.filter_edit.textChanged.connect(self._update_counts)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(list(ImageSortFilterProxy.SORT_KEYS))
        self.order_btn = QToolButton(checkable=True, text="↑")
        self.order_btn.setToolTip("Toggle ascending / descending")
        self.sort_combo.currentTextChanged.connect(self._apply_sort)
        self.order_btn.toggled.connect(self._apply_sort)

        self.size_slider = QSlider(Qt.Horizontal, minimum=MIN_THUMB, maximum=MAX_THUMB, value=DEFAULT_THUMB)
        self.size_slider.setFixedWidth(140)
        self.size_slider.setToolTip("Thumbnail size")
        self.size_slider.valueChanged.connect(self.set_thumb_size)

        self.select_all_btn = QPushButton("Select all")
        self.send_btn = QPushButton("Send to stack")
        self.send_btn.setMinimumWidth(self.send_btn.fontMetrics().horizontalAdvance("Send to stack (0000)") + 24)
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._send_selected)

        bar = QHBoxLayout()
        bar.addWidget(self.filter_edit, 1)
        bar.addWidget(QLabel("Sort:"))
        bar.addWidget(self.sort_combo)
        bar.addWidget(self.order_btn)
        bar.addSpacing(12)
        bar.addWidget(QLabel("Size:"))
        bar.addWidget(self.size_slider)
        bar.addSpacing(12)
        bar.addWidget(self.select_all_btn)
        bar.addWidget(self.send_btn)

        # --- grid
        self.view = QListView()
        self.view.setModel(self.proxy)
        self.delegate = ThumbDelegate(DEFAULT_THUMB, self.view)
        self.view.setItemDelegate(self.delegate)
        self.view.setMouseTracking(True)  # hover highlight
        self.view.setViewMode(QListView.IconMode)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setMovement(QListView.Static)
        self.view.setLayoutMode(QListView.Batched)  # stays responsive with thousands of items
        self.view.setBatchSize(200)
        self.view.setUniformItemSizes(True)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setSpacing(6)
        self.view.selectionModel().selectionChanged.connect(self._update_counts)
        self.view.doubleClicked.connect(lambda idx: self.send_to_stack.emit([idx.data(PathRole)]))
        self.select_all_btn.clicked.connect(self.view.selectAll)

        self.count_lbl = QLabel("No images loaded")
        self.count_lbl.setStyleSheet("color: palette(mid);")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.count_lbl)

        model.rowsInserted.connect(self._update_counts)
        model.modelReset.connect(self._update_counts)
        self.set_thumb_size(DEFAULT_THUMB)
        self._apply_sort()
        self.skipped = 0

    # ----- public --------------------------------------------------------
    def set_thumb_size(self, px: int) -> None:
        self.delegate.thumb_size = px
        self.view.setGridSize(self.delegate.cell_size())
        self.view.doItemsLayout()

    def selected_paths(self) -> list[str]:
        rows = sorted(self.view.selectionModel().selectedIndexes(), key=lambda i: i.row())
        return [i.data(PathRole) for i in rows]

    def set_skipped(self, n: int) -> None:
        self.skipped = n
        self._update_counts()

    # ----- internals -----------------------------------------------------
    def _apply_sort(self, *_args) -> None:
        descending = self.order_btn.isChecked()
        self.order_btn.setText("↓" if descending else "↑")
        self.proxy.set_sort_key(
            self.sort_combo.currentText(), Qt.DescendingOrder if descending else Qt.AscendingOrder
        )

    def _send_selected(self) -> None:
        paths = self.selected_paths()
        if paths:
            self.send_to_stack.emit(paths)

    def _update_counts(self, *_args) -> None:
        total = self.model.rowCount()
        shown = self.proxy.rowCount()
        selected = len(self.view.selectionModel().selectedIndexes())
        if total == 0:
            text = "No images loaded"
        else:
            text = f"{total} images" if shown == total else f"{shown} of {total} images shown"
            if self.skipped:
                text += f" · {self.skipped} skipped (see log)"
            if selected:
                text += f" · {selected} selected"
        self.count_lbl.setText(text)
        self.send_btn.setEnabled(selected > 0)
        self.send_btn.setText(f"Send to stack ({selected})" if selected else "Send to stack")
