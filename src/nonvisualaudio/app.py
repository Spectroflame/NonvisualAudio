"""Application entry point."""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtWidgets import QApplication

from nonvisualaudio.ui.main_window import MainWindow


def _configure_logging() -> None:
    """Enable structured logging when NVA_DEBUG=1.

    All UI events, analysis steps, and subprocess invocations are logged to
    stderr with timestamps so an external watcher can follow the live stream.
    """
    debug = os.environ.get("NVA_DEBUG", "").strip() not in ("", "0", "false", "False")
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
        force=True,
    )
    if debug:
        logging.getLogger("nonvisualaudio").info("Debug logging enabled (NVA_DEBUG=1).")


def main() -> int:
    _configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("NonvisualAudio")
    app.setOrganizationName("NonvisualAudio")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
