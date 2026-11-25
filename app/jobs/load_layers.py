"""Load full-resolution images for the stack view on a background thread."""
from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QImageIOHandler, QImageReader

from .base import Job, JobFailed

#: Longest edge kept in memory per layer. Larger images are downscaled for
#: display but keep their true size on the canvas (see LayerImage.scale).
MAX_LAYER_DIM = 4096


@dataclass
class LayerImage:
    path: str
    image: QImage
    orig_width: int
    orig_height: int

    @property
    def scale(self) -> float:
        """Factor that maps the in-memory image back to original pixels."""
        return self.orig_width / self.image.width() if self.image.width() else 1.0


class LoadLayersJob(Job):
    """Emits ``item`` with one LayerImage per path, in the order given."""

    title = "Load stack layers"

    def __init__(self, paths: list[str], max_dim: int = MAX_LAYER_DIM) -> None:
        super().__init__()
        self.paths = list(paths)
        self.max_dim = max_dim

    def load_one(self, path: str) -> LayerImage:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if not size.isValid():
            raise ValueError(reader.errorString() or "unrecognised image")
        w, h = size.width(), size.height()
        if reader.transformation() & QImageIOHandler.TransformationRotate90:
            w, h = h, w
        if max(size.width(), size.height()) > self.max_dim:
            reader.setScaledSize(size.scaled(QSize(self.max_dim, self.max_dim), Qt.KeepAspectRatio))
        img = reader.read()
        if img.isNull():
            raise ValueError("could not decode image")
        return LayerImage(path, img.convertToFormat(QImage.Format_ARGB32_Premultiplied), w, h)

    def summarize(self, result: dict) -> str:
        msg = f"Added {result['loaded']} layer(s) to the stack"
        if result["skipped"]:
            msg += f" ({len(result['skipped'])} could not be loaded)"
        return msg

    def execute(self):
        if not self.paths:
            raise JobFailed("No images to add to the stack.")
        skipped = []
        for i, path in enumerate(self.paths, 1):
            self.check_cancelled()
            try:
                self.signals.item.emit(self.load_one(path))
            except Exception as exc:  # noqa: BLE001
                skipped.append((path, str(exc)))
                self.log(f"Skipped {os.path.basename(path)}: {exc}")
            self.report_progress(i, len(self.paths))
        loaded = len(self.paths) - len(skipped)
        if loaded == 0:
            raise JobFailed("None of the selected images could be loaded.")
        return {"loaded": loaded, "skipped": skipped}
