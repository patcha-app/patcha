import logging
import os

from memorai.utils.logging import init_logging
from memorai.config import settings
from memorai.daemon import ActivityDaemon


def main():
    init_logging()
    log = logging.getLogger("memorai")
    log.info("starting (pid=%d)", os.getpid())
    poll_interval = settings.get("poll_interval") or 60
    daemon = ActivityDaemon(poll_interval=poll_interval, batch_size=50)
    daemon.start()
    log.info("shutting down")


if __name__ == "__main__":
    main()
