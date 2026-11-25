"""``--self-test``: prove a build can load images, read video and slice it.

Runs the real jobs through the real window, so a frozen app that's missing
OpenCV's video backend, a Qt image plugin, or anything else, fails loudly.
Writes a report to stdout and to <temp>/image_explorer_selftest.txt (windowed
Windows builds have no console) and returns a process exit code.
"""
from __future__ import annotations

import os
import platform
import sys
import tempfile
import time
import traceback

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QColor, QImage, QImageReader

REPORT_PATH = os.path.join(tempfile.gettempdir(), "image_explorer_selftest.txt")


def _wait(signal, timeout_s: float = 60.0) -> bool:
    loop = QEventLoop()
    fired = {"ok": False}

    def done(*_):
        fired["ok"] = True
        loop.quit()

    signal.connect(done)
    QTimer.singleShot(int(timeout_s * 1000), loop.quit)
    loop.exec()
    return fired["ok"]


def run() -> int:
    import cv2
    import numpy as np

    from . import __version__
    from .resources import is_frozen
    from .ui.main_window import MainWindow

    lines: list[str] = []
    failures: list[str] = []

    def log(msg: str) -> None:
        lines.append(msg)
        print(msg, flush=True)

    def check(name: str, ok: bool, detail: str = "") -> None:
        log(f"[{'PASS' if ok else 'FAIL'}] {name}{' — ' + detail if detail else ''}")
        if not ok:
            failures.append(name)

    start = time.monotonic()
    log(f"Image Explorer {__version__} self-test")
    log(f"Python {platform.python_version()} on {platform.platform()} ({platform.machine()}), frozen={is_frozen()}")
    log(f"OpenCV {cv2.__version__}")

    try:
        formats = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
        check("Qt image formats", {"png", "jpg", "jpeg"} <= formats, ", ".join(sorted(formats)))

        work = tempfile.mkdtemp(prefix="imgexp_selftest_")
        pics = os.path.join(work, "pics")
        os.makedirs(pics)
        for i, colour in enumerate(["red", "green", "blue"]):
            img = QImage(160, 120, QImage.Format_RGB32)
            img.fill(QColor(colour))
            img.save(os.path.join(pics, f"img{i}.{'jpg' if i else 'png'}"))

        video = os.path.join(work, "clip.mp4")
        writer = cv2.VideoWriter(video, cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
        check("OpenCV can write MP4", writer.isOpened())
        for i in range(60):
            writer.write(np.full((240, 320, 3), (i * 4) % 256, np.uint8))
        writer.release()

        win = MainWindow(show_dialogs=False)
        win.show()

        # pipeline 1: folder -> mosaic -> stack
        win.open_in_explore(pics)
        _wait(win.runner.busy_changed)
        check("Load images", win.explore_tab.model.rowCount() == 3, f"{win.explore_tab.model.rowCount()} loaded")
        win.explore_tab.mosaic.view.selectAll()
        win.explore_tab.mosaic.send_btn.click()
        _wait(win.runner.busy_changed)
        check("Stack layers", len(win.explore_tab.stack_view.layers) == 3)

        # pipeline 2: probe -> slice
        win.open_video(video)
        _wait(win.runner.busy_changed)
        probe = win.slice_tab.probe
        check("Read video", probe is not None and probe.info.frame_count == 60,
              f"{probe.info.width}x{probe.info.height}, {probe.info.frame_count} frames, {probe.info.backend}" if probe else "no result")
        if probe is not None:
            out = os.path.join(work, "frames")
            win.slice_tab.out_edit.setText(out)
            win.slice_tab.interval_spin.setValue(2)
            win.slice_video()
            _wait(win.runner.busy_changed)
            saved = sorted(os.listdir(out)) if os.path.isdir(out) else []
            check("Slice video", len(saved) == 3, ", ".join(saved))

        win.close()
    except Exception:  # noqa: BLE001
        check("No unexpected exceptions", False, traceback.format_exc())

    log(f"{'OK' if not failures else 'FAILED: ' + ', '.join(failures)} in {time.monotonic() - start:.1f} s")
    try:
        with open(REPORT_PATH, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f"Report written to {REPORT_PATH}", flush=True)
    except OSError:
        pass
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
