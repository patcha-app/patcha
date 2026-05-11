import logging
from typing import Optional

logger = logging.getLogger(__name__)


class CollectorGuard:
    """Silences and disables a collector after max_failures consecutive failures.

    Usage:
        guard = CollectorGuard("safari", max_failures=3)

        if not guard.ok:
            return []
        try:
            ...
            guard.success()
        except Exception as e:
            guard.fail(e)
    """

    def __init__(self, name: str, max_failures: int = 3):
        self.name = name
        self.max_failures = max_failures
        self._failures = 0
        self._disabled = False

    @property
    def ok(self) -> bool:
        return not self._disabled

    def success(self) -> None:
        self._failures = 0

    def fail(self, exc: Exception, msg: Optional[str] = None) -> None:
        if self._disabled:
            return
        self._failures += 1
        label = msg or str(exc)
        if self._failures >= self.max_failures:
            self._disabled = True
            logger.warning(
                "%s: disabled after %d consecutive failures (last: %s). "
                "Will not retry until restart.",
                self.name,
                self._failures,
                label,
            )
        else:
            logger.warning(
                "%s: failure %d/%d — %s",
                self.name,
                self._failures,
                self.max_failures,
                label,
            )
