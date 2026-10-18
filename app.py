"""SmartForm AI — launch with:  python app.py"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE = Path(__file__).resolve().parent


def setup_logging() -> None:
    (BASE / "logs").mkdir(exist_ok=True)
    handler = RotatingFileHandler(BASE / "logs" / "smartform.log", maxBytes=500_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def main() -> int:
    setup_logging()
    from PySide6.QtWidgets import QApplication, QMessageBox
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    try:
        window = MainWindow(BASE)
    except Exception as exc:
        logging.exception("Startup failed")
        QMessageBox.critical(None, "SmartForm AI", f"Could not start: {exc}\n\nCheck config.json / profile.json.")
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
