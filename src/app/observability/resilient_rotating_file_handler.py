"""Rotating diagnostics handler that remains writable under Windows readers."""
from __future__ import annotations

import logging
import logging.handlers
import os
import shutil
import time
from pathlib import Path


class ResilientRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Rotate normally, then copy/truncate if another process blocks rename.

    Windows readers commonly open log files without ``FILE_SHARE_DELETE``.
    The standard handler then drops every record once ``maxBytes`` is reached
    because each attempted rename raises ``PermissionError``. Copying the
    completed segment and truncating the base file preserves both boundedness
    and continued diagnostics without requiring readers to close first.
    """

    _ROLLOVER_RETRY_SECONDS = 60.0

    def __init__(
        self,
        filename: str | os.PathLike[str],
        mode: str = "a",
        maxBytes: int = 0,
        backupCount: int = 0,
        encoding: str | None = None,
        delay: bool = False,
        errors: str | None = None,
    ) -> None:
        super().__init__(filename, mode, maxBytes, backupCount, encoding, delay, errors)
        self._retry_rollover_after = 0.0

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # noqa: N802
        if time.monotonic() < self._retry_rollover_after:
            return False
        return bool(super().shouldRollover(record))

    def doRollover(self) -> None:  # noqa: N802
        try:
            super().doRollover()
            self._retry_rollover_after = 0.0
            return
        except PermissionError:
            # ``RotatingFileHandler`` closes its stream before renaming. The
            # backup cascade has also already run, so ``.1`` is the correct
            # destination for a copy fallback.
            self.stream = None

        source = Path(self.baseFilename)
        destination = Path(f"{self.baseFilename}.1")
        try:
            shutil.copyfile(source, destination)
            with source.open("r+b") as handle:
                handle.truncate(0)
            self._retry_rollover_after = 0.0
        except OSError:
            # Observability is more important than a hard size cap. Reopen in
            # append mode and retry rotation later instead of dropping every
            # subsequent record at the boundary.
            self._retry_rollover_after = time.monotonic() + self._ROLLOVER_RETRY_SECONDS
        finally:
            if not self.delay:
                self.stream = self._open()
