"""Overlay stack view: layers composited with per-layer opacity.

Layer list convention (like image editors): row 0 is the TOP of the stack.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..jobs.load_layers import LayerImage

MAX_LAYERS = 20
TOP_DEFAULT_OPACITY = 50  # percent, for every layer above the bottom one
BOTTOM_DEFAULT_OPACITY = 100


@dataclass
class Layer:
    path: str
    item: QGraphicsPixmapItem
    width: int  # original pixels
    height: int
    opacity: int = TOP_DEFAULT_OPACITY  # percent
    visible: bool = True

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


# --------------------------------------------------------------------------
class StackCanvas(QGraphicsView):
    """Zoom with the mouse wheel, pan by dragging; checkerboard background."""

    zoom_changed = Signal(float)
    MIN_ZOOM, MAX_ZOOM = 0.02, 32.0

    def __init__(self, scene: QGraphicsScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self._checker = self._make_checker()

    @staticmethod
    def _make_checker(cell: int = 12) -> QBrush:
        pm = QPixmap(cell * 2, cell * 2)
        pm.fill(QColor("#e6e6e6"))
        p = QPainter(pm)
        p.fillRect(0, 0, cell, cell, QColor("#c8c8c8"))
        p.fillRect(cell, cell, cell, cell, QColor("#c8c8c8"))
        p.end()
        return QBrush(pm)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        painter.fillRect(rect, self.palette().window())
        # checkerboard only behind the images, in screen space so it doesn't scale
        content = self.scene().itemsBoundingRect()
        if content.isEmpty():
            return
        painter.save()
        painter.resetTransform()
        target = self.mapFromScene(content).boundingRect()
        painter.fillRect(target, self._checker)
        painter.restore()

    def zoom(self) -> float:
        return self.transform().m11()

    def set_zoom(self, z: float) -> None:
        z = max(self.MIN_ZOOM, min(self.MAX_ZOOM, z))
        factor = z / self.zoom()
        self.scale(factor, factor)
        self.zoom_changed.emit(self.zoom())

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        steps = event.angleDelta().y() / 120
        if steps:
            self.set_zoom(self.zoom() * (1.15 ** steps))

    def fit(self) -> None:
        rect = self.scene().itemsBoundingRect()
        if not rect.isEmpty():
            pad = max(rect.width(), rect.height()) * 0.02
            self.fitInView(rect.adjusted(-pad, -pad, pad, pad), Qt.KeepAspectRatio)
            self.zoom_changed.emit(self.zoom())

    def actual_size(self) -> None:
        self.set_zoom(1.0)


# --------------------------------------------------------------------------
class StackView(QWidget):
    #: Paths that need loading; the main window runs a LoadLayersJob.
    load_requested = Signal(list)
    #: Informational messages for the log.
    message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.layers: list[Layer] = []  # index 0 = top
        self.solo_path: str | None = None
        self._insert_at = 0  # where the next arriving layer goes
        self._pending: set[str] = set()
        self._fresh_batch = False
        self._syncing = False

        self.scene = QGraphicsScene(self)
        self.canvas = StackCanvas(self.scene)
        self.canvas.zoom_changed.connect(lambda z: self.zoom_lbl.setText(f"{z * 100:.0f}%"))

        # ---------- layer panel
        self.layer_list = QListWidget()
        self.layer_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.layer_list.setDefaultDropAction(Qt.MoveAction)
        self.layer_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.layer_list.model().rowsMoved.connect(self._on_rows_moved)
        self.layer_list.itemChanged.connect(self._on_item_changed)
        self.layer_list.currentRowChanged.connect(self._on_current_changed)
        self.layer_list.setToolTip("Top of the list = top of the stack. Drag to reorder.")

        self.up_btn = QPushButton("▲ Up")
        self.down_btn = QPushButton("▼ Down")
        self.remove_btn = QPushButton("Remove")
        self.up_btn.clicked.connect(lambda: self.move_current(-1))
        self.down_btn.clicked.connect(lambda: self.move_current(+1))
        self.remove_btn.clicked.connect(self.remove_current)
        order_row = QHBoxLayout()
        for b in (self.up_btn, self.down_btn, self.remove_btn):
            order_row.addWidget(b)

        # selected-layer controls
        self.opacity_slider = QSlider(Qt.Horizontal, minimum=0, maximum=100)
        self.opacity_spin = QSpinBox(minimum=0, maximum=100, suffix=" %")
        self.opacity_slider.valueChanged.connect(self.opacity_spin.setValue)
        self.opacity_spin.valueChanged.connect(self.opacity_slider.setValue)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        op_row = QHBoxLayout()
        op_row.addWidget(self.opacity_slider, 1)
        op_row.addWidget(self.opacity_spin)

        self.solo_btn = QPushButton("Solo", checkable=True)
        self.solo_btn.setToolTip("Show only this layer at 100% (doesn't change its settings)")
        self.solo_btn.toggled.connect(self._on_solo_toggled)
        self.info_lbl = QLabel("—")
        self.info_lbl.setStyleSheet("color: palette(mid);")

        self.selected_box = QGroupBox("Selected layer")
        sel_form = QFormLayout(self.selected_box)
        sel_form.addRow("Opacity:", op_row)
        sel_form.addRow("", self.solo_btn)
        sel_form.addRow("Size:", self.info_lbl)

        # stack-wide controls
        self.align_combo = QComboBox()
        self.align_combo.addItems(["Centre", "Top-left"])
        self.align_combo.currentIndexChanged.connect(lambda _: self._layout_items())
        self.reset_btn = QPushButton("Reset opacities")
        self.reset_btn.setToolTip(f"Bottom layer {BOTTOM_DEFAULT_OPACITY}%, others {TOP_DEFAULT_OPACITY}%")
        self.reset_btn.clicked.connect(self.reset_opacities)
        self.clear_btn = QPushButton("Clear stack")
        self.clear_btn.clicked.connect(self.clear)

        stack_box = QGroupBox("Stack")
        stack_form = QFormLayout(stack_box)
        stack_form.addRow("Align:", self.align_combo)
        btns = QHBoxLayout()
        btns.addWidget(self.reset_btn)
        btns.addWidget(self.clear_btn)
        stack_form.addRow(btns)

        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(QLabel("Layers (top → bottom)"))
        pl.addWidget(self.layer_list, 1)
        pl.addLayout(order_row)
        pl.addWidget(self.selected_box)
        pl.addWidget(stack_box)
        panel.setMinimumWidth(260)

        # ---------- canvas toolbar
        self.fit_btn = QPushButton("Fit")
        self.fit_btn.clicked.connect(self.canvas.fit)
        self.actual_btn = QPushButton("100%")
        self.actual_btn.clicked.connect(self.canvas.actual_size)
        self.zoom_lbl = QLabel("100%")
        self.empty_lbl = QLabel(
            "Stack is empty — select images in the mosaic and click “Send to stack”, "
            "or double-click a thumbnail."
        )
        self.empty_lbl.setStyleSheet("color: palette(mid);")
        tb = QHBoxLayout()
        tb.addWidget(self.empty_lbl, 1)
        tb.addWidget(QLabel("Zoom:"))
        tb.addWidget(self.zoom_lbl)
        tb.addWidget(self.fit_btn)
        tb.addWidget(self.actual_btn)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addLayout(tb)
        ll.addWidget(self.canvas, 1)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(panel)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([800, 300])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.canvas.setAcceptDrops(False)  # let file drops reach the main window
        self._build_actions()
        self._refresh_controls()

    # ======================================================================
    # keyboard shortcuts (active while the stack view or its children have focus)
    # ======================================================================
    def _build_actions(self) -> None:
        def act(text, keys, slot):
            a = QAction(text, self)
            a.setShortcuts([QKeySequence(k) for k in keys])
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            a.triggered.connect(slot)
            self.addAction(a)
            return a

        self.shortcut_actions = [
            act("Move layer up", ["Ctrl+Up"], lambda: self.move_current(-1)),
            act("Move layer down", ["Ctrl+Down"], lambda: self.move_current(+1)),
            act("Remove layer", ["Delete", "Backspace"], self.remove_current),
            act("Show / hide layer", ["H"], self.toggle_current_visibility),
            act("Solo layer", ["S"], lambda: self.solo_btn.isEnabled() and self.solo_btn.toggle()),
            act("Opacity −10%", ["["], lambda: self.nudge_opacity(-10)),
            act("Opacity +10%", ["]"], lambda: self.nudge_opacity(+10)),
            act("Zoom in", ["Ctrl+=", "Ctrl++"], lambda: self.canvas.set_zoom(self.canvas.zoom() * 1.25)),
            act("Zoom out", ["Ctrl+-"], lambda: self.canvas.set_zoom(self.canvas.zoom() / 1.25)),
            act("Fit to view", ["Ctrl+0"], self.canvas.fit),
            act("Actual size (100%)", ["Ctrl+Shift+0"], self.canvas.actual_size),
        ]

    def toggle_current_visibility(self) -> None:
        row = self.layer_list.currentRow()
        if 0 <= row < len(self.layers):
            self.set_visible(row, not self.layers[row].visible)

    def nudge_opacity(self, delta: int) -> None:
        row = self.layer_list.currentRow()
        if 0 <= row < len(self.layers) and self.solo_path is None:
            self.set_opacity(row, max(0, min(100, self.layers[row].opacity + delta)))

    # ======================================================================
    # adding layers
    # ======================================================================
    def add_paths(self, paths: list[str]) -> None:
        """Queue paths for loading. The first path ends up on top."""
        existing = {l.path for l in self.layers} | self._pending
        new = [p for p in dict.fromkeys(paths) if p not in existing]
        dupes = len(paths) - len(new)
        room = MAX_LAYERS - len(self.layers) - len(self._pending)
        if len(new) > room:
            self.message.emit(
                f"Stack limit is {MAX_LAYERS} layers; {len(new) - max(room, 0)} image(s) not added."
            )
            new = new[: max(room, 0)]
        if dupes:
            self.message.emit(f"{dupes} image(s) already in the stack.")
        if not new:
            return
        self._fresh_batch = not self.layers and not self._pending
        self._insert_at = 0
        self._pending.update(new)
        self.load_requested.emit(new)

    def add_layer_image(self, li: LayerImage) -> None:
        """Slot for LoadLayersJob.item — inserts below the previous arrival."""
        self._pending.discard(li.path)
        item = QGraphicsPixmapItem(QPixmap.fromImage(li.image))
        item.setTransformationMode(Qt.SmoothTransformation)
        item.setScale(li.scale)  # show at original pixel size
        self.scene.addItem(item)
        layer = Layer(li.path, item, li.orig_width, li.orig_height)
        row = min(self._insert_at, len(self.layers))
        self.layers.insert(row, layer)
        self._insert_at = row + 1
        first = len(self.layers) == 1
        self._rebuild_list(select_row=row)
        self._apply_all()
        self._layout_items()
        if first:
            self.canvas.fit()

    def batch_finished(self) -> None:
        """Call when a load job ends; applies defaults to a brand-new stack."""
        self._pending.clear()
        if self._fresh_batch and self.layers:
            self.reset_opacities()
            self.canvas.fit()
        self._fresh_batch = False

    # ======================================================================
    # editing
    # ======================================================================
    def reset_opacities(self) -> None:
        for i, layer in enumerate(self.layers):
            is_bottom = i == len(self.layers) - 1
            layer.opacity = BOTTOM_DEFAULT_OPACITY if is_bottom else TOP_DEFAULT_OPACITY
        self._apply_all()
        self._rebuild_list(select_row=self.layer_list.currentRow())

    def set_opacity(self, row: int, percent: int) -> None:
        self.layers[row].opacity = int(percent)
        self._apply_all()
        self._update_item_text(row)
        if row == self.layer_list.currentRow():
            self._sync_controls()

    def set_visible(self, row: int, visible: bool) -> None:
        self.layers[row].visible = visible
        self._apply_all()
        self._syncing = True
        self.layer_list.item(row).setCheckState(Qt.Checked if visible else Qt.Unchecked)
        self._syncing = False

    def set_solo(self, path: str | None) -> None:
        self.solo_path = path
        self._apply_all()
        self._sync_controls()

    def move(self, row: int, delta: int) -> None:
        new = row + delta
        if not (0 <= row < len(self.layers) and 0 <= new < len(self.layers)):
            return
        self.layers.insert(new, self.layers.pop(row))
        self._rebuild_list(select_row=new)
        self._apply_all()

    def move_current(self, delta: int) -> None:
        self.move(self.layer_list.currentRow(), delta)

    def remove(self, row: int) -> None:
        if not 0 <= row < len(self.layers):
            return
        layer = self.layers.pop(row)
        self.scene.removeItem(layer.item)
        if self.solo_path == layer.path:
            self.solo_path = None
        self._rebuild_list(select_row=min(row, len(self.layers) - 1))
        self._apply_all()
        self._layout_items()

    def remove_current(self) -> None:
        self.remove(self.layer_list.currentRow())

    def clear(self) -> None:
        for layer in self.layers:
            self.scene.removeItem(layer.item)
        self.layers.clear()
        self.solo_path = None
        self._rebuild_list()
        self._layout_items()

    def paths(self) -> list[str]:
        return [l.path for l in self.layers]

    # ======================================================================
    # rendering
    # ======================================================================
    def _apply_all(self) -> None:
        """Push z-order, visibility and opacity to the graphics items."""
        n = len(self.layers)
        for i, layer in enumerate(self.layers):
            layer.item.setZValue(n - i)  # row 0 = highest z
            if self.solo_path is not None:
                solo = layer.path == self.solo_path
                layer.item.setVisible(solo)
                layer.item.setOpacity(1.0)
            else:
                layer.item.setVisible(layer.visible)
                layer.item.setOpacity(layer.opacity / 100)
        self._refresh_controls()

    def _layout_items(self) -> None:
        centre = self.align_combo.currentIndex() == 0
        for layer in self.layers:
            if centre:
                layer.item.setPos(QPointF(-layer.width / 2, -layer.height / 2))
            else:
                layer.item.setPos(QPointF(0, 0))
        rect = self.scene.itemsBoundingRect()
        margin = max(rect.width(), rect.height()) * 0.25
        self.scene.setSceneRect(rect.adjusted(-margin, -margin, margin, margin))
        self.canvas.viewport().update()

    # ======================================================================
    # list <-> model sync
    # ======================================================================
    def _item_text(self, layer: Layer) -> str:
        return f"{layer.name}   {layer.opacity}%"

    def _rebuild_list(self, select_row: int = -1) -> None:
        self._syncing = True
        self.layer_list.clear()
        for layer in self.layers:
            it = QListWidgetItem(self._item_text(layer))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            it.setFlags(it.flags() & ~Qt.ItemIsDropEnabled)  # drop between rows only
            it.setCheckState(Qt.Checked if layer.visible else Qt.Unchecked)
            it.setData(Qt.UserRole, layer.path)
            it.setToolTip(f"{layer.path}\n{layer.width} × {layer.height} px\nTick = visible")
            self.layer_list.addItem(it)
        self._syncing = False
        if self.layers:
            self.layer_list.setCurrentRow(max(0, min(select_row, len(self.layers) - 1)))
        self._sync_controls()

    def _update_item_text(self, row: int) -> None:
        self._syncing = True
        self.layer_list.item(row).setText(self._item_text(self.layers[row]))
        self._syncing = False

    def _on_rows_moved(self, *_args) -> None:
        """Drag-and-drop reorder: rebuild self.layers from the list order."""
        by_path = {l.path: l for l in self.layers}
        order = [self.layer_list.item(i).data(Qt.UserRole) for i in range(self.layer_list.count())]
        self.layers = [by_path[p] for p in order]
        self._apply_all()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._syncing:
            return
        row = self.layer_list.row(item)
        if 0 <= row < len(self.layers):
            visible = item.checkState() == Qt.Checked
            if visible != self.layers[row].visible:
                self.layers[row].visible = visible
                self._apply_all()

    def _on_current_changed(self, _row: int) -> None:
        self._sync_controls()

    def _on_opacity_changed(self, value: int) -> None:
        if self._syncing:
            return
        row = self.layer_list.currentRow()
        if 0 <= row < len(self.layers):
            self.set_opacity(row, value)

    def _on_solo_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        row = self.layer_list.currentRow()
        if checked and 0 <= row < len(self.layers):
            self.set_solo(self.layers[row].path)
        else:
            self.set_solo(None)

    def _sync_controls(self) -> None:
        row = self.layer_list.currentRow()
        has = 0 <= row < len(self.layers)
        self._syncing = True
        if has:
            layer = self.layers[row]
            self.opacity_slider.setValue(layer.opacity)
            self.opacity_spin.setValue(layer.opacity)
            self.solo_btn.setChecked(self.solo_path == layer.path)
            self.info_lbl.setText(f"{layer.width} × {layer.height} px")
        else:
            self.solo_btn.setChecked(False)
            self.info_lbl.setText("—")
        self._syncing = False
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        row = self.layer_list.currentRow()
        has = 0 <= row < len(self.layers)
        n = len(self.layers)
        self.selected_box.setEnabled(has)
        # opacity is meaningless while soloing; make that obvious
        soloing = self.solo_path is not None
        self.opacity_slider.setEnabled(has and not soloing)
        self.opacity_spin.setEnabled(has and not soloing)
        self.up_btn.setEnabled(has and row > 0)
        self.down_btn.setEnabled(has and row < n - 1)
        self.remove_btn.setEnabled(has)
        for w in (self.reset_btn, self.clear_btn, self.fit_btn, self.actual_btn):
            w.setEnabled(n > 0)
        if n == 0:
            self.empty_lbl.setText(
                "Stack is empty — select images in the mosaic and click “Send to stack”, "
                "or double-click a thumbnail."
            )
        else:
            solo = f" · soloing {os.path.basename(self.solo_path)}" if soloing else ""
            self.empty_lbl.setText(f"{n} layer(s){solo}")
