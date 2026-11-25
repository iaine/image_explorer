"""Main window: tabs, menus/shortcuts, drag-and-drop, settings and job wiring."""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
)

from ..jobs import Job, JobRunner
from ..jobs.demo_job import DemoJob
from ..jobs.eta import format_duration
from ..jobs.load_images import LoadImagesJob, supported_extensions
from ..jobs.load_layers import LoadLayersJob
from ..jobs.probe_video import ProbeVideoJob
from ..jobs.slice_video import SliceVideoJob
from ..settings import Settings
from .explore_tab import ExploreTab
from .log_panel import LogPanel
from .shortcuts import ShortcutsDialog
from .slice_tab import VIDEO_FILTER, SliceTab

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv", ".mpg", ".mpeg"}


class MainWindow(QMainWindow):
    def __init__(self, show_dialogs: bool = True, settings: Settings | None = None, debug: bool = False) -> None:
        super().__init__()
        #: Show the Debug menu (demo jobs). Off in normal use; --debug turns it on.
        self.debug = debug
        #: Set False in tests so message boxes don't block.
        self.show_dialogs = show_dialogs
        #: In-memory settings unless a persistent store is passed in (see main.py).
        self.settings = settings or Settings()
        self.setWindowTitle("Image Explorer")
        self.resize(1100, 750)

        self.runner = JobRunner(self)

        # --- tabs
        self.explore_tab = ExploreTab()
        self.slice_tab = SliceTab()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.explore_tab, "Explore")
        self.tabs.addTab(self.slice_tab, "Slice Video")
        self.setCentralWidget(self.tabs)

        # --- log panel
        self.log_panel = LogPanel(self)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_panel)
        self.resizeDocks([self.log_panel], [140], Qt.Vertical)

        # --- status bar
        self.status_lbl = QLabel("Ready — drop a folder, video or images onto the window to start")
        self.eta_lbl = QLabel("")
        self.progress = QProgressBar(maximumWidth=240, textVisible=True)
        self.progress.hide()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setToolTip("Cancel running jobs (Esc)")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self._cancel)
        sb = self.statusBar()
        sb.addWidget(self.status_lbl, 1)
        sb.addPermanentWidget(self.eta_lbl)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.cancel_btn)

        # --- drag-and-drop overlay
        self.setAcceptDrops(True)
        self.drop_overlay = QLabel(self)
        self.drop_overlay.setAlignment(Qt.AlignCenter)
        self.drop_overlay.setWordWrap(True)
        self.drop_overlay.setStyleSheet(
            "background: rgba(30, 110, 200, 40); border: 3px dashed rgba(30, 110, 200, 200);"
            "border-radius: 12px; font-size: 18px; color: palette(text);"
        )
        self.drop_overlay.hide()

        # --- signals from tabs
        self.explore_tab.load_requested.connect(self.load_images)
        stack = self.explore_tab.stack_view
        stack.load_requested.connect(self.load_layers)
        stack.message.connect(lambda m: self.log_panel.append(f"Stack: {m}"))
        self.slice_tab.probe_requested.connect(self.probe_video)
        self.slice_tab.slice_requested.connect(self.slice_video)
        self.slice_tab.open_in_explore.connect(self.open_in_explore)

        self._build_menus()
        self._wire_runner()
        self.restore_state()

    # ======================================================================
    # menus & shortcuts
    # ======================================================================
    def _action(self, text, slot, keys=(), tip="") -> QAction:
        a = QAction(text, self)
        if keys:
            a.setShortcuts([QKeySequence(k) for k in keys])
            a.setShortcutContext(Qt.WindowShortcut)
        if tip:
            a.setStatusTip(tip)
        a.triggered.connect(slot)
        self.addAction(a)  # keeps shortcuts live even if a menu is hidden
        return a

    def _build_menus(self) -> None:
        mb = self.menuBar()

        # File
        self.open_folder_act = self._action("&Open image folder…", self.browse_folder, ["Ctrl+O"])
        self.open_video_act = self._action("Open &video…", self.browse_video, ["Ctrl+Shift+O"])
        self.reload_act = self._action("&Reload folder", self.reload_folder, ["F5"])
        self.quit_act = self._action("&Quit", self.close, ["Ctrl+Q"])
        self.recent_folders_menu = QMenu("Recent &folders", self)
        self.recent_videos_menu = QMenu("Recent v&ideos", self)
        self.recent_folders_menu.aboutToShow.connect(lambda: self._fill_recent(self.recent_folders_menu, "folders"))
        self.recent_videos_menu.aboutToShow.connect(lambda: self._fill_recent(self.recent_videos_menu, "videos"))
        file_menu = mb.addMenu("&File")
        for a in (self.open_folder_act, self.open_video_act, self.reload_act):
            file_menu.addAction(a)
        file_menu.addSeparator()
        file_menu.addMenu(self.recent_folders_menu)
        file_menu.addMenu(self.recent_videos_menu)
        file_menu.addSeparator()
        file_menu.addAction(self.quit_act)

        # View
        self.explore_tab_act = self._action("&Explore tab", lambda: self.tabs.setCurrentWidget(self.explore_tab), ["Ctrl+1"])
        self.slice_tab_act = self._action("&Slice Video tab", lambda: self.tabs.setCurrentWidget(self.slice_tab), ["Ctrl+2"])
        self.mosaic_act = self._action("&Mosaic view", lambda: self.show_explore_view(0), ["Ctrl+M"])
        self.stack_act = self._action("S&tack view", lambda: self.show_explore_view(1), ["Ctrl+L"])
        self.log_act = self.log_panel.toggleViewAction()
        self.log_act.setText("&Log panel")
        self.log_act.setShortcut(QKeySequence("Ctrl+J"))
        self.addAction(self.log_act)
        view_menu = mb.addMenu("&View")
        for a in (self.explore_tab_act, self.slice_tab_act):
            view_menu.addAction(a)
        view_menu.addSeparator()
        for a in (self.mosaic_act, self.stack_act):
            view_menu.addAction(a)
        view_menu.addSeparator()
        view_menu.addAction(self.log_act)

        # Actions
        self.filter_act = self._action("&Find in mosaic", self.focus_filter, ["Ctrl+F"])
        self.send_act = self._action("Send selection to &stack", self.send_selection_to_stack, ["Ctrl+Return", "Ctrl+Enter"])
        self.slice_act = self._action("Slice &video", self.run_slice, ["Ctrl+R"])
        self.cancel_act = self._action("&Cancel jobs", self._cancel, ["Esc"])
        self.cancel_act.setEnabled(False)  # only while a job runs, so Esc isn't swallowed
        act_menu = mb.addMenu("&Actions")
        for a in (self.filter_act, self.send_act, self.slice_act):
            act_menu.addAction(a)
        act_menu.addSeparator()
        act_menu.addAction(self.cancel_act)

        # Stack (the actions live on the stack view; listed here for discovery)
        stack_menu = mb.addMenu("&Stack")
        for a in self.explore_tab.stack_view.shortcut_actions:
            stack_menu.addAction(a)
        stack_menu.setToolTipsVisible(True)
        stack_menu.setToolTip("These shortcuts work while the stack view has focus")

        # Help / Debug
        self.help_act = self._action("&Keyboard shortcuts", self.show_shortcuts, ["F1"])
        mb.addMenu("&Help").addAction(self.help_act)
        if not self.debug:
            return
        debug_menu = mb.addMenu("&Debug")
        for outcome in ("success", "failure", "error"):
            act = QAction(f"Run demo job ({outcome})", self)
            act.triggered.connect(lambda _=False, o=outcome: self.start_job(DemoJob(outcome=o)))
            debug_menu.addAction(act)

    def shortcut_groups(self) -> list[tuple[str, list[QAction]]]:
        return [
            ("File", [self.open_folder_act, self.open_video_act, self.reload_act, self.quit_act]),
            ("View", [self.explore_tab_act, self.slice_tab_act, self.mosaic_act, self.stack_act, self.log_act]),
            ("Actions", [self.filter_act, self.send_act, self.slice_act, self.cancel_act, self.help_act]),
            ("Stack view (when it has focus)", self.explore_tab.stack_view.shortcut_actions),
        ]

    def show_shortcuts(self) -> ShortcutsDialog:
        dlg = ShortcutsDialog(self.shortcut_groups(), self)
        if self.show_dialogs:
            dlg.exec()
        return dlg

    # ----- action slots ----------------------------------------------------
    def _guard_busy(self) -> bool:
        """True (and a status message) if a job is running."""
        if self.runner.is_busy():
            self.status_lbl.setText("A job is running — wait for it to finish or press Esc to cancel.")
            return True
        return False

    def browse_folder(self) -> None:
        if self._guard_busy():
            return
        self.tabs.setCurrentWidget(self.explore_tab)
        self.explore_tab._browse()

    def browse_video(self) -> None:
        if self._guard_busy():
            return
        self.tabs.setCurrentWidget(self.slice_tab)
        self.slice_tab._browse_video()

    def reload_folder(self) -> None:
        if self.explore_tab.dir_edit.text() and not self._guard_busy():
            self.tabs.setCurrentWidget(self.explore_tab)
            self.explore_tab.request_load()

    def show_explore_view(self, index: int) -> None:
        self.tabs.setCurrentWidget(self.explore_tab)
        (self.explore_tab.mosaic_radio if index == 0 else self.explore_tab.stack_radio).setChecked(True)
        self.explore_tab.views.setCurrentIndex(index)
        if index == 1:
            self.explore_tab.stack_view.layer_list.setFocus()

    def focus_filter(self) -> None:
        self.show_explore_view(0)
        self.explore_tab.mosaic.filter_edit.setFocus()
        self.explore_tab.mosaic.filter_edit.selectAll()

    def send_selection_to_stack(self) -> None:
        if self.tabs.currentWidget() is self.explore_tab and self.explore_tab.views.currentIndex() == 0:
            self.explore_tab.mosaic._send_selected()

    def run_slice(self) -> None:
        if self.slice_tab.run_btn.isEnabled():
            self.tabs.setCurrentWidget(self.slice_tab)
            self.slice_video()

    # ----- recent items ------------------------------------------------------
    def _fill_recent(self, menu: QMenu, kind: str) -> None:
        menu.clear()
        items = self.settings.recent(kind)
        if not items:
            menu.addAction("(none)").setEnabled(False)
            return
        for i, path in enumerate(items):
            label = f"&{i + 1}  {path}" if i < 9 else path
            a = menu.addAction(label)
            a.triggered.connect(lambda _=False, p=path, k=kind: self.open_recent(k, p))
        menu.addSeparator()
        menu.addAction("Clear list").triggered.connect(lambda: self.settings.clear_recent(kind))

    def open_recent(self, kind: str, path: str) -> None:
        if self._guard_busy():
            return
        if kind == "folders":
            self.open_in_explore(path)
        else:
            self.open_video(path)

    # ======================================================================
    # drag and drop
    # ======================================================================
    @staticmethod
    def _local_paths(event) -> list[str]:
        md = event.mimeData()
        if not md.hasUrls():
            return []
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile() and u.toLocalFile()]

    def classify_paths(self, paths: list[str]) -> tuple[str, list[str]]:
        """Decide what a drop means: ('folder'|'video'|'images'|'', paths)."""
        folders = [p for p in paths if os.path.isdir(p)]
        if folders:
            return "folder", folders[:1]
        files = [p for p in paths if os.path.isfile(p)]
        videos = [p for p in files if os.path.splitext(p)[1].lower() in VIDEO_EXTS]
        if videos:
            return "video", videos[:1]
        image_exts = supported_extensions()
        images = [p for p in files if os.path.splitext(p)[1].lower() in image_exts]
        if images:
            return "images", images
        return "", []

    def handle_paths(self, paths: list[str]) -> str:
        """Open dropped paths. Returns what was done (for status + tests)."""
        kind, targets = self.classify_paths(paths)
        if not kind:
            self.status_lbl.setText("Nothing to open — drop a folder, a video or image files.")
            return ""
        if self._guard_busy():
            return "busy"
        if len(paths) > len(targets) and kind != "images":
            self.log_panel.append(f"Opened {os.path.basename(targets[0])}; ignored {len(paths) - 1} other dropped item(s).")
        if kind == "folder":
            self.open_in_explore(targets[0])
        elif kind == "video":
            self.open_video(targets[0])
        else:
            self.show_explore_view(1)
            self.explore_tab.stack_view.add_paths(targets)
        return kind

    def _overlay_text(self, kind: str, n: int) -> str:
        return {
            "folder": "Drop to open this folder in Explore",
            "video": "Drop to open this video in Slice Video",
            "images": f"Drop to add {n} image(s) to the stack",
        }[kind]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        kind, targets = self.classify_paths(self._local_paths(event))
        if not kind:
            event.ignore()
            return
        event.acceptProposedAction()
        self.drop_overlay.setText(self._overlay_text(kind, len(targets)))
        self.drop_overlay.setGeometry(self.centralWidget().geometry().adjusted(12, 12, -12, -12))
        self.drop_overlay.raise_()
        self.drop_overlay.show()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.drop_overlay.hide()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self.drop_overlay.hide()
        if self.handle_paths(self._local_paths(event)):
            event.acceptProposedAction()

    # ======================================================================
    # opening things
    # ======================================================================
    def open_in_explore(self, directory: str) -> None:
        self.show_explore_view(0)
        self.explore_tab.set_directory(directory)

    def open_video(self, path: str) -> None:
        self.tabs.setCurrentWidget(self.slice_tab)
        self.slice_tab.set_video(path)

    # ======================================================================
    # job wiring
    # ======================================================================
    def _wire_runner(self) -> None:
        r = self.runner
        r.job_started.connect(self._on_started)
        r.job_progress.connect(self._on_progress)
        r.job_eta.connect(lambda _job, s: self.eta_lbl.setText(f"≈ {format_duration(s)} left"))
        r.job_message.connect(lambda job, m: self.log_panel.append(f"{job.title}: {m}"))
        r.job_success.connect(self._on_success)
        r.job_failure.connect(self._on_failure)
        r.job_error.connect(self._on_error)
        r.busy_changed.connect(self._on_busy_changed)

    def start_job(self, job: Job) -> Job:
        return self.runner.start(job)

    # ----- pipeline 1: load images ---------------------------------------
    def load_images(self, directory: str, recursive: bool = False) -> LoadImagesJob:
        job = LoadImagesJob(directory, recursive=recursive)
        job.signals.item.connect(self.explore_tab.model.add_records)
        job.signals.success.connect(self._on_images_loaded)
        self.tabs.setCurrentWidget(self.explore_tab)
        return self.start_job(job)

    def load_layers(self, paths: list[str]) -> LoadLayersJob:
        stack = self.explore_tab.stack_view
        job = LoadLayersJob(paths)
        job.signals.item.connect(stack.add_layer_image)
        job.signals.finished.connect(stack.batch_finished)
        return self.start_job(job)

    def _on_images_loaded(self, result: dict) -> None:
        self.explore_tab.mosaic.set_skipped(len(result["skipped"]))
        self.settings.add_recent("folders", result["directory"])

    # ----- pipeline 2: video ---------------------------------------------
    def probe_video(self, path: str) -> ProbeVideoJob:
        job = ProbeVideoJob(path)
        job.signals.success.connect(self.slice_tab.set_probe)
        job.signals.success.connect(lambda r: self.settings.add_recent("videos", r.info.path))
        return self.start_job(job)

    def slice_video(self) -> SliceVideoJob | None:
        tab = self.slice_tab
        if tab.probe is None or tab.plan is None or self._guard_busy():
            return None
        job = SliceVideoJob(tab.probe.info, tab.plan, tab.output_dir(), overwrite=tab.overwrite_chk.isChecked())
        tab.start_run()
        job.signals.item.connect(tab.show_preview)
        job.signals.success.connect(tab.show_result)
        job.signals.failure.connect(tab.show_stopped)
        job.signals.error.connect(lambda m, _tb: tab.show_stopped(f"Unexpected error: {m}"))
        return self.start_job(job)

    # ----- job feedback --------------------------------------------------
    def _on_started(self, job: Job) -> None:
        self.status_lbl.setText(f"{job.title}…")
        self.log_panel.append(f"{job.title} started")
        self.progress.setRange(0, 0)  # busy indicator until first progress
        self.eta_lbl.setText("")

    def _on_progress(self, job: Job, done: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.progress.setFormat(f"{done} / {total}")

    def _on_success(self, job: Job, result: object) -> None:
        summary = job.summarize(result)
        self.status_lbl.setText(summary)
        self.log_panel.append(summary, "success")

    def _on_failure(self, job: Job, message: str) -> None:
        self.status_lbl.setText(f"{job.title}: {message}")
        self.log_panel.append(f"{job.title} failed: {message}", "failure")
        if self.show_dialogs and not message.startswith("Cancelled"):
            QMessageBox.warning(self, job.title, message)

    def _on_error(self, job: Job, message: str, tb: str) -> None:
        self.status_lbl.setText(f"{job.title}: unexpected error")
        self.log_panel.append(f"{job.title} error: {message}", "error")
        if self.show_dialogs:
            box = QMessageBox(QMessageBox.Critical, job.title, f"An unexpected error occurred:\n{message}", parent=self)
            box.setDetailedText(tb)
            box.exec()

    def _on_busy_changed(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        self.cancel_btn.setEnabled(True)
        if not busy:
            self.eta_lbl.setText("")
        self.explore_tab.set_busy(busy)
        self.slice_tab.set_busy(busy)
        for a in (self.open_folder_act, self.open_video_act, self.reload_act):
            a.setEnabled(not busy)
        self.cancel_act.setEnabled(busy)

    def _cancel(self) -> None:
        if not self.runner.is_busy():
            return
        self.cancel_btn.setEnabled(False)
        self.status_lbl.setText("Cancelling…")
        self.runner.cancel_all()

    # ======================================================================
    # settings
    # ======================================================================
    def save_state(self) -> None:
        s = self.settings
        s.set("window/geometry", self.saveGeometry())
        s.set("window/state", self.saveState())
        s.set("window/tab", self.tabs.currentIndex())

        ex = self.explore_tab
        s.set("explore/folder", ex.dir_edit.text())
        s.set("explore/recursive", ex.recursive_chk.isChecked())
        s.set("explore/view", ex.views.currentIndex())
        s.set("mosaic/thumb_size", ex.mosaic.size_slider.value())
        s.set("mosaic/sort", ex.mosaic.sort_combo.currentText())
        s.set("mosaic/descending", ex.mosaic.order_btn.isChecked())
        s.set("stack/align", ex.stack_view.align_combo.currentIndex())

        sl = self.slice_tab
        s.set("slice/interval", sl.interval_spin.value())
        s.set("slice/format", sl.format_combo.currentText())
        s.set("slice/jpeg_quality", sl.quality_spin.value())
        video = sl.video_edit.text()
        if video:
            s.set("slice/last_video_dir", os.path.dirname(video))
        s.sync()

    def restore_state(self) -> None:
        s = self.settings
        if (g := s.get_bytes("window/geometry")) is not None:
            self.restoreGeometry(g)
        if (st := s.get_bytes("window/state")) is not None:
            self.restoreState(st)
        self.tabs.setCurrentIndex(min(max(s.get_int("window/tab", 0), 0), self.tabs.count() - 1))

        ex = self.explore_tab
        folder = s.get_str("explore/folder")
        if folder and os.path.isdir(folder):
            ex.set_directory(folder, load=False)  # F5 / Load to open it; no surprise work at start-up
            ex.last_browse_dir = folder
        ex.recursive_chk.setChecked(s.get_bool("explore/recursive", False))
        view = 1 if s.get_int("explore/view", 0) == 1 else 0
        (ex.stack_radio if view else ex.mosaic_radio).setChecked(True)
        ex.views.setCurrentIndex(view)
        m = ex.mosaic
        m.size_slider.setValue(s.get_int("mosaic/thumb_size", m.size_slider.value()))
        sort = s.get_str("mosaic/sort", "Name")
        if m.sort_combo.findText(sort) >= 0:
            m.sort_combo.setCurrentText(sort)
        m.order_btn.setChecked(s.get_bool("mosaic/descending", False))
        ex.stack_view.align_combo.setCurrentIndex(1 if s.get_int("stack/align", 0) == 1 else 0)

        sl = self.slice_tab
        sl.interval_spin.setValue(max(1, s.get_int("slice/interval", sl.interval_spin.value())))
        fmt = s.get_str("slice/format", "JPEG")
        if sl.format_combo.findText(fmt) >= 0:
            sl.format_combo.setCurrentText(fmt)
        sl.quality_spin.setValue(s.get_int("slice/jpeg_quality", sl.quality_spin.value()))
        sl.last_browse_dir = s.get_str("slice/last_video_dir")

        if folder and os.path.isdir(folder):
            self.status_lbl.setText(f"Last folder: {folder} — press F5 to load it")

    # ======================================================================
    def event(self, e: QEvent) -> bool:
        # keep the drop overlay sized if the window is resized mid-drag
        if e.type() == QEvent.Resize and self.drop_overlay.isVisible():
            self.drop_overlay.setGeometry(self.centralWidget().geometry().adjusted(12, 12, -12, -12))
        return super().event(e)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.runner.is_busy():
            self.status_lbl.setText("Stopping background jobs…")
            self.runner.shutdown(timeout_ms=3000)
        self.save_state()
        super().closeEvent(event)
