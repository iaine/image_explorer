"""Milestone 3 tests: overlay stack view and the layer loader."""
import os

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QColor, QImage

from app.jobs import JobRunner
from app.jobs.load_layers import LayerImage, LoadLayersJob
from app.ui.main_window import MainWindow
from app.ui.stack_view import MAX_LAYERS, StackView


def layer_image(path, w=200, h=100, colour="red", scale=1):
    img = QImage(w // scale, h // scale, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(colour))
    return LayerImage(path, img, w, h)


def feed(view, specs):
    """Simulate a LoadLayersJob: add_paths -> item per path -> finished."""
    view.add_paths([s[0] for s in specs])
    for spec in specs:
        view.add_layer_image(layer_image(*spec))
    view.batch_finished()


@pytest.fixture
def view(qtbot):
    v = StackView()
    qtbot.addWidget(v)
    return v


def names(view):
    return [os.path.basename(l.path) for l in view.layers]


def test_first_path_is_on_top_with_defaults(view):
    feed(view, [("/a.png",), ("/b.png",), ("/c.png",)])
    assert names(view) == ["a.png", "b.png", "c.png"]
    assert [l.opacity for l in view.layers] == [50, 50, 100]
    z = [l.item.zValue() for l in view.layers]
    assert z == sorted(z, reverse=True)
    assert [round(l.item.opacity(), 2) for l in view.layers] == [0.5, 0.5, 1.0]


def test_second_batch_goes_on_top_and_keeps_existing_opacities(view):
    feed(view, [("/a.png",), ("/b.png",)])
    view.set_opacity(1, 80)
    feed(view, [("/x.png",), ("/y.png",)])
    assert names(view) == ["x.png", "y.png", "a.png", "b.png"]
    assert [l.opacity for l in view.layers] == [50, 50, 50, 80]


def test_duplicates_and_limit(view, qtbot):
    messages = []
    view.message.connect(messages.append)
    requested = []
    view.load_requested.connect(requested.append)
    view.add_paths(["/a.png", "/a.png"])  # same path twice in one call
    assert requested[-1] == ["/a.png"]
    view.batch_finished()

    view2 = StackView()
    qtbot.addWidget(view2)
    reqs = []
    view2.load_requested.connect(reqs.append)
    view2.message.connect(messages.append)
    view2.add_paths([f"/{i}.png" for i in range(MAX_LAYERS + 5)])
    assert len(reqs[0]) == MAX_LAYERS
    assert any("limit" in m for m in messages)


def test_already_in_stack_is_skipped(view):
    feed(view, [("/a.png",)])
    requested = []
    view.load_requested.connect(requested.append)
    view.add_paths(["/a.png", "/b.png"])
    assert requested == [["/b.png"]]


def test_opacity_slider_updates_selected_layer(view):
    feed(view, [("/a.png",), ("/b.png",)])
    view.layer_list.setCurrentRow(0)
    view.opacity_slider.setValue(25)
    assert view.layers[0].opacity == 25
    assert view.layers[0].item.opacity() == pytest.approx(0.25)
    assert "25%" in view.layer_list.item(0).text()
    assert view.opacity_spin.value() == 25


def test_visibility_checkbox(view):
    feed(view, [("/a.png",), ("/b.png",)])
    view.layer_list.item(0).setCheckState(Qt.Unchecked)
    assert view.layers[0].visible is False
    assert not view.layers[0].item.isVisible()
    assert view.layers[1].item.isVisible()


def test_solo_shows_one_layer_at_full_opacity_then_restores(view):
    feed(view, [("/a.png",), ("/b.png",), ("/c.png",)])
    view.layer_list.setCurrentRow(1)
    view.solo_btn.setChecked(True)
    vis = [l.item.isVisible() for l in view.layers]
    assert vis == [False, True, False]
    assert view.layers[1].item.opacity() == 1.0
    assert not view.opacity_slider.isEnabled()
    view.solo_btn.setChecked(False)
    assert all(l.item.isVisible() for l in view.layers)
    assert view.layers[1].item.opacity() == pytest.approx(0.5)


def test_move_and_remove(view):
    feed(view, [("/a.png",), ("/b.png",), ("/c.png",)])
    view.layer_list.setCurrentRow(2)
    view.move_current(-1)
    assert names(view) == ["a.png", "c.png", "b.png"]
    assert view.layer_list.currentRow() == 1
    assert view.layers[1].item.zValue() > view.layers[2].item.zValue()
    assert not view.up_btn.isEnabled() or view.layer_list.currentRow() > 0

    view.remove_current()
    assert names(view) == ["a.png", "b.png"]
    assert len(view.scene.items()) == 2


def test_drag_reorder_syncs_model(view):
    feed(view, [("/a.png",), ("/b.png",), ("/c.png",)])
    lw = view.layer_list
    # simulate an InternalMove: move row 0 to the end via the model
    lw.model().moveRow(lw.rootIndex(), 0, lw.rootIndex(), 3)
    order = [lw.item(i).data(Qt.UserRole) for i in range(lw.count())]
    assert [os.path.basename(p) for p in order] == names(view)
    z = [l.item.zValue() for l in view.layers]
    assert z == sorted(z, reverse=True)


def test_reset_opacities(view):
    feed(view, [("/a.png",), ("/b.png",)])
    view.set_opacity(0, 10)
    view.set_opacity(1, 10)
    view.reset_opacities()
    assert [l.opacity for l in view.layers] == [50, 100]


def test_alignment_and_true_size(view):
    feed(view, [("/big.png", 400, 200, "red", 4), ("/small.png", 100, 50)])
    big = next(l for l in view.layers if l.path == "/big.png")
    # stored downscaled (100x50) but displayed at original size
    assert big.item.sceneBoundingRect().width() == pytest.approx(400)
    assert big.item.pos().x() == pytest.approx(-200)  # centred
    view.align_combo.setCurrentIndex(1)  # top-left
    assert all(l.item.pos().x() == 0 and l.item.pos().y() == 0 for l in view.layers)


def test_clear(view):
    feed(view, [("/a.png",), ("/b.png",)])
    view.clear()
    assert view.layers == [] and view.layer_list.count() == 0 and not view.scene.items()
    assert not view.clear_btn.isEnabled()


# ----- loader -------------------------------------------------------------
@pytest.fixture
def runner(qtbot):
    r = JobRunner()
    yield r
    r.shutdown()


def test_layer_loader_downscales_but_reports_original_size(qtbot, runner, tmp_path):
    img = QImage(3000, 1000, QImage.Format_RGB32)
    img.fill(QColor("navy"))
    img.save(str(tmp_path / "wide.png"))
    (tmp_path / "bad.png").write_bytes(b"junk")
    job = LoadLayersJob([str(tmp_path / "wide.png"), str(tmp_path / "bad.png")], max_dim=1000)
    items, results = [], []
    job.signals.item.connect(items.append)
    job.signals.success.connect(results.append)
    with qtbot.waitSignal(job.signals.finished, timeout=5000):
        runner.start(job)
    li = items[0]
    assert (li.image.width(), li.orig_width, li.orig_height) == (1000, 3000, 1000)
    assert li.scale == pytest.approx(3.0)
    assert results[0]["loaded"] == 1 and len(results[0]["skipped"]) == 1


def test_layer_loader_fails_when_nothing_loads(qtbot, runner, tmp_path):
    (tmp_path / "bad.png").write_bytes(b"junk")
    job = LoadLayersJob([str(tmp_path / "bad.png")])
    with qtbot.waitSignal(job.signals.failure, timeout=3000):
        runner.start(job)


# ----- end to end ---------------------------------------------------------
def test_mosaic_to_stack_end_to_end(qtbot, tmp_path):
    for name, colour in [("1.png", "red"), ("2.png", "green"), ("3.png", "blue")]:
        img = QImage(120, 80, QImage.Format_RGB32)
        img.fill(QColor(colour))
        img.save(str(tmp_path / name))
    win = MainWindow(show_dialogs=False)
    qtbot.addWidget(win)
    tab = win.explore_tab
    with qtbot.waitSignal(win.runner.job_finished, timeout=5000):
        tab.set_directory(str(tmp_path))
    qtbot.waitUntil(lambda: not win.runner.is_busy(), timeout=1000)

    m = tab.mosaic
    sm = m.view.selectionModel()
    for r in range(3):
        sm.select(m.proxy.index(r, 0), QItemSelectionModel.Select)
    with qtbot.waitSignal(win.runner.job_finished, timeout=5000):
        m.send_btn.click()
    qtbot.waitUntil(lambda: not win.runner.is_busy(), timeout=1000)

    sv = tab.stack_view
    assert [os.path.basename(p) for p in sv.paths()] == ["1.png", "2.png", "3.png"]
    assert [l.opacity for l in sv.layers] == [50, 50, 100]
    assert tab.views.currentWidget() is sv
    assert "Added 3 layer(s)" in win.status_lbl.text()
