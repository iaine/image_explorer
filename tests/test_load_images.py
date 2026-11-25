"""Milestone 2 tests: background image loading, model, sort/filter, mosaic."""
import os
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

from app.jobs.load_images import LoadImagesJob
from app.jobs import JobRunner
from app.models.image_model import ImageModel, ImageSortFilterProxy, PathRole
from app.ui.main_window import MainWindow


def make_image(path, w, h, colour="red"):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(colour))
    assert img.save(str(path))


@pytest.fixture
def image_dir(tmp_path):
    make_image(tmp_path / "img2.png", 800, 600, "red")
    make_image(tmp_path / "img10.jpg", 1200, 300, "green")
    make_image(tmp_path / "img1.png", 100, 100, "blue")  # smaller than thumb size
    sub = tmp_path / "sub"
    sub.mkdir()
    make_image(sub / "deep.png", 300, 900, "yellow")
    (tmp_path / "broken.png").write_bytes(b"not really a png")
    (tmp_path / "notes.txt").write_text("ignore me")
    (tmp_path / ".hidden.png").write_bytes(b"")
    # make file sizes / mtimes distinct and predictable
    now = time.time()
    for i, name in enumerate(["img1.png", "img2.png", "img10.jpg"]):
        os.utime(tmp_path / name, (now + i * 10, now + i * 10))
    return tmp_path


@pytest.fixture
def runner(qtbot):
    r = JobRunner()
    yield r
    r.shutdown()


def run_load(qtbot, runner, directory, **kw):
    job = LoadImagesJob(str(directory), **kw)
    batches, results = [], []
    job.signals.item.connect(batches.append)
    job.signals.success.connect(results.append)
    with qtbot.waitSignal(job.signals.finished, timeout=5000):
        runner.start(job)
    return job, [r for b in batches for r in b], results


def test_loads_images_and_skips_bad_files(qtbot, runner, image_dir):
    _, records, results = run_load(qtbot, runner, image_dir)
    names = sorted(r.name for r in records)
    assert names == ["img1.png", "img10.jpg", "img2.png"]
    result = results[0]
    assert result["loaded"] == 3
    assert [os.path.basename(p) for p, _ in result["skipped"]] == ["broken.png"]


def test_thumbnails_are_scaled_but_dimensions_are_original(qtbot, runner, image_dir):
    _, records, _ = run_load(qtbot, runner, image_dir, thumb_size=128)
    rec = next(r for r in records if r.name == "img10.jpg")
    assert (rec.width, rec.height) == (1200, 300)
    assert max(rec.thumb.width(), rec.thumb.height()) == 128
    small = next(r for r in records if r.name == "img1.png")
    assert small.thumb.width() == 100  # never upscaled


def test_recursive_includes_subfolders(qtbot, runner, image_dir):
    _, records, _ = run_load(qtbot, runner, image_dir, recursive=True)
    assert os.path.join("sub", "deep.png") in {r.name for r in records}


def test_empty_folder_fails(qtbot, runner, tmp_path):
    job = LoadImagesJob(str(tmp_path))
    with qtbot.waitSignal(job.signals.failure, timeout=3000) as blocker:
        runner.start(job)
    assert "No supported images" in blocker.args[0]


def test_missing_folder_fails(qtbot, runner, tmp_path):
    job = LoadImagesJob(str(tmp_path / "nope"))
    with qtbot.waitSignal(job.signals.failure, timeout=3000) as blocker:
        runner.start(job)
    assert "not found" in blocker.args[0]


def test_only_broken_files_fails(qtbot, runner, tmp_path):
    (tmp_path / "a.png").write_bytes(b"junk")
    job = LoadImagesJob(str(tmp_path))
    with qtbot.waitSignal(job.signals.failure, timeout=3000) as blocker:
        runner.start(job)
    assert "could be read" in blocker.args[0]


def test_proxy_natural_sort_and_filter(qtbot, runner, image_dir):
    _, records, _ = run_load(qtbot, runner, image_dir)
    model = ImageModel()
    model.add_records(records)
    proxy = ImageSortFilterProxy()
    proxy.setSourceModel(model)
    proxy.set_sort_key("Name")
    names = [proxy.index(i, 0).data() for i in range(proxy.rowCount())]
    assert names == ["img1.png", "img2.png", "img10.jpg"]

    proxy.set_sort_key("Dimensions", Qt.DescendingOrder)
    assert proxy.index(0, 0).data() == "img2.png"  # 800x600 is the largest area

    proxy.setFilterFixedString("IMG1")
    assert {proxy.index(i, 0).data() for i in range(proxy.rowCount())} == {"img1.png", "img10.jpg"}


def test_model_converts_thumb_to_pixmap_once(qtbot, runner, image_dir):
    _, records, _ = run_load(qtbot, runner, image_dir)
    model = ImageModel()
    model.add_records(records)
    idx = model.index(0, 0)
    pm = idx.data(Qt.DecorationRole)
    assert not pm.isNull()
    assert records[0].thumb is None  # QImage released after conversion
    assert idx.data(Qt.DecorationRole).cacheKey() == pm.cacheKey()


def test_window_end_to_end(qtbot, image_dir):
    win = MainWindow(show_dialogs=False)
    qtbot.addWidget(win)
    tab = win.explore_tab
    with qtbot.waitSignal(win.runner.job_finished, timeout=5000):
        tab.set_directory(str(image_dir))
    qtbot.waitUntil(lambda: not win.runner.is_busy(), timeout=1000)

    mosaic = tab.mosaic
    assert tab.model.rowCount() == 3
    assert "3 images" in mosaic.count_lbl.text() and "1 skipped" in mosaic.count_lbl.text()
    assert "Loaded 3 images" in win.status_lbl.text()

    mosaic.view.selectAll()
    assert mosaic.send_btn.isEnabled() and "(3)" in mosaic.send_btn.text()
    with qtbot.waitSignal(win.runner.job_finished, timeout=5000):
        mosaic.send_btn.click()
    assert len(tab.stack_paths) == 3
    assert tab.views.currentIndex() == 1

    # reloading clears the previous results first
    with qtbot.waitSignal(win.runner.job_finished, timeout=5000):
        tab.request_load()
    qtbot.waitUntil(lambda: not win.runner.is_busy(), timeout=1000)
    assert tab.model.rowCount() == 3
