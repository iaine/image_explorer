# Image Explorer (PySide6 prototype)

Explore a folder of images as a **mosaic** or an **overlay stack**, and slice
videos into frames every *x* seconds with **OpenCV**. All heavy work runs on
background threads and reports success, failure or error to the UI.

## Run

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
python -m pytest -q     # 80 tests; 1 permissions test is skipped when run as root
```

Use the **Debug** menu to run demo jobs that succeed, fail or error, and the
**Cancel** button in the status bar to stop a running job.

## Status

| Milestone | Scope | Status |
|---|---|---|
| 1 | Skeleton, shared job contract, runner, demo job, tests | ✅ |
| 2 | Load images in background + mosaic view | ✅ |
| 3 | Stack view: overlay layers with opacity | ✅ |
| 4 | Video probe + estimate (OpenCV) | ✅ |
| 5 | Video slicing with live ETA (OpenCV) | ✅ |
| 6 | Polish: QSettings, shortcuts, drag-and-drop | ✅ |

## Command-line options

```bash
python main.py --debug       # also show the Debug menu (demo jobs)
python main.py --self-test   # smoke-test images, stack, video read + slice; exit 0/1
python main.py --version
```

## Building installers (PyInstaller)

PyInstaller doesn't cross-compile, so **build the Mac app on a Mac and the Windows app on Windows**. Each script:
1. creates its own `.venv-build`
2. installs `requirements-build.txt`
3. runs the tests (skip with `SKIP_TESTS=1`)
4. builds with `packaging/image_explorer.spec`
5. runs the **built** app's `--self-test`
6. packages the result

| | Command | Output in `dist/` |
|---|---|---|
| macOS | `./build_mac.sh` | `Image Explorer.app` + `Image-Explorer-<ver>-macOS-<arch>.dmg` |
| Windows | double-click `build_windows.bat` (or `powershell -ExecutionPolicy Bypass -File build_windows.ps1`) | `Image Explorer\Image Explorer.exe` + `Image-Explorer-<ver>-windows-x64.zip` |

**No Mac or Windows machine?** Push the project to GitHub and run the **Build** workflow (`.github/workflows/build.yml`) from the Actions tab, or push a `v*` tag. It builds Windows x64, macOS Apple Silicon and macOS Intel, and attaches the zip/dmg files to the run.

**Notes**
- **One-folder builds:** the `.exe` needs its `_internal` folder next to it. This starts faster and gets fewer antivirus false positives than a single-file exe.
- **Architecture:** the Mac app runs on the architecture it was built on (`arm64` or `x86_64`). OpenCV wheels are per-architecture, so there's no universal2 build.
- **Size:** OpenCV is most of the size, because it includes FFmpeg for video. The spec drops Qt parts the app doesn't use (QML/Quick, PDF, translations).
- **Icons:** `assets/icon.png`, `.ico` and `.icns` come from `packaging/make_icons.py`. To use your own artwork, replace `icon.png` and run `python packaging/make_icons.py --from-png`.
- **Bundle id:** change `BUNDLE_ID` in the spec to your own reverse-DNS id before distributing on macOS.

### Signing

Unsigned builds work, but other people's machines will warn:
- **macOS** Gatekeeper: right-click the app → Open.
- **Windows** SmartScreen: More info → Run anyway.

To distribute properly:
- **macOS:** `CODESIGN_IDENTITY="Developer ID Application: Name (TEAMID)" NOTARY_PROFILE=<profile> ./build_mac.sh`
  - Create the profile once with `xcrun notarytool store-credentials`.
  - This signs every binary with hardened runtime, using `packaging/entitlements.plist`, then notarizes and staples the app and signs the DMG.
- **Windows:** sign `dist\Image Explorer\Image Explorer.exe` with `signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a ...` using your code-signing certificate, then zip.

## Explore tab (milestone 2)

- **Browse…** picks a folder and starts loading straight away. **Load images** reloads it, optionally with **Include subfolders**.
- Thumbnails (up to 256 px) are made on a worker thread with `QImageReader`, with EXIF rotation applied, and stream into the mosaic in batches.
- Unreadable files are skipped and listed in the log. The run only fails if the folder is missing or empty, or no file could be read.
- Mosaic: filename filter, sort by name (natural: 2 before 10), date, size or dimensions, thumbnail size slider, and multi-select (Ctrl/Shift).
- **Send to stack** (or double-click a tile) adds images to the overlay stack.

Memory: about 200 KB per image for thumbnails, so roughly 200 MB for 1,000 images.

## Stack view (milestone 3)

- The first image selected goes on **top** (A on top of B on top of C). A new batch goes above the existing layers.
- Full-resolution layers load on a worker thread. Images longer than 4096 px are downscaled in memory but still **drawn at their true size**, so layers of different resolutions line up.
- A new stack starts with the bottom layer at 100% and the others at 50%. **Reset opacities** brings those values back.
- Layer list (top = top of stack): tick for visibility, drag or ▲/▼ to reorder, **Remove**.
- Selected layer: opacity slider, and **Solo**, which shows only this layer at 100% without changing its settings.
- Align layers by **Centre** (default) or **Top-left**. Zoom with the mouse wheel, pan by dragging, or use **Fit** / **100%**.
- A checkerboard shows wherever the combined layers aren't fully opaque.
- Limit: 20 layers. Duplicates are ignored.

## Slice Video tab (milestone 4)

- Choosing a video starts an **Analyse video** job. OpenCV reads the resolution, frame rate, duration, codec and file size.
- The output folder defaults to `<video folder>/<video name>_frames`.
- Frames are taken at 0, x, 2x, … seconds, strictly before the end of the video.
- **How the estimate works:** the probe times real work on *this* video:
  - jumping to and decoding 5 frames spread through the video (median time)
  - decoding about 30 frames in a row
  - encoding the sample frames as PNG and at several JPEG qualities (others are interpolated), and measuring their size

  `app/video/opencv_video.plan_slice()` then works out the cost of both extraction methods, picks the cheaper one, and estimates time and disk space.
- The estimate updates instantly when *x* or the format changes. There are warnings when *x* covers the whole video or the output drive is short of space.
- On a 60 s 1080p test video, the estimates were within 0.1 s of measured extraction times, and disk use matched exactly.

## Slicing (milestone 5)

- **Slice video** saves frames using the method the estimate picked, on a worker thread. The status bar shows progress and time remaining, and the Output panel previews the latest frame.
- Files are named `<video>_<index>_<HhMMmSSs>.<ext>`, e.g. `holiday_00012_0h01m00s.jpg`, so they sort by name and show their time.
- The output folder is created if needed and checked for write access first.
- If it already has frames from this video, the job stops unless **Overwrite existing frames** is ticked. Overwriting only removes this video's frames.
- **Cancel** keeps the frames already written and says how many there are.
- Write errors (e.g. disk full) become a failure message saying how many frames were saved. Frames that can't be decoded are skipped and logged.
- Frames are encoded with `cv2.imencode` and written with Python I/O, because `cv2.imwrite` fails silently on Windows paths with non-ASCII characters.
- When it finishes: **Open folder** (system file manager) or **Open in Explore** (loads the frames into the mosaic).

## Polish (milestone 6)

**Settings** are saved on exit with `QSettings`: in the registry on Windows, a plist on macOS, and `~/.config/ImageExplorer/Image Explorer.conf` on Linux. They cover:
- window size and layout, log panel, and current tab
- last image folder, subfolders option, mosaic/stack view, thumbnail size and sort
- stack alignment
- slice interval, format, JPEG quality, and the folder the video browser opens in

The last folder is filled in but **not loaded** at start-up; press F5 to load it. Missing folders and garbage values fall back to defaults.

**Recent folders / videos** (File menu, last 8) are recorded only after a successful load or analysis. Missing paths are hidden.

**Drag and drop** onto the window:
- a folder opens in Explore
- a video opens in Slice Video
- image files go onto the stack

An overlay says what will happen before you let go. If several things are dropped, a folder wins over a video, which wins over images. Drops are refused while a job is running.

**Keyboard shortcuts** (F1 shows them all):

| Keys | Action |
|---|---|
| Ctrl+O / Ctrl+Shift+O | Open image folder / video |
| F5 | Reload folder |
| Ctrl+1 / Ctrl+2 | Explore / Slice Video tab |
| Ctrl+M / Ctrl+L | Mosaic / Stack view |
| Ctrl+F | Filter the mosaic |
| Ctrl+Enter | Send selection to stack |
| Ctrl+R | Slice video |
| Esc | Cancel running jobs |
| Ctrl+J | Show/hide log |
| *Stack view focused:* Ctrl+↑/↓ | Move layer |
| H / S | Hide-show / solo layer |
| [ / ] | Opacity −/+10% |
| Delete | Remove layer |
| Ctrl+= / Ctrl+- / Ctrl+0 / Ctrl+Shift+0 | Zoom in / out / fit / 100% |

The stack's single-key shortcuts only work while the stack view has focus, so typing in the filter box is never hijacked.

## Writing a new job

```python
from app.jobs import Job, JobFailed

class MyJob(Job):
    title = "My job"

    def execute(self):
        items = ...
        if not items:
            raise JobFailed("Nothing to do")    # -> failure (warning box)
        for i, item in enumerate(items, 1):
            self.check_cancelled()              # honours Cancel
            ...                                 # anything unexpected -> error (+traceback)
            self.report_progress(i, len(items)) # throttled
        return {"count": len(items)}            # -> success

    def summarize(self, result):                # shown in status bar + log
        return f"Processed {result['count']} items"

window.start_job(MyJob())
```

Rules: never touch widgets or create `QPixmap` inside `execute`; emit
`QImage` via `self.signals.item` and convert in the GUI thread.
