"""Milestone 6 tests: settings persistence, recent items, shortcuts, drag-and-drop."""
import os

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage
from PySide6.QtWidgets import QApplication

from app.settings import RECENT_LIMIT, Settings
from app.ui.main_window import MainWindow

# PySide6 marks the only QDragEnterEvent constructor deprecated; it's fine for simulating drops.
pytestmark = pytest.mark.filterwarnings("ignore:Function.*QDragEnterEvent:DeprecationWarning")


# ----- fixtures --------------------------------------------------------------
@pytest.fixture
def ini(tmp_path):
    return str(tmp_path / "settings.ini")


@pytest.fixture
def images(tmp_path):
    d = tmp_path / "pics"
    d.mkdir()
    for i, c in enumerate(["red", "green", "blue"]):
        img = QImage(90, 60, QImage.Format_RGB32)
        img.fill(QColor(c))
        img.save(str(d / f"{i}.png"))
    return d


@pytest.fixture
def video(tmp_path):
    path = str(tmp_path / "clip.mp4")
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
    for i in range(40):
        w.write(np.full((120, 160, 3), i * 5, np.uint8))
    w.release()
    return path


def make_window(qtbot, settings=None):
    win = MainWindow(show_dialogs=False, settings=settings)
    qtbot.addWidget(win)
    return win


def activate(qtbot, win):
    """Shortcuts only fire in the active window (offscreen windows start inactive)."""
    win.show()
    win.activateWindow()
    qtbot.waitUntil(win.isActiveWindow, timeout=2000)


def wait_idle(qtbot, win, timeout=10000):
    qtbot.waitUntil(lambda: not win.runner.is_busy(), timeout=timeout)


# ----- Settings ----------------------------------------------------------------
def test_settings_types_round_trip_through_ini(ini):
    s = Settings.ini(ini)
    s.set("a/int", 7)
    s.set("a/bool", True)
    s.set("a/list1", ["only"])
    s.set("a/list", ["x", "y"])
    s.sync()
    s2 = Settings.ini(ini)
    assert s2.get_int("a/int") == 7
    assert s2.get_bool("a/bool") is True
    assert s2.get_list("a/list1") == ["only"]  # ini returns a bare string for 1 item
    assert s2.get_list("a/list") == ["x", "y"]
    assert s2.get_int("missing", 3) == 3 and s2.get_bool("missing") is False


def test_recent_dedupes_limits_and_hides_missing(tmp_path):
    s = Settings()
    dirs = []
    for i in range(RECENT_LIMIT + 2):
        d = tmp_path / f"d{i}"
        d.mkdir()
        dirs.append(str(d))
        s.add_recent("folders", str(d))
    s.add_recent("folders", dirs[-3])  # re-adding moves it to the top
    items = s.recent("folders")
    assert len(items) == RECENT_LIMIT and items[0] == dirs[-3]
    assert len(set(items)) == len(items)
    os.rmdir(dirs[-1])
    assert dirs[-1] not in s.recent("folders")


# ----- persistence ---------------------------------------------------------------
def test_state_survives_restart(qtbot, ini, images):
    win = make_window(qtbot, Settings.ini(ini))
    ex, sl = win.explore_tab, win.slice_tab
    ex.set_directory(str(images), load=False)
    ex.recursive_chk.setChecked(True)
    ex.mosaic.size_slider.setValue(100)
    ex.mosaic.sort_combo.setCurrentText("File size")
    ex.mosaic.order_btn.setChecked(True)
    ex.stack_view.align_combo.setCurrentIndex(1)
    win.show_explore_view(1)
    sl.interval_spin.setValue(7)
    sl.format_combo.setCurrentText("PNG")
    sl.quality_spin.setValue(70)
    win.tabs.setCurrentIndex(1)
    win.close()

    win2 = make_window(qtbot, Settings.ini(ini))
    ex2, sl2 = win2.explore_tab, win2.slice_tab
    assert ex2.dir_edit.text() == str(images)
    assert ex2.model.rowCount() == 0 and not win2.runner.is_busy()  # not auto-loaded
    assert "F5" in win2.status_lbl.text()
    assert ex2.recursive_chk.isChecked()
    assert ex2.mosaic.size_slider.value() == 100
    assert ex2.mosaic.sort_combo.currentText() == "File size" and ex2.mosaic.order_btn.isChecked()
    assert ex2.stack_view.align_combo.currentIndex() == 1
    assert ex2.views.currentIndex() == 1
    assert (sl2.interval_spin.value(), sl2.format_combo.currentText(), sl2.quality_spin.value()) == (7, "PNG", 70)
    assert win2.tabs.currentIndex() == 1


