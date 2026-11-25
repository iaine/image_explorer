"""Pipeline 1: scan a directory and load thumbnails on a background thread."""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QImageIOHandler, QImageReader

from ..models.image_model import ImageRecord
from .base import Job, JobFailed
from .eta import EtaEstimator


def supported_extensions() -> set[str]:
    return {"." + bytes(f).decode().lower() for f in QImageReader.supportedImageFormats()}


class LoadImagesJob(Job):
    """Emits ``item`` with *lists* of ImageRecord (batched to keep the UI smooth).

    Result on success: dict(directory, loaded, skipped=[(path, reason)], seconds)
    """

    title = "Load images"
    #: Max seconds between batches sent to the UI.
    batch_interval = 0.05

    def __init__(self, directory: str, recursive: bool = False, thumb_size: int = 256) -> None:
        super().__init__()
        self.directory = directory
        self.recursive = recursive
        self.thumb_size = thumb_size

    # ----- scanning ------------------------------------------------------
    def scan(self) -> list[Path]:
        root = Path(self.directory)
        exts = supported_extensions()
        pattern = root.rglob("*") if self.recursive else root.iterdir()
        files = []
        for p in pattern:
            self.check_cancelled()
            if p.suffix.lower() in exts and p.is_file() and not p.name.startswith("."):
                files.append(p)
        files.sort(key=lambda p: str(p).lower())
        return files

    # ----- loading one image --------------------------------------------
    def load_one(self, path: Path) -> ImageRecord:
        reader = QImageReader(str(path))

        def reason(fallback: str) -> str:
            err = reader.errorString()
            return fallback if not err or "unknown" in err.lower() else err

        reader.setAutoTransform(True)  # honour EXIF orientation
        size = reader.size()
        if not reader.canRead():
            raise ValueError(reason("unrecognised or corrupt image"))

        if size.isValid():
            w, h = size.width(), size.height()
            if reader.transformation() & QImageIOHandler.TransformationRotate90:
                w, h = h, w  # report the size the user will see
            if max(size.width(), size.height()) > self.thumb_size:
                reader.setScaledSize(size.scaled(QSize(self.thumb_size, self.thumb_size), Qt.KeepAspectRatio))
            thumb = reader.read()
        else:  # formats that can't report size up front
            full = reader.read()
            w, h = full.width(), full.height()
            thumb = full.scaled(self.thumb_size, self.thumb_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        if thumb.isNull():
            raise ValueError(reason("could not decode image (corrupt or truncated?)"))

        stat = path.stat()
        return ImageRecord(
            path=str(path),
            name=str(path.relative_to(self.directory)) if self.recursive else path.name,
            size_bytes=stat.st_size,
            mtime=stat.st_mtime,
            width=w,
            height=h,
            thumb=thumb.convertToFormat(QImage.Format_ARGB32_Premultiplied),
        )

    # ----- Job -----------------------------------------------------------
    def summarize(self, result: dict) -> str:
        msg = f"Loaded {result['loaded']} images in {result['seconds']:.1f} s"
        if result["skipped"]:
            msg += f" ({len(result['skipped'])} skipped)"
        return msg

    def execute(self):
        start = time.monotonic()
        if not os.path.isdir(self.directory):
            raise JobFailed(f"Folder not found: {self.directory}")
        if not os.access(self.directory, os.R_OK):
            raise JobFailed(f"Folder can't be read: {self.directory}")

        files = self.scan()
        if not files:
            where = " (including subfolders)" if self.recursive else ""
            raise JobFailed(f"No supported images found in {self.directory}{where}.")
        self.log(f"Found {len(files)} image files in {self.directory}")

        eta = EtaEstimator(len(files), window=25)
        batch: list[ImageRecord] = []
        skipped: list[tuple[str, str]] = []
        last_flush = time.monotonic()

        for i, path in enumerate(files, 1):
            self.check_cancelled()
            try:
                batch.append(self.load_one(path))
            except Exception as exc:  # noqa: BLE001 - a bad file shouldn't stop the run
                skipped.append((str(path), str(exc)))
                self.log(f"Skipped {path.name}: {exc}")

            now = time.monotonic()
            if batch and now - last_flush >= self.batch_interval:
                self.signals.item.emit(batch)
                batch, last_flush = [], now
            self.report_progress(i, len(files), eta.step())

        if batch:
            self.signals.item.emit(batch)

        loaded = len(files) - len(skipped)
        if loaded == 0:
            raise JobFailed(f"None of the {len(files)} image files could be read.")
        return {
            "directory": self.directory,
            "loaded": loaded,
            "skipped": skipped,
            "seconds": time.monotonic() - start,
        }
