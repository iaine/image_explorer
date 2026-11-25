"""Typed wrapper around QSettings, plus recent-file lists.

``Settings()`` with no store keeps everything in memory (used by tests and
whenever persistence isn't wanted). ``Settings.user()`` uses the platform's
normal location (registry on Windows, plist on macOS, ini on Linux).
"""
from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import QByteArray, QSettings

RECENT_LIMIT = 8


class _MemoryStore:
    def __init__(self) -> None:
        self._d: dict[str, Any] = {}

    def value(self, key: str, default=None):
        return self._d.get(key, default)

    def setValue(self, key: str, value) -> None:  # noqa: N802 - mirror QSettings
        self._d[key] = value

    def remove(self, key: str) -> None:
        self._d.pop(key, None)

    def sync(self) -> None:
        pass


class Settings:
    def __init__(self, store: QSettings | None = None) -> None:
        self.store = store if store is not None else _MemoryStore()

    @classmethod
    def user(cls) -> "Settings":
        return cls(QSettings())

    @classmethod
    def ini(cls, path: str) -> "Settings":
        return cls(QSettings(path, QSettings.IniFormat))

    # ----- typed access ----------------------------------------------------
    # QSettings' ini/plist backends return strings for everything, so convert.
    def get_str(self, key: str, default: str = "") -> str:
        v = self.store.value(key, default)
        return default if v is None else str(v)

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.store.value(key, default))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = self.store.value(key, default)
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes")
        return bool(v)

    def get_list(self, key: str) -> list[str]:
        v = self.store.value(key, [])
        if v is None or v == "":
            return []
        if isinstance(v, str):  # a one-item list comes back as a plain string
            return [v]
        return [str(x) for x in v]

    def get_bytes(self, key: str) -> QByteArray | None:
        v = self.store.value(key)
        return v if isinstance(v, QByteArray) and not v.isEmpty() else None

    def set(self, key: str, value) -> None:
        self.store.setValue(key, value)

    def sync(self) -> None:
        self.store.sync()

    # ----- recent items ------------------------------------------------------
    def recent(self, kind: str, existing_only: bool = True) -> list[str]:
        items = self.get_list(f"recent/{kind}")
        if existing_only:
            items = [p for p in items if os.path.exists(p)]
        return items

    def add_recent(self, kind: str, path: str) -> list[str]:
        path = os.path.abspath(path)
        items = [p for p in self.get_list(f"recent/{kind}") if os.path.abspath(p) != path]
        items.insert(0, path)
        items = items[:RECENT_LIMIT]
        self.set(f"recent/{kind}", items)
        self.sync()
        return items

    def clear_recent(self, kind: str) -> None:
        self.set(f"recent/{kind}", [])
        self.sync()