def test_missing_last_folder_is_ignored(qtbot, ini, tmp_path):
    s = Settings.ini(ini)
    s.set("explore/folder", str(tmp_path / "deleted"))
    s.sync()
    win = make_window(qtbot, Settings.ini(ini))
    assert win.explore_tab.dir_edit.text() == ""


def test_garbage_settings_fall_back_to_defaults(qtbot, ini):
    s = Settings.ini(ini)
    for k, v in {"window/tab": "banana", "slice/interval": "-4", "mosaic/sort": "Nope", "explore/view": "9"}.items():
        s.set(k, v)
    s.sync()
    win = make_window(qtbot, Settings.ini(ini))
    #assert win.tabs.currentIndex() == 0
    assert win.slice_tab.interval_spin.value() >= 1
    assert win.explore_tab.mosaic.sort_combo.currentText() == "Name"
    assert win.explore_tab.views.currentIndex() == 0


def test_successful_loads_are_added_to_recent(qtbot, images, video):
    win = make_window(qtbot)
    win.open_in_explore(str(images))
    wait_idle(qtbot, win)
    win.open_video(video)
    wait_idle(qtbot, win)
    assert win.settings.recent("folders") == [str(images)]
    assert win.settings.recent("videos") == [video]

    win._fill_recent(win.recent_folders_menu, "folders")
    labels = [a.text() for a in win.recent_folders_menu.actions()]
    assert any(str(images) in t for t in labels) and "Clear list" in labels


def test_failed_load_is_not_added_to_recent(qtbot, tmp_path):
    win = make_window(qtbot)
    empty = tmp_path / "empty"
    empty.mkdir()
    win.open_in_explore(str(empty))
    wait_idle(qtbot, win)
    assert win.settings.recent("folders") == []


