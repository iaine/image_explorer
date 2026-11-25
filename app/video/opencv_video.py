"""OpenCV video helpers shared by the probe (M4) and the slicer (M5).

Everything here is plain Python + OpenCV (no Qt), so it can run on a worker
thread and be unit-tested directly.
"""
from __future__ import annotations

import bisect
import math
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

import cv2
import numpy as np

#: Output formats: name -> file extension
FORMATS: dict[str, str] = {"JPEG": ".jpg", "PNG": ".png"}
DEFAULT_JPEG_QUALITY = 90
#: JPEG qualities sampled during the probe; others are interpolated.
JPEG_LADDER = (40, 60, 75, 85, 90, 95, 100)
PNG_COMPRESSION = 3


def encode_params(fmt: str, quality: int = DEFAULT_JPEG_QUALITY) -> list[int]:
    if fmt == "JPEG":
        return [cv2.IMWRITE_JPEG_QUALITY, int(quality)]
    if fmt == "PNG":
        return [cv2.IMWRITE_PNG_COMPRESSION, PNG_COMPRESSION]
    raise ValueError(f"Unknown format {fmt!r}")


class VideoError(Exception):
    """A user-facing problem with the video file."""


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration: float  # seconds
    codec: str
    backend: str
    file_size: int


@dataclass
class SampleStats:
    """Per-frame costs measured on real frames from this video."""

    seek_read_s: float  # seek to a timestamp + decode one frame
    grab_s: float  # sequential grab (decode, no colour conversion)
    retrieve_s: float  # convert a grabbed frame to BGR
    #: (seconds, bytes) per encoded frame, keyed "PNG" or "JPEG@<quality>"
    encode: dict[str, tuple[float, float]] = field(default_factory=dict)
    samples: int = 0

    def cost(self, fmt: str, quality: int = DEFAULT_JPEG_QUALITY) -> tuple[float, float]:
        """(encode seconds, bytes) per frame; JPEG qualities are interpolated."""
        if fmt != "JPEG":
            return self.encode.get(fmt, (0.0, 0.0))
        qs = sorted(int(k.split("@")[1]) for k in self.encode if k.startswith("JPEG@"))
        if not qs:
            return (0.0, 0.0)
        q = min(max(quality, qs[0]), qs[-1])
        i = bisect.bisect_left(qs, q)
        if qs[i] == q:
            return self.encode[f"JPEG@{q}"]
        lo, hi = qs[i - 1], qs[i]
        (t0, b0), (t1, b1) = self.encode[f"JPEG@{lo}"], self.encode[f"JPEG@{hi}"]
        f = (q - lo) / (hi - lo)
        return (t0 + f * (t1 - t0), b0 + f * (b1 - b0))


@dataclass
class SlicePlan:
    interval: int
    timestamps: list[float]
    strategy: str  # "seek" or "sequential"
    est_seconds: float
    est_bytes: float
    fmt: str
    quality: int = DEFAULT_JPEG_QUALITY

    @property
    def count(self) -> int:
        return len(self.timestamps)


# --------------------------------------------------------------------------
# probing
# --------------------------------------------------------------------------
def fourcc_to_str(value: float) -> str:
    code = int(value)
    chars = "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4))
    return chars.strip("\x00 ").strip() or "unknown"


def open_capture(path: str) -> cv2.VideoCapture:
    if not os.path.isfile(path):
        raise VideoError(f"File not found: {path}")
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise VideoError(
            "OpenCV couldn't open this file. It may not be a video, or it uses a codec "
            "this OpenCV build doesn't support."
        )
    return cap


def read_info(cap: cv2.VideoCapture, path: str) -> VideoInfo:
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if fps <= 0 or fps > 1000:
        raise VideoError("Couldn't read the frame rate from this video.")
    if frames <= 0:
        raise VideoError("Couldn't determine the length of this video.")
    if width <= 0 or height <= 0:
        raise VideoError("Couldn't read the frame size from this video.")
    return VideoInfo(
        path=path,
        width=width,
        height=height,
        fps=fps,
        frame_count=frames,
        duration=frames / fps,
        codec=fourcc_to_str(cap.get(cv2.CAP_PROP_FOURCC)),
        backend=cap.getBackendName(),
        file_size=os.path.getsize(path),
    )


def timestamps_for(duration: float, interval: int) -> list[float]:
    """Frame times 0, x, 2x, … strictly before the end of the video."""
    if interval < 1:
        raise ValueError("interval must be a positive integer")
    count = max(1, math.ceil(duration / interval - 1e-9))
    return [i * interval for i in range(count)]


