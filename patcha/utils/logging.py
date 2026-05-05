import logging
import logging.handlers
from pathlib import Path

_LEVEL_COLORS = {
    logging.DEBUG: "\033[33m",  # yellow
    logging.INFO: "\033[32m",  # green
    logging.WARNING: "\033[33m",  # yellow
    logging.ERROR: "\033[31m",  # red
    logging.CRITICAL: "\033[31m",  # red
}
_RESET = "\033[0m"


class _ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname}{_RESET}"
        return super().format(record)


def init_logging(level=logging.INFO):
    log_dir = Path.home() / "Library" / "Logs" / "patcha"
    log_dir.mkdir(parents=True, exist_ok=True)

    plain_fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    color_fmt = _ColorFormatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "patcha.log", maxBytes=8388608, backupCount=5
    )
    file_handler.setFormatter(plain_fmt)

    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(color_fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)