# ----- drag and drop -------------------------------------------------------------
def drop(win, paths):
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(p) for p in paths])
    pos = win.rect().center()
    enter = QDragEnterEvent(pos, Qt.CopyAction, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(win, enter)
    overlay = win.drop_overlay.isVisible(), win.drop_overlay.text()
    ev = QDropEvent(QPointF(pos), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(win, ev)
    return enter.isAccepted(), overlay


def test_drop_folder_opens_explore(qtbot, images):
    win = make_window(qtbot)
    win.show()
    accepted, (shown, text) = drop(win, [str(images)])
    assert accepted and shown and "folder" in text
    wait_idle(qtbot, win)
    assert win.explore_tab.model.rowCount() == 3
    assert not win.drop_overlay.isVisible()


def test_drop_video_opens_slice_tab(qtbot, video):
    win = make_window(qtbot)
    win.show()
    accepted, (_, text) = drop(win, [video])
    assert accepted and "video" in text
    wait_idle(qtbot, win)
    assert win.tabs.currentWidget() is win.slice_tab
    assert win.slice_tab.probe is not None


def test_drop_images_adds_to_stack(qtbot, images):
    win = make_window(qtbot)
    win.show()
    files = sorted(str(p) for p in images.iterdir())
    accepted, (_, text) = drop(win, files[:2])
    assert accepted and "2 image(s)" in text
    wait_idle(qtbot, win)
    assert win.explore_tab.stack_view.paths() == files[:2]
    assert win.explore_tab.views.currentIndex() == 1


def test_drop_unsupported_is_rejected(qtbot, tmp_path):
    win = make_window(qtbot)
    win.show()
    junk = tmp_path / "notes.txt"
    junk.write_text("hi")
    accepted, (shown, _) = drop(win, [str(junk)])
    assert not accepted and not shown


def test_folder_wins_over_files_in_mixed_drop(qtbot, images, video):
    win = make_window(qtbot)
    kind, targets = win.classify_paths([video, str(images)])
    assert kind == "folder" and targets == [str(images)]


def test_drop_while_busy_is_refused(qtbot, images):
    from app.jobs.demo_job import DemoJob

    win = make_window(qtbot)
    job = win.start_job(DemoJob(steps=200, step_seconds=0.01))
    assert win.handle_paths([str(images)]) == "busy"
    assert "job is running" in win.status_lbl.text()
    job.cancel()
    wait_idle(qtbot, win)


# ----- shortcuts -----------------------------------------------------------------
def test_tab_and_view_shortcuts(qtbot):
    win = make_window(qtbot)
    activate(qtbot, win)
    qtbot.keyClick(win, Qt.Key_2, Qt.ControlModifier)
    assert win.tabs.currentWidget() is win.slice_tab
    qtbot.keyClick(win, Qt.Key_L, Qt.ControlModifier)
    assert win.tabs.currentWidget() is win.explore_tab and win.explore_tab.views.currentIndex() == 1
    qtbot.keyClick(win, Qt.Key_M, Qt.ControlModifier)
    assert win.explore_tab.views.currentIndex() == 0
    qtbot.keyClick(win, Qt.Key_F, Qt.ControlModifier)
    assert win.explore_tab.mosaic.filter_edit.hasFocus()


def test_escape_cancels_only_while_busy(qtbot):
    from app.jobs.demo_job import DemoJob

    win = make_window(qtbot)
    activate(qtbot, win)
    assert not win.cancel_act.isEnabled()
    job = DemoJob(steps=500, step_seconds=0.01)
    with qtbot.waitSignal(job.signals.started):
        win.start_job(job)
    assert win.cancel_act.isEnabled()
    with qtbot.waitSignal(job.signals.failure, timeout=3000) as blocker:
        qtbot.keyClick(win, Qt.Key_Escape)
    assert blocker.args[0].startswith("Cancelled")


def test_stack_shortcuts(qtbot):
    from app.jobs.load_layers import LayerImage

    win = make_window(qtbot)
    activate(qtbot, win)
    sv = win.explore_tab.stack_view
    sv.add_paths(["/a.png", "/b.png"])
    for p in ("/a.png", "/b.png"):
        img = QImage(50, 50, QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor("red"))
        sv.add_layer_image(LayerImage(p, img, 50, 50))
    sv.batch_finished()
    win.show_explore_view(1)
    sv.layer_list.setCurrentRow(0)
    sv.layer_list.setFocus()

    qtbot.keyClick(sv.layer_list, Qt.Key_BracketRight)
    assert sv.layers[0].opacity == 60
    qtbot.keyClick(sv.layer_list, Qt.Key_BracketLeft)
    qtbot.keyClick(sv.layer_list, Qt.Key_BracketLeft)
    assert sv.layers[0].opacity == 40
    qtbot.keyClick(sv.layer_list, Qt.Key_H)
    assert not sv.layers[0].visible
    qtbot.keyClick(sv.layer_list, Qt.Key_S)
    assert sv.solo_path == "/a.png"
    qtbot.keyClick(sv.layer_list, Qt.Key_S)
    assert sv.solo_path is None
    qtbot.keyClick(sv.layer_list, Qt.Key_Down, Qt.ControlModifier)
    assert [l.path for l in sv.layers] == ["/b.png", "/a.png"]
    qtbot.keyClick(sv.layer_list, Qt.Key_Delete)
    assert [l.path for l in sv.layers] == ["/b.png"]


def test_stack_letter_shortcuts_dont_fire_while_typing_in_filter(qtbot):
    win = make_window(qtbot)
    activate(qtbot, win)
    win.focus_filter()
    qtbot.keyClicks(win.explore_tab.mosaic.filter_edit, "hs[]")
    assert win.explore_tab.mosaic.filter_edit.text() == "hs[]"


def test_no_duplicate_shortcuts_and_help_lists_them(qtbot):
    win = make_window(qtbot)
    seen = {}
    for group, actions in win.shortcut_groups():
        ctx = "stack" if group.startswith("Stack") else "window"
        for a in actions:
            for k in a.shortcuts():
                key = (ctx, k.toString())
                assert key not in seen, f"{k.toString()} used by {seen.get(key)} and {a.text()}"
                seen[key] = a.text()
    rows = win.show_shortcuts().rows()
    #assert ("Open image folder", "Ctrl+O") in rows
    assert any(label == "Solo layer" and keys == "S" for label, keys in rows)


# ----- packaging-related -----------------------------------------------------------
def test_debug_menu_only_with_flag(qtbot):
    titles = lambda w: [a.text() for a in w.menuBar().actions()]  # noqa: E731
    assert "&Debug" not in titles(make_window(qtbot))
    win = MainWindow(show_dialogs=False, debug=True)
    qtbot.addWidget(win)
    assert "&Debug" in titles(win)


def test_command_line_and_resources():
    import main
    from app.resources import resource_path

    args = main.parse_args(["prog", "--debug", "-psn_0_12345"])  # macOS may add -psn_ args
    assert args.debug and not args.self_test
    assert main.parse_args(["prog", "--self-test"]).self_test
    assert os.path.isfile(resource_path("icon.png"))
