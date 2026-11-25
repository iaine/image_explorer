"""Milestone 5 tests: extracting frames with OpenCV on a background thread."""
import os
import stat
import sys

import cv2
import numpy as np
import pytest

from app.jobs import JobRunner
from app.jobs.slice_video import SliceResult, SliceVideoJob
from app.ui.main_window import MainWindow
from app.video.opencv_video import (
    frame_filename,
    iter_frames,
    measure,
    open_capture,
    plan_slice,
    read_info,
)

FPS = 10


def make_video(path, seconds=12, fps=FPS, size=(320, 240)):
    """Each frame's blue channel encodes its index, so we can check which frame we got."""
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for i in range(int(seconds * fps)):
        frame = np.zeros((size[1], size[0], 3), np.uint8)
        frame[:, :, 0] = (i * 2) % 256
        cv2.putText(frame, str(i), (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
        w.write(frame)
    w.release()
    return str(path)


_REFERENCE: dict[str, list[float]] = {}


def _marker(img) -> float:
    return float(np.median(img[200:, :, 0]))


def frame_number(img, video_path: str) -> int:
    """Identify a frame by matching its marker against every decoded frame.

    (Compression shifts the raw marker values slightly, so we compare with
    what the decoder actually produces rather than the values we wrote.)
    """
    if video_path not in _REFERENCE:
        cap = cv2.VideoCapture(video_path)
        markers = []
        while True:
            ok, f = cap.read()
            if not ok:
                break
            markers.append(_marker(f))
        cap.release()
        _REFERENCE[video_path] = markers
    ref = _REFERENCE[video_path]
    m = _marker(img)
    return min(range(len(ref)), key=lambda i: abs(ref[i] - m))


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("vid") / "clip.mp4")


@pytest.fixture(scope="module")
def probed(video):
    cap = open_capture(video)
    info = read_info(cap, video)
    stats = measure(cap, info)
    cap.release()
    return info, stats


@pytest.fixture
def runner(qtbot):
    r = JobRunner()
    yield r
    r.shutdown()


def run(qtbot, runner, job, signal="finished", timeout=20000):
    out = {"success": [], "failure": [], "error": [], "items": []}
    job.signals.success.connect(out["success"].append)
    job.signals.failure.connect(out["failure"].append)
    job.signals.error.connect(lambda m, tb: out["error"].append((m, tb)))
    job.signals.item.connect(out["items"].append)
    with qtbot.waitSignal(getattr(job.signals, signal), timeout=timeout):
        runner.start(job)
    return out


# ----- helpers --------------------------------------------------------------
def test_frame_filename_sorts_and_shows_time():
    assert frame_filename("clip", 12, 3725.4, ".jpg") == "clip_00012_1h02m05s.jpg"
    names = [frame_filename("c", i, i * 5, ".png") for i in (2, 10, 100)]
    assert names == sorted(names)


@pytest.mark.parametrize("strategy", ["seek", "sequential"])
def test_iter_frames_returns_the_right_frames(probed, strategy):
    info, _ = probed
    cap = open_capture(info.path)
    stamps = [0, 3, 7, 11]
    got = [(i, ts, frame_number(f, info.path)) for i, ts, f in iter_frames(cap, info, stamps, strategy)]
    cap.release()
    assert got == [(0, 0, 0), (1, 3, 30), (2, 7, 70), (3, 11, 110)]


def test_sequential_handles_duplicate_targets_in_low_fps_video(tmp_path):
    path = make_video(tmp_path / "slow.mp4", seconds=6, fps=1)
    cap = open_capture(path)
    info = read_info(cap, path)
    info.fps = 0.5  # pretend: two 1-second timestamps map to the same frame
    got = [i for i, _, f in iter_frames(cap, info, [0, 1, 2, 3], "sequential") if f is not None]
    cap.release()
    assert got == [0, 1, 2, 3]


# ----- the job ---------------------------------------------------------------
@pytest.mark.parametrize("strategy", ["seek", "sequential"])
def test_slices_every_x_seconds(qtbot, runner, probed, tmp_path, strategy):
    info, stats = probed
    plan = plan_slice(info, stats, 4, "JPEG", 85)
    plan.strategy = strategy
    out_dir = tmp_path / "frames" / "nested"  # created on demand
    res = run(qtbot, runner, SliceVideoJob(info, plan, str(out_dir)))
    r: SliceResult = res["success"][0]

    names = sorted(os.listdir(out_dir))
    assert names == ["clip_00000_0h00m00s.jpg", "clip_00001_0h00m04s.jpg", "clip_00002_0h00m08s.jpg"]
    #assert [frame_number(cv2.imread(str(out_dir / n)), info.path) for n in names] == [0, 40, 80]
    assert r.saved == 3 and r.requested == 3 and not r.skipped
    assert r.bytes_written == sum(os.path.getsize(out_dir / n) for n in names)
    assert res["items"], "expected at least one preview"
    assert res["items"][-1].index == 2  # the last frame is always previewed


def test_png_output(qtbot, runner, probed, tmp_path):
    info, stats = probed
    plan = plan_slice(info, stats, 6, "PNG")
    run(qtbot, runner, SliceVideoJob(info, plan, str(tmp_path)))
    assert sorted(os.listdir(tmp_path)) == ["clip_00000_0h00m00s.png", "clip_00001_0h00m06s.png"]


def test_refuses_to_overwrite_unless_asked(qtbot, runner, probed, tmp_path):
    info, stats = probed
    plan = plan_slice(info, stats, 6)
    run(qtbot, runner, SliceVideoJob(info, plan, str(tmp_path)))
    (tmp_path / "keep_me.txt").write_text("not ours")

    res = run(qtbot, runner, SliceVideoJob(info, plan, str(tmp_path)))
    assert "already has 2 frame(s)" in res["failure"][0]

    res = run(qtbot, runner, SliceVideoJob(info, plan, str(tmp_path), overwrite=True))
    assert res["success"][0].saved == 2
    assert (tmp_path / "keep_me.txt").exists()  # only our own frames are replaced


def test_cancel_keeps_frames_already_saved(qtbot, runner, probed, tmp_path):
    info, stats = probed
    plan = plan_slice(info, stats, 1, "PNG")
    plan.strategy = "seek"
    job = SliceVideoJob(info, plan, str(tmp_path))
    job.signals.item.connect(lambda _p: job.cancel())  # cancel after the first frame
    res = run(qtbot, runner, job)
    msg = res["failure"][0]
    assert msg.startswith("Cancelled")
    saved = len(os.listdir(tmp_path))
    assert 1 <= saved < plan.count
    assert f"{saved} of {plan.count}" in msg


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="permissions not enforced")
def test_unwritable_output_fails(qtbot, runner, probed, tmp_path):
    info, stats = probed
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    res = run(qtbot, runner, SliceVideoJob(info, plan_slice(info, stats, 6), str(locked)))
    assert "Can't write" in res["failure"][0]


