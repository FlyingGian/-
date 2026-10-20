import argparse
import sys
import logging
from logging.handlers import TimedRotatingFileHandler


def main():
    parser = argparse.ArgumentParser(description="AshareQuant 桌面研究工作站")
    parser.add_argument("--data-dir")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--screenshot")
    args = parser.parse_args()
    from application.service import Service

    service = Service(args.data_dir)
    logdir = service.repo.root / "logs"
    logdir.mkdir(exist_ok=True)
    handler = TimedRotatingFileHandler(
        logdir / "asharequant.log", when="midnight", backupCount=14, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[handler],
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import QTimer
    from presentation.main import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("AshareQuant")
    app.setOrganizationName("AshareQuant")

    def error_hook(kind, value, tb):
        logging.critical("未处理异常", exc_info=(kind, value, tb))
        QMessageBox.critical(
            None,
            "AshareQuant",
            "操作出现异常，已写入日志。请到环境自检页检查依赖与数据完整性。\n" + str(value),
        )

    sys.excepthook = error_hook
    window = MainWindow(service, first_run=not args.smoke)
    window.show()
    if args.screenshot:

        def capture():
            window.grab().save(args.screenshot)
            if args.smoke:
                window.close()

        QTimer.singleShot(1800, capture)
    elif args.smoke:
        QTimer.singleShot(1800, window.close)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
