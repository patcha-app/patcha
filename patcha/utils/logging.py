import logging
import logging.handlers
from pathlib import Path


def init_logging(level=logging.INFO):
    log_dir = Path.home() / "Library" / "Logs" / "patcha"
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S"
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "patcha.log", maxBytes=8388608, backupCount=5
    )
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)

    if level <= logging.DEBUG:
        stderr_handler = logging.StreamHandler()
        stderr_handler.setFormatter(fmt)
        root.addHandler(stderr_handler)
