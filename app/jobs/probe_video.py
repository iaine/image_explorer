"""Milestone 4: read video metadata and time sample frames with OpenCV."""
from __future__ import annotations

import os
from dataclasses import dataclass

from ..video.opencv_video import SampleStats, VideoError, VideoInfo, measure, open_capture, read_info
from .base import Job, JobFailed


@dataclass
class ProbeResult:
    info: VideoInfo
    stats: SampleStats


class ProbeVideoJob(Job):
    title = "Analyse video"

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path

    def summarize(self, result: ProbeResult) -> str:
        i = result.info
        return (
            f"Analysed {os.path.basename(i.path)}: {i.width}×{i.height}, "
            f"{i.fps:.2f} fps, {i.duration:.1f} s, codec {i.codec}"
        )

    def execute(self) -> ProbeResult:
        self.report_progress(0, 3)
        try:
            cap = open_capture(self.path)
        except VideoError as exc:
            raise JobFailed(str(exc)) from exc
        try:
            info = read_info(cap, self.path)
            self.report_progress(1, 3)
            self.log(
                f"{os.path.basename(self.path)}: {info.width}×{info.height} @ {info.fps:.3f} fps, "
                f"{info.frame_count} frames, backend {info.backend}"
            )
            stats = measure(cap, info, check_cancelled=self.check_cancelled)
            self.report_progress(3, 3)
            return ProbeResult(info, stats)
        except VideoError as exc:
            raise JobFailed(str(exc)) from exc
        finally:
            cap.release()