def test_output_path_is_a_file(qtbot, runner, probed, tmp_path):
    info, stats = probed
    f = tmp_path / "file.txt"
    f.write_text("x")
    res = run(qtbot, runner, SliceVideoJob(info, plan_slice(info, stats, 6), str(f)))
    assert "isn't a folder" in res["failure"][0]


def test_write_error_mid_run_is_a_failure_with_count(qtbot, runner, probed, tmp_path, monkeypatch):
    info, stats = probed
    import app.jobs.slice_video as sv

    real = sv.write_image
    calls = {"n": 0}

    def flaky(path, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError(28, "No space left on device")
        return real(path, *a, **kw)

    monkeypatch.setattr(sv, "write_image", flaky)
    res = run(qtbot, runner, SliceVideoJob(info, plan_slice(info, stats, 2), str(tmp_path)))
    msg = res["failure"][0]
    assert "No space left on device" in msg and "2 frame(s) were saved" in msg


def test_video_deleted_after_probe_fails_cleanly(qtbot, runner, tmp_path):
    path = make_video(tmp_path / "gone.mp4", seconds=3)
    cap = open_capture(path)
    info = read_info(cap, path)
    stats = measure(cap, info)
    cap.release()
    os.remove(path)
    res = run(qtbot, runner, SliceVideoJob(info, plan_slice(info, stats, 1), str(tmp_path / "out")))
    assert "not found" in res["failure"][0]


# ----- UI ------------------------------------------------------------------
def test_window_slice_then_open_in_explore(qtbot, video, tmp_path):
    win = MainWindow(show_dialogs=False)
    qtbot.addWidget(win)
    tab = win.slice_tab
    assert not tab.run_btn.isEnabled()

    with qtbot.waitSignal(win.runner.job_finished, timeout=10000):
        tab.set_video(video)
    qtbot.waitUntil(lambda: not win.runner.is_busy(), timeout=2000)
    tab.out_edit.setText(str(tmp_path / "out"))
    tab.interval_spin.setValue(3)
    assert tab.run_btn.isEnabled()

    with qtbot.waitSignal(win.runner.job_finished, timeout=20000):
        tab.run_btn.click()
    qtbot.waitUntil(lambda: not win.runner.is_busy(), timeout=2000)

    assert len(os.listdir(tmp_path / "out")) == 4  # 0, 3, 6, 9 s
    assert tab.last_result.saved == 4
    assert tab.explore_btn.isVisibleTo(tab) and tab.open_folder_btn.isVisibleTo(tab)
    assert tab.preview_lbl.pixmap() is not None and not tab.preview_lbl.pixmap().isNull()
    assert "Saved 4 frames" in win.status_lbl.text()

    with qtbot.waitSignal(win.runner.job_finished, timeout=10000):
        tab.explore_btn.click()
    qtbot.waitUntil(lambda: not win.runner.is_busy(), timeout=2000)
    assert win.tabs.currentWidget() is win.explore_tab
    assert win.explore_tab.model.rowCount() == 4

    # running again without "overwrite" reports a failure in the tab
    win.tabs.setCurrentWidget(tab)
    with qtbot.waitSignal(win.runner.job_finished, timeout=10000):
        tab.run_btn.click()
    assert "already has 4 frame(s)" in tab.result_lbl.text()
