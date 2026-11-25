"""Image records, the list model behind the mosaic, and a sort/filter proxy."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QImage, QPixmap

PathRole = Qt.UserRole + 1
RecordRole = Qt.UserRole + 2


@dataclass
class ImageRecord:
    path: str
    name: str
    size_bytes: int
    mtime: float
    width: int
    height: int
    #: Thumbnail created on the worker thread; released once converted to a pixmap.
    thumb: QImage | None = field(default=None, repr=False)

    @property
    def natural_key(self) -> list:
        """'img2.png' sorts before 'img10.png'."""
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", self.name)]


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


class ImageModel(QAbstractListModel):
    """Holds ImageRecords. Pixmaps are created lazily in the GUI thread."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._records: list[ImageRecord] = []
        self._pixmaps: dict[str, QPixmap] = {}

    # ----- editing -------------------------------------------------------
    def clear(self) -> None:
        self.beginResetModel()
        self._records.clear()
        self._pixmaps.clear()
        self.endResetModel()

    def add_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        start = len(self._records)
        self.beginInsertRows(QModelIndex(), start, start + len(records) - 1)
        self._records.extend(records)
        self.endInsertRows()

    def records(self) -> list[ImageRecord]:
        return list(self._records)

    def record(self, row: int) -> ImageRecord:
        return self._records[row]

    # ----- Qt model API --------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._records)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        rec = self._records[index.row()]
        if role == Qt.DisplayRole:
            return rec.name
        if role == Qt.DecorationRole:
            return self._pixmap(rec)
        if role == Qt.ToolTipRole:
            modified = datetime.fromtimestamp(rec.mtime).strftime("%Y-%m-%d %H:%M")
            return (
                f"<b>{rec.name}</b><br>{rec.width} × {rec.height} px<br>"
                f"{_human_size(rec.size_bytes)}<br>Modified {modified}"
            )
        if role == PathRole:
            return rec.path
        if role == RecordRole:
            return rec
        return None

    def _pixmap(self, rec: ImageRecord) -> QPixmap | None:
        pm = self._pixmaps.get(rec.path)
        if pm is None and rec.thumb is not None:
            pm = QPixmap.fromImage(rec.thumb)
            self._pixmaps[rec.path] = pm
            rec.thumb = None  # don't hold the image twice
        return pm


class ImageSortFilterProxy(QSortFilterProxyModel):
    """Sorts by name (natural), date or size; filters by filename substring."""

    SORT_KEYS = {
        "Name": lambda r: r.natural_key,
        "Date modified": lambda r: (r.mtime, r.natural_key),
        "File size": lambda r: (r.size_bytes, r.natural_key),
        "Dimensions": lambda r: (r.width * r.height, r.natural_key),
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._key = self.SORT_KEYS["Name"]
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setDynamicSortFilter(True)  # newly loaded rows land in order

    def set_sort_key(self, name: str, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        self._key = self.SORT_KEYS[name]
        self.invalidate()
        self.sort(0, order)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        a = left.data(RecordRole)
        b = right.data(RecordRole)
        return self._key(a) < self._key(b)
