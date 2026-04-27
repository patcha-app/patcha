import logging
import logging.handlers
from pathlib import Path

def init_logging(level=logging.INFO):
    dir = Path.home() / "Library" / "Logs" / "memorai"
    dir.mkdir(parents= True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        dir / "memorai.log", maxBytes=8388608, backupCount=5
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S"
    ))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
