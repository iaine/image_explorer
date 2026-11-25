"""Milestone 4 tests: OpenCV probe, frame-time maths, estimates and the UI."""
import cv2
import numpy as np
import pytest

from app.jobs import JobRunner
from app.jobs.probe_video import ProbeResult, ProbeVideoJob
from app.ui.main_window import MainWindow
from app.video.opencv_video import (
    SampleStats,
    VideoInfo,
    fourcc_to_str,
    plan_slice,
    timestamps_for,
)


def make_video(path, seconds=12, fps=10, size=(320, 240)):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    assert w.isOpened()
    for i in range(int(seconds * fps)):
        frame = np.zeros((size[1], size[0], 3), np.uint8)
        frame[:] = (i * 3 % 255, 80, 160)
        cv2.putText(frame, str(i), (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
        w.write(frame)
    w.release()
    return path


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("vid") / "clip.mp4")


@pytest.fixture
def runner(qtbot):
    r = JobRunner()
    yield r
    r.shutdown()


# ----- pure functions ------------------------------------------------------
@pytest.mark.parametrize(
    "duration, x, expected",
    [
        (10.0, 5, [0, 5]),  # a frame exactly at the end doesn't exist
        (10.5, 5, [0, 5, 10]),
        (12.0, 1, list(range(12))),
        (3.0, 10, [0]),  # x longer than the video -> first frame only
        (0.4, 1, [0]),
    ],
)
def test_timestamps(duration, x, expected):
    assert timestamps_for(duration, x) == expected


def test_timestamps_rejects_zero():
    with pytest.raises(ValueError):
        timestamps_for(10, 0)


def test_fourcc():
    assert fourcc_to_str(cv2.VideoWriter_fourcc(*"avc1")) == "avc1"
    assert fourcc_to_str(0) == "unknown"


def fake(duration=600.0, fps=30.0):
    info = VideoInfo("/v.mp4", 1920, 1080, fps, int(duration * fps), duration, "avc1", "FFMPEG", 1)
    stats = SampleStats(
        seek_read_s=0.05, grab_s=0.004, retrieve_s=0.003,
        encode={"JPEG@90": (0.01, 200_000), "PNG": (0.05, 2_000_000)},
    )
    return info, stats


def test_plan_picks_sequential_for_small_intervals_and_seek_for_large():
    info, stats = fake()
    # x=1: seek ≈ 600×0.062 = 37 s; sequential ≈ 17,971×0.004 + 600×0.015 ≈ 81 s
    small = plan_slice(info, stats, 1)
    large = plan_slice(info, stats, 60)
    assert small.count == 600 and large.count == 10
    assert large.strategy == "seek"
    # whichever strategy is chosen, it must be the cheaper of the two
    for plan in (small, large):
        seek = plan.count * (stats.seek_read_s + stats.cost("JPEG")[0] + 0.002)
        assert plan.est_seconds <= seek + 1e-9


def test_plan_prefers_sequential_when_seeking_is_expensive():
    info, stats = fake()
    stats.seek_read_s = 0.5  # e.g. long GOPs: every seek decodes many frames
    assert plan_slice(info, stats, 1).strategy == "sequential"


def test_plan_disk_estimate_scales_with_format():
    info, stats = fake()
    jpeg = plan_slice(info, stats, 10, "JPEG")
    png = plan_slice(info, stats, 10, "PNG")
    assert jpeg.est_bytes == pytest.approx(60 * 200_000)
    assert png.est_bytes == pytest.approx(10 * jpeg.est_bytes)


# ----- probe job -----------------------------------------------------------
def test_probe_reads_metadata_and_measures(qtbot, runner, video):
    job = ProbeVideoJob(str(video))
    results = []
    job.signals.success.connect(results.append)
    with qtbot.waitSignal(job.signals.finished, timeout=10000):
        runner.start(job)
    r: ProbeResult = results[0]
    assert (r.info.width, r.info.height) == (320, 240)
    assert r.info.fps == pytest.approx(10)
    assert r.info.duration == pytest.approx(12, abs=0.2)
    assert r.stats.samples == 5
    assert r.stats.seek_read_s > 0 and r.stats.grab_s > 0
    assert r.stats.cost("PNG")[1] > 0 and r.stats.cost("JPEG")[1] > 0
    # higher JPEG quality -> bigger files, and interpolation sits between ladder points
    assert r.stats.cost("JPEG", 40)[1] < r.stats.cost("JPEG", 100)[1]
    b85, b88, b90 = (r.stats.cost("JPEG", q)[1] for q in (85, 88, 90))
    assert min(b85, b90) <= b88 <= max(b85, b90)


def test_probe_fails_for_non_video(qtbot, runner, tmp_path):
    bad = tmp_path / "fake.mp4"
    bad.write_bytes(b"this is not a video" * 100)
    job = ProbeVideoJob(str(bad))
    with qtbot.waitSignal(job.signals.failure, timeout=5000) as blocker:
        runner.start(job)
    assert "couldn't" in blocker.args[0].lower()


def test_probe_fails_for_missing_file(qtbot, runner, tmp_path):
    job = ProbeVideoJob(str(tmp_path / "nope.mp4"))
    with qtbot.waitSignal(job.signals.failure, timeout=3000) as blocker:
        runner.start(job)
    assert "not found" in blocker.args[0]


# ----- UI ------------------------------------------------------------------
def test_slice_tab_end_to_end(qtbot, video):
    win = MainWindow(show_dialogs=False)
    qtbot.addWidget(win)
    tab = win.slice_tab
    with qtbot.waitSignal(win.runner.job_finished, timeout=10000):
        tab.set_video(str(video))
    qtbot.waitUntil(lambda: tab.probe is not None, timeout=2000)

    assert tab.res_lbl.text() == "320 × 240"
    assert tab.out_edit.text().endswith("clip_frames")  # sensible default
    assert tab.interval_spin.maximum() == 12

    tab.interval_spin.setValue(5)
    assert tab.frames_lbl.text() == "3"  # 0, 5, 10 s
    assert tab.times_lbl.text() == "0:00.0, 0:05.0, 0:10.0"
    tab.interval_spin.setValue(1)
    assert tab.frames_lbl.text() == "12"
    assert "…" in tab.times_lbl.text()

    jpeg_disk = tab.plan.est_bytes
    tab.format_combo.setCurrentText("PNG")
    assert tab.plan.est_bytes > jpeg_disk
    assert tab.time_lbl.text().startswith("≈")

    tab.format_combo.setCurrentText("JPEG")
    hi = tab.plan.est_bytes
    tab.quality_spin.setValue(50)
    assert tab.plan.est_bytes < hi
    assert tab.plan.quality == 50

    tab.interval_spin.setValue(12)
    assert tab.warning_lbl.isVisibleTo(tab) and "first frame" in tab.warning_lbl.text()


def test_slice_tab_failure_clears_estimate(qtbot, tmp_path):
    bad = tmp_path / "fake.mp4"
    bad.write_bytes(b"junk")
    win = MainWindow(show_dialogs=False)
    qtbot.addWidget(win)
    with qtbot.waitSignal(win.runner.job_failure, timeout=5000):
        win.slice_tab.set_video(str(bad))
    assert win.slice_tab.probe is None
    assert win.slice_tab.frames_lbl.text() == "—"
