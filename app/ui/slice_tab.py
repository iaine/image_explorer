"""Slice Video tab: choose a video, analyse it, and estimate the slicing job."""
from __future__ import annotations

import os
import shutil

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..jobs.eta import format_duration
from ..jobs.probe_video import ProbeResult
from ..jobs.slice_video import FramePreview, SliceResult
from ..video.opencv_video import DEFAULT_JPEG_QUALITY, FORMATS, SlicePlan, plan_slice

VIDEO_FILTER = "Video files (*.mp4 *.mov *.m4v *.avi *.mkv *.webm);;All files (*)"


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def format_timestamp(seconds: float) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:04.1f}" if h else f"{int(m)}:{s:04.1f}"


def _value_label() -> QLabel:
    lbl = QLabel("—")
    lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lbl


class SliceTab(QWidget):
    #: The main window runs a ProbeVideoJob for this path.
    probe_requested = Signal(str)
    #: The main window runs a SliceVideoJob from self.probe / self.plan.
    slice_requested = Signal()
    #: Load this folder in the Explore tab.
    open_in_explore = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.probe: ProbeResult | None = None
        self.plan: SlicePlan | None = None
        self._busy = False
        #: Where "Browse…" starts when no video is set (restored from settings).
        self.last_browse_dir = ""
        self.last_result: SliceResult | None = None

        # ---------- inputs
        self.video_edit = QLineEdit(placeholderText="Choose a video file…", readOnly=True)
        self.video_btn = QPushButton("Browse…")
        self.video_btn.clicked.connect(self._browse_video)
        video_row = QHBoxLayout()
        video_row.addWidget(self.video_edit, 1)
        video_row.addWidget(self.video_btn)

        self.out_edit = QLineEdit(placeholderText="Choose an output folder…")
        self.out_edit.textChanged.connect(self.update_estimate)
        self.out_edit.setAcceptDrops(False)  # file drops go to the main window
        self.out_btn = QPushButton("Browse…")
        self.out_btn.clicked.connect(self._browse_out)
        out_row = QHBoxLayout()
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(self.out_btn)

        self.interval_spin = QSpinBox(minimum=1, maximum=86_400, value=5, suffix=" s")
        self.interval_spin.setToolTip("Save one frame every x seconds")
        self.interval_spin.valueChanged.connect(self.update_estimate)
        self.format_combo = QComboBox()
        self.format_combo.addItems(list(FORMATS))
        self.format_combo.setToolTip("JPEG is much smaller; PNG is lossless")
        self.format_combo.currentTextChanged.connect(self.update_estimate)
        self.quality_lbl = QLabel("Quality:")
        self.quality_spin = QSpinBox(minimum=1, maximum=100, value=DEFAULT_JPEG_QUALITY)
        self.quality_spin.setToolTip("JPEG quality (higher = larger files, better detail)")
        self.quality_spin.valueChanged.connect(self.update_estimate)
        self.format_combo.currentTextChanged.connect(self._on_format_changed)
        self.overwrite_chk = QCheckBox("Overwrite existing frames")
        self.overwrite_chk.setToolTip("Replace frames from a previous run of this video in the output folder")
        settings_row = QHBoxLayout()
        settings_row.addWidget(self.interval_spin)
        settings_row.addSpacing(16)
        settings_row.addWidget(QLabel("Format:"))
        settings_row.addWidget(self.format_combo)
        settings_row.addWidget(self.quality_lbl)
        settings_row.addWidget(self.quality_spin)
        settings_row.addSpacing(16)
        settings_row.addWidget(self.overwrite_chk)
        settings_row.addStretch(1)

        form = QFormLayout()
        form.addRow("Video:", video_row)
        form.addRow("Every x seconds:", settings_row)
        form.addRow("Output folder:", out_row)

        # ---------- video details
        self.info_box = QGroupBox("Video")
        self.res_lbl, self.fps_lbl, self.dur_lbl = _value_label(), _value_label(), _value_label()
        self.codec_lbl, self.size_lbl = _value_label(), _value_label()
        info = QFormLayout(self.info_box)
        info.addRow("Resolution:", self.res_lbl)
        info.addRow("Frame rate:", self.fps_lbl)
        info.addRow("Duration:", self.dur_lbl)
        info.addRow("Codec:", self.codec_lbl)
        info.addRow("File size:", self.size_lbl)

        # ---------- estimate
        self.estimate_box = QGroupBox("Estimate")
        self.frames_lbl, self.times_lbl = _value_label(), _value_label()
        self.disk_lbl, self.time_lbl, self.method_lbl = _value_label(), _value_label(), _value_label()
        self.times_lbl.setWordWrap(True)
        est = QFormLayout(self.estimate_box)
        est.addRow("Frames to extract:", self.frames_lbl)
        est.addRow("Frame times:", self.times_lbl)
        est.addRow("Disk space:", self.disk_lbl)
        est.addRow("Processing time:", self.time_lbl)
        est.addRow("Method:", self.method_lbl)
        self.note_lbl = QLabel(
            "Estimates come from timing a few real frames of this video; actual times vary with disk speed."
        )
        self.note_lbl.setWordWrap(True)
        self.note_lbl.setStyleSheet("color: palette(mid);")
        est.addRow(self.note_lbl)
        self.warning_lbl = QLabel()
        self.warning_lbl.setWordWrap(True)
        self.warning_lbl.setStyleSheet("color: #b26a00;")
        self.warning_lbl.hide()
        est.addRow(self.warning_lbl)

        boxes = QHBoxLayout()
        boxes.addWidget(self.info_box, 1)
        boxes.addWidget(self.estimate_box, 2)

        self.run_btn = QPushButton("Slice video")
        self.run_btn.setDefault(True)
        self.run_btn.clicked.connect(self.slice_requested)
        run_row = QHBoxLayout()
        run_row.addStretch(1)
        run_row.addWidget(self.run_btn)

        # ---------- progress / result
        self.result_box = QGroupBox("Output")
        self.preview_lbl = QLabel()
        self.preview_lbl.setFixedSize(320, 180)
        self.preview_lbl.setAlignment(Qt.AlignCenter)
        self.preview_lbl.setStyleSheet("background: palette(dark); color: palette(light);")
        self.preview_lbl.setText("Latest frame appears here")
        self.result_lbl = QLabel("Nothing extracted yet.")
        self.result_lbl.setWordWrap(True)
        self.result_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.open_folder_btn = QPushButton("Open folder")
        self.open_folder_btn.clicked.connect(self._open_folder)
        self.explore_btn = QPushButton("Open in Explore")
        self.explore_btn.clicked.connect(
            lambda: self.last_result and self.open_in_explore.emit(self.last_result.output_dir)
        )
        for b in (self.open_folder_btn, self.explore_btn):
            b.hide()
        res_btns = QHBoxLayout()
        res_btns.addWidget(self.open_folder_btn)
        res_btns.addWidget(self.explore_btn)
        res_btns.addStretch(1)
        res_text = QVBoxLayout()
        res_text.addWidget(self.result_lbl)
        res_text.addLayout(res_btns)
        res_text.addStretch(1)
        res = QHBoxLayout(self.result_box)
        res.addWidget(self.preview_lbl)
        res.addLayout(res_text, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(boxes)
        layout.addLayout(run_row)
        layout.addWidget(self.result_box)
        layout.addStretch(1)
        self._refresh_run()

    # ======================================================================
    def set_video(self, path: str) -> None:
        self.video_edit.setText(path)
        self.clear_probe()
        if not self.out_edit.text():
            stem = os.path.splitext(os.path.basename(path))[0]
            self.out_edit.setText(os.path.join(os.path.dirname(path), f"{stem}_frames"))
        self.probe_requested.emit(path)

    def clear_probe(self) -> None:
        self.probe = None
        self.plan = None
        for lbl in (self.res_lbl, self.fps_lbl, self.dur_lbl, self.codec_lbl, self.size_lbl):
            lbl.setText("—")
        self.update_estimate()

    def set_probe(self, result: ProbeResult) -> None:
        if result.info.path != self.video_edit.text():
            return  # stale result from a previously chosen video
        self.probe = result
        i = result.info
        self.res_lbl.setText(f"{i.width} × {i.height}")
        self.fps_lbl.setText(f"{i.fps:.2f} fps")
        self.dur_lbl.setText(f"{format_timestamp(i.duration)}  ({i.frame_count:,} frames)")
        self.codec_lbl.setText(f"{i.codec}  (via {i.backend})")
        self.size_lbl.setText(human_bytes(i.file_size))
        self.interval_spin.setMaximum(max(1, int(i.duration)))
        self.update_estimate()

    def update_estimate(self, *_args) -> None:
        if self.probe is None:
            self.plan = None
            for lbl in (self.frames_lbl, self.times_lbl, self.disk_lbl, self.time_lbl, self.method_lbl):
                lbl.setText("—")
            self.warning_lbl.hide()
            self._refresh_run()
            return

        info, stats = self.probe.info, self.probe.stats
        x = self.interval_spin.value()
        fmt = self.format_combo.currentText()
        plan = plan_slice(info, stats, x, fmt, self.quality_spin.value())
        self.plan = plan

        self.frames_lbl.setText(f"{plan.count:,}")
        shown = [format_timestamp(t) for t in plan.timestamps[:4]]
        more = f", … {format_timestamp(plan.timestamps[-1])}" if plan.count > 4 else ""
        self.times_lbl.setText(", ".join(shown) + more)
        self.disk_lbl.setText(f"≈ {human_bytes(plan.est_bytes)}  (≈ {human_bytes(plan.est_bytes / plan.count)} per frame)")
        self.time_lbl.setText(f"≈ {format_duration(max(plan.est_seconds, 1))}")
        self.method_lbl.setText(
            "Decode straight through (measured faster for this video)"
            if plan.strategy == "sequential"
            else "Jump to each timestamp (measured faster for this video)"
        )
        self._update_warnings(plan)
        self._refresh_run()

    def _update_warnings(self, plan: SlicePlan) -> None:
        warnings = []
        if self.interval_spin.value() >= self.probe.info.duration:
            warnings.append("x is as long as the video, so only the first frame will be saved.")
        out = self.out_edit.text().strip()
        if out:
            existing = out
            while existing and not os.path.isdir(existing):
                parent = os.path.dirname(existing)
                if parent == existing:
                    break
                existing = parent
            if os.path.isdir(existing):
                free = shutil.disk_usage(existing).free
                if plan.est_bytes > free * 0.9:
                    warnings.append(
                        f"Only {human_bytes(free)} free on the output drive; this needs about "
                        f"{human_bytes(plan.est_bytes)}."
                    )
        self.warning_lbl.setText("⚠ " + "\n⚠ ".join(warnings) if warnings else "")
        self.warning_lbl.setVisible(bool(warnings))

    # ======================================================================
    def _browse_video(self) -> None:
        start = os.path.dirname(self.video_edit.text()) or self.last_browse_dir
        path, _ = QFileDialog.getOpenFileName(self, "Choose video", start, VIDEO_FILTER)
        if path:
            self.set_video(path)

    def _browse_out(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose output folder", self.out_edit.text())
        if path:
            self.out_edit.setText(path)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for w in (
            self.video_btn, self.out_btn, self.out_edit, self.interval_spin,
            self.format_combo, self.quality_spin, self.overwrite_chk,
        ):
            w.setEnabled(not busy)
        self._refresh_run()

    def output_dir(self) -> str:
        return os.path.abspath(os.path.expanduser(self.out_edit.text().strip()))

    def _on_format_changed(self, fmt: str) -> None:
        jpeg = fmt == "JPEG"
        self.quality_lbl.setVisible(jpeg)
        self.quality_spin.setVisible(jpeg)

    def _refresh_run(self) -> None:
        ready = self.plan is not None and bool(self.out_edit.text().strip())
        self.run_btn.setEnabled(ready and not self._busy)
        if self.plan is None:
            self.run_btn.setToolTip("Choose a video first")
        elif not self.out_edit.text().strip():
            self.run_btn.setToolTip("Choose an output folder")
        else:
            self.run_btn.setToolTip(f"Save {self.plan.count} frame(s)")

    # ----- progress / result --------------------------------------------
    def start_run(self) -> None:
        self.last_result = None
        self.open_folder_btn.hide()
        self.explore_btn.hide()
        self.preview_lbl.clear()
        self.preview_lbl.setText("Starting…")
        self.result_lbl.setText(f"Extracting {self.plan.count} frame(s) to {self.output_dir()}…")

    def show_preview(self, p: FramePreview) -> None:
        pm = QPixmap.fromImage(p.image).scaled(
            self.preview_lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.preview_lbl.setPixmap(pm)
        self.result_lbl.setText(
            f"Frame {p.index + 1} of {p.total} · {format_timestamp(p.timestamp)}\n{os.path.basename(p.path)}"
        )

    def show_result(self, r: SliceResult) -> None:
        self.last_result = r
        lines = [
            f"Saved {r.saved} of {r.requested} frame(s) ({human_bytes(r.bytes_written)}) "
            f"in {format_duration(r.seconds)}. Estimate was {format_duration(max(r.est_seconds, 1))}.",
            f"Folder: {r.output_dir}",
        ]
        if r.skipped:
            lines.append(f"{len(r.skipped)} frame(s) couldn't be read and were skipped (see log).")
        self.result_lbl.setText("\n".join(lines))
        self.open_folder_btn.show()
        self.explore_btn.show()

    def show_stopped(self, message: str) -> None:
        self.result_lbl.setText(message)

    def _open_folder(self) -> None:
        if self.last_result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_result.output_dir))
