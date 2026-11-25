"""Milestone 5: save a frame every x seconds from a video, with OpenCV."""
from __future__ import annotations

import glob
import os
import time
import uuid
from dataclasses import dataclass, field

import cv2
from PySide6.QtGui import QImage

from ..video.opencv_video import (
    FORMATS,
    SlicePlan,
    VideoError,
    VideoInfo,
    frame_filename,
    iter_frames,
    open_capture,
    write_image,
)
from .base import Job, JobFailed
from .eta import EtaEstimator, format_duration

PREVIEW_SIZE = 320
PREVIEW_HZ = 5.0


@dataclass
class SliceResult:
    output_dir: str
    saved: int
    requested: int
    bytes_written: int
    seconds: float
    est_seconds: float
    strategy: str
    files: list[str] = field(default_factory=list)
    skipped: list[float] = field(default_factory=list)  # timestamps


@dataclass
class FramePreview:
    index: int
    total: int
    timestamp: float
    path: str
    image: QImage


def bgr_to_qimage(frame, max_side: int = PREVIEW_SIZE) -> QImage:
    h, w = frame.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1:
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    # .copy() so the QImage owns its memory once the numpy array is gone
    return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()


class SliceVideoJob(Job):
    """Writes frames named <stem>_<index>_<HhMMmSSs>.<ext> into ``output_dir``.

    item signal: FramePreview (throttled) for a live "latest frame" preview.
    """

    title = "Slice video"

    def __init__(
        self,
        info: VideoInfo,
        plan: SlicePlan,
        output_dir: str,
        overwrite: bool = False,
    ) -> None:
        super().__init__()
        self.info = info
        self.plan = plan
        self.output_dir = output_dir
        self.overwrite = overwrite
        self.stem = os.path.splitext(os.path.basename(info.path))[0]
        self.saved = 0
        self._last_preview = 0.0

    # ----- messages ------------------------------------------------------
    def cancel_message(self) -> str:
        return (
            f"Cancelled — {self.saved} of {self.plan.count} frames were saved to {self.output_dir}"
            if self.saved
            else "Cancelled by user — no frames were saved"
        )

    def summarize(self, r: SliceResult) -> str:
        msg = (
            f"Saved {r.saved} frames to {r.output_dir} in {format_duration(r.seconds)} "
            f"(estimate was {format_duration(max(r.est_seconds, 1))})"
        )
        if r.skipped:
            msg += f"; {len(r.skipped)} frame(s) couldn't be read"
        return msg

    # ----- checks --------------------------------------------------------
    def existing_outputs(self) -> list[str]:
        ext = FORMATS[self.plan.fmt]
        return glob.glob(os.path.join(glob.escape(self.output_dir), f"{glob.escape(self.stem)}_*{ext}"))

    def prepare_output_dir(self) -> None:
        if os.path.exists(self.output_dir) and not os.path.isdir(self.output_dir):
            raise JobFailed(f"The output path exists but isn't a folder: {self.output_dir}")
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            probe = os.path.join(self.output_dir, f".write_test_{uuid.uuid4().hex}")
            with open(probe, "wb") as fh:
                fh.write(b"ok")
            os.remove(probe)
        except OSError as exc:
            raise JobFailed(f"Can't write to the output folder {self.output_dir}: {exc.strerror or exc}") from exc

        existing = self.existing_outputs()
        if existing and not self.overwrite:
            raise JobFailed(
                f"The output folder already has {len(existing)} frame(s) from this video. "
                "Tick “Overwrite existing frames” or choose another folder."
            )
        if existing and self.overwrite:
            for p in existing:
                os.remove(p)
            self.log(f"Removed {len(existing)} existing frame(s) from a previous run")

    # ----- Job -----------------------------------------------------------
    def execute(self) -> SliceResult:
        start = time.monotonic()
        plan, info = self.plan, self.info
        self.prepare_output_dir()
        try:
            cap = open_capture(info.path)
        except VideoError as exc:
            raise JobFailed(str(exc)) from exc

        self.log(
            f"Extracting {plan.count} frame(s) every {plan.interval} s as {plan.fmt}"
            f"{f' q{plan.quality}' if plan.fmt == 'JPEG' else ''}, method: {plan.strategy}"
        )
        digits = max(5, len(str(plan.count)))
        ext = FORMATS[plan.fmt]
        eta = EtaEstimator(plan.count, window=15)
        self.signals.progress.emit(0, plan.count)
        self.signals.eta.emit(plan.est_seconds)  # until we have real measurements

        files, skipped, total_bytes = [], [], 0
        try:
            for i, ts, frame in iter_frames(cap, info, plan.timestamps, plan.strategy):
                self.check_cancelled()
                if frame is None:
                    skipped.append(ts)
                    self.log(f"Couldn't read the frame at {ts:.1f} s — skipped")
                else:
                    path = os.path.join(self.output_dir, frame_filename(self.stem, i, ts, ext, digits))
                    try:
                        total_bytes += write_image(path, frame, plan.fmt, plan.quality)
                    except OSError as exc:
                        raise JobFailed(
                            f"Couldn't write {os.path.basename(path)}: {exc.strerror or exc}. "
                            f"{self.saved} frame(s) were saved before this."
                        ) from exc
                    files.append(path)
                    self.saved += 1
                    self._maybe_preview(i, plan.count, ts, path, frame)
                self.report_progress(i + 1, plan.count, eta.step())
        finally:
            cap.release()

        if self.saved == 0:
            raise JobFailed("No frames could be read from this video.")
        return SliceResult(
            output_dir=self.output_dir,
            saved=self.saved,
            requested=plan.count,
            bytes_written=total_bytes,
            seconds=time.monotonic() - start,
            est_seconds=plan.est_seconds,
            strategy=plan.strategy,
            files=files,
            skipped=skipped,
        )

    def _maybe_preview(self, i, total, ts, path, frame) -> None:
        now = time.monotonic()
        if now - self._last_preview < 1 / PREVIEW_HZ and i < total - 1:
            return
        self._last_preview = now
        self.signals.item.emit(FramePreview(i, total, ts, path, bgr_to_qimage(frame)))
