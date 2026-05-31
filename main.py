import argparse
import logging
import os

from patcha.utils.logging import init_logging
from patcha.config import settings
from patcha.daemon import ActivityDaemon, daemon_status


def main():
    parser = argparse.ArgumentParser(description="patcha daemon")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Start even if another daemon appears to be running",
    )
    args = parser.parse_args()

    init_logging(level=logging.DEBUG if args.debug else logging.INFO)
    log = logging.getLogger("patcha")

    status = daemon_status()
    if status.get("running") and not args.force:
        log.error(
            "daemon already running (pid=%s); refusing to start a second instance. "
            "Stop it first or pass --force.",
            status["pid"],
        )
        return

    log.info("starting (pid=%d)", os.getpid())
    poll_interval = settings.get("poll_interval") or 60
    daemon = ActivityDaemon(poll_interval=poll_interval, batch_size=50)
    daemon.start()
    log.info("shutting down")


if __name__ == "__main__":
    main()
