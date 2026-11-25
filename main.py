"""Entry point.

    python main.py               # normal start
    python main.py --debug       # also show the Debug menu (demo jobs)
    python main.py --self-test   # run a headless-friendly smoke test and exit 0/1
    python main.py --version
"""
import argparse
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app import __version__
from app.resources import APP_NAME, ORG_NAME, resource_path


def parse_args(argv):
    p = argparse.ArgumentParser(prog="image-explorer", add_help=True)
    p.add_argument("--debug", action="store_true", help="show the Debug menu")
    p.add_argument("--self-test", action="store_true", help="run the built-in smoke test and exit")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    # parse_known_args: ignore extra arguments macOS or Qt may pass in
    args, _unknown = p.parse_known_args(argv[1:])
    return args


def main() -> int:
    args = parse_args(sys.argv)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(ORG_NAME)  # QSettings location uses these
    app.setWindowIcon(QIcon(resource_path("icon.png")))

    if args.self_test:
        from app.selftest import run

        return run()

    from app.settings import Settings
    from app.ui.main_window import MainWindow

    window = MainWindow(settings=Settings.user(), debug=args.debug)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
