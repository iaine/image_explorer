"""Explore tab: directory picker, background loading, Mosaic / Stack views."""
from __future__ import annotations


from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models.image_model import ImageModel
from .mosaic_view import MosaicView
from .stack_view import StackView


class ExploreTab(QWidget):
    #: (directory, recursive) — the main window starts the LoadImagesJob.
    load_requested = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = ImageModel(self)
        #: Where "Browse…" starts when no folder is set (restored from settings).
        self.last_browse_dir = ""

        # --- directory row
        self.dir_edit = QLineEdit(placeholderText="Choose a folder of images…", readOnly=True)
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self._browse)
        self.recursive_chk = QCheckBox("Include subfolders")
        self.load_btn = QPushButton("Load images")
        self.load_btn.setEnabled(False)
        self.load_btn.clicked.connect(self.request_load)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Folder:"))
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(self.browse_btn)
        dir_row.addWidget(self.recursive_chk)
        dir_row.addWidget(self.load_btn)

        # --- view switcher
        self.mosaic_radio = QRadioButton("Mosaic", checked=True)
        self.stack_radio = QRadioButton("Stack")
        self.view_group = QButtonGroup(self)
        self.view_group.addButton(self.mosaic_radio, 0)
        self.view_group.addButton(self.stack_radio, 1)
        switch_row = QHBoxLayout()
        switch_row.addWidget(QLabel("View:"))
        switch_row.addWidget(self.mosaic_radio)
        switch_row.addWidget(self.stack_radio)
        switch_row.addStretch(1)

        # --- views
        self.mosaic = MosaicView(self.model)
        self.mosaic.send_to_stack.connect(self.add_to_stack)
        self.stack_view = StackView()

        self.views = QStackedWidget()
        self.views.addWidget(self.mosaic)
        self.views.addWidget(self.stack_view)
        self.view_group.idClicked.connect(self.views.setCurrentIndex)

        layout = QVBoxLayout(self)
        layout.addLayout(dir_row)
        layout.addLayout(switch_row)
        layout.addWidget(self.views, 1)

    # ----- directory / loading ------------------------------------------
    def set_directory(self, path: str, load: bool = True) -> None:
        self.dir_edit.setText(path)
        self.load_btn.setEnabled(bool(path))
        if load:
            self.request_load()

    def request_load(self) -> None:
        path = self.dir_edit.text()
        if not path:
            return
        self.model.clear()
        self.mosaic.set_skipped(0)
        self.load_requested.emit(path, self.recursive_chk.isChecked())

    def _browse(self) -> None:
        start = self.dir_edit.text() or self.last_browse_dir
        path = QFileDialog.getExistingDirectory(self, "Choose image folder", start)
        if path:
            self.set_directory(path)

    def set_busy(self, busy: bool) -> None:
        for w in (self.browse_btn, self.load_btn, self.recursive_chk):
            w.setEnabled(not busy and (w is not self.load_btn or bool(self.dir_edit.text())))

    # ----- stack hand-off ------------------------------------------------
    def add_to_stack(self, paths: list[str]) -> None:
        self.stack_view.add_paths(paths)
        self.stack_radio.setChecked(True)
        self.views.setCurrentIndex(1)

    @property
    def stack_paths(self) -> list[str]:
        return self.stack_view.paths()
