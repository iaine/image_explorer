"""Locate bundled files both from source and inside a PyInstaller build."""
from __future__ import annotations

import os
import sys

APP_NAME = "Image Explorer"
ORG_NAME = "ImageExplorer"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_path(*parts: str) -> str:
    """Path to a file under ``assets/``.

    PyInstaller unpacks data files to ``sys._MEIPASS``; from source they live
    next to the ``app`` package.
    """
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", *parts)