def measure(
    cap: cv2.VideoCapture,
    info: VideoInfo,
    n_samples: int = 5,
    n_grabs: int = 30,
    check_cancelled: Callable[[], None] = lambda: None,
) -> SampleStats:
    """Time real decode/encode work on this video to drive the estimate."""
    # warm-up: first read includes decoder start-up costs we don't want to count
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    ok, frame = cap.read()
    if not ok or frame is None:
        raise VideoError("Couldn't decode any frames from this video.")

    # 1) random access: seek + read at spread-out timestamps
    points = [info.duration * (i + 1) / (n_samples + 1) for i in range(n_samples)]
    seek_times, frames = [], []
    for t in points:
        check_cancelled()
        start = time.perf_counter()
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, f = cap.read()
        seek_times.append(time.perf_counter() - start)
        if ok and f is not None:
            frames.append(f)
    if not frames:
        frames = [frame]

    # 2) sequential decode from the start
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    grabs = min(n_grabs, max(1, info.frame_count - 1))
    start = time.perf_counter()
    done = 0
    for _ in range(grabs):
        check_cancelled()
        if not cap.grab():
            break
        done += 1
    grab_s = (time.perf_counter() - start) / max(done, 1)
    start = time.perf_counter()
    cap.retrieve()
    retrieve_s = time.perf_counter() - start

    # 3) encoding cost and size: PNG, plus a ladder of JPEG qualities
    variants = [("PNG", "PNG", 0)] + [(f"JPEG@{q}", "JPEG", q) for q in JPEG_LADDER]
    encode = {}
    for key, fmt, q in variants:
        times, sizes = [], []
        for f in frames:
            check_cancelled()
            start = time.perf_counter()
            ok, buf = cv2.imencode(FORMATS[fmt], f, encode_params(fmt, q))
            times.append(time.perf_counter() - start)
            sizes.append(len(buf) if ok else 0)
        # median time (robust to hiccups), mean size (what the disk will actually see)
        encode[key] = (float(np.median(times)), float(np.mean(sizes)))

    return SampleStats(
        seek_read_s=float(np.median(seek_times)),  # median: one slow seek shouldn't flip the plan
        grab_s=grab_s,
        retrieve_s=retrieve_s,
        encode=encode,
        samples=len(frames),
    )


#: Rough per-file cost of creating and writing an output file.
WRITE_OVERHEAD_S = 0.002


def plan_slice(
    info: VideoInfo,
    stats: SampleStats,
    interval: int,
    fmt: str = "JPEG",
    quality: int = DEFAULT_JPEG_QUALITY,
) -> SlicePlan:
    """Pick the cheaper extraction strategy and estimate time and disk use.

    seek:        count × (seek+read + encode)
    sequential:  frames up to the last target grabbed + count × (retrieve + encode)
    """
    stamps = timestamps_for(info.duration, interval)
    n = len(stamps)
    enc_s, enc_bytes = stats.cost(fmt, quality)
    per_out = enc_s + WRITE_OVERHEAD_S
    last_frame = min(info.frame_count, frame_index(stamps[-1], info) + 1)

    seek_cost = n * (stats.seek_read_s + per_out)
    seq_cost = last_frame * stats.grab_s + n * (stats.retrieve_s + per_out)
    strategy, cost = ("sequential", seq_cost) if seq_cost < seek_cost else ("seek", seek_cost)
    return SlicePlan(interval, stamps, strategy, cost, n * enc_bytes, fmt, quality)


# --------------------------------------------------------------------------
# extracting
# --------------------------------------------------------------------------
def frame_index(ts: float, info: VideoInfo) -> int:
    return min(info.frame_count - 1, int(round(ts * info.fps)))


def iter_frames(
    cap: cv2.VideoCapture, info: VideoInfo, timestamps: list[float], strategy: str
) -> Iterator[tuple[int, float, np.ndarray | None]]:
    """Yield (index, timestamp, frame-or-None) for each requested timestamp."""
    if strategy == "seek":
        for i, ts in enumerate(timestamps):
            cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000)
            ok, frame = cap.read()
            yield i, ts, frame if ok else None
        return

    if strategy != "sequential":
        raise ValueError(f"Unknown strategy {strategy!r}")
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    targets = [frame_index(ts, info) for ts in timestamps]
    ti, idx = 0, 0
    while ti < len(targets):
        if not cap.grab():
            break  # ran out of frames early (frame count over-reported)
        if idx == targets[ti]:
            ok, frame = cap.retrieve()
            # several timestamps can map to one frame in very low-fps video
            while ti < len(targets) and targets[ti] == idx:
                yield ti, timestamps[ti], frame if ok else None
                ti += 1
        idx += 1
    for j in range(ti, len(targets)):  # anything we never reached
        yield j, timestamps[j], None


def frame_filename(stem: str, index: int, ts: float, ext: str, digits: int = 5) -> str:
    """e.g. 'holiday_00012_0h01m00s.jpg' — sorts by name, shows the time."""
    h, rem = divmod(int(ts), 3600)
    m, s = divmod(rem, 60)
    return f"{stem}_{index:0{digits}d}_{h}h{m:02d}m{s:02d}s{ext}"


def write_image(path: str, frame: np.ndarray, fmt: str, quality: int) -> int:
    """Encode in memory then write with Python I/O.

    cv2.imwrite fails silently on Windows paths with non-ASCII characters, and
    this way a full disk raises an OSError we can report.
    """
    ok, buf = cv2.imencode(FORMATS[fmt], frame, encode_params(fmt, quality))
    if not ok:
        raise OSError(f"OpenCV couldn't encode the frame as {fmt}")
    with open(path, "wb") as fh:
        fh.write(buf.tobytes())
    return len(buf)
