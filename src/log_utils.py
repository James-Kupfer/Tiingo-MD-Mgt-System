"""
log_utils.py
------------
Shared logging setup for the pipeline's command-line scripts: one timestamped
file per run in ``logs\\`` plus a console handler, and a retention purge.
"""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from config import CONSOLE_LOG_LEVEL, LOG_DIR, LOG_LEVEL, LOG_RETENTION_DAYS

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_SUFFIXES = (".txt", ".log")  # File types purged; the downloader still writes .log


def purge_old_logs(log_dir: Path = LOG_DIR, days: int = LOG_RETENTION_DAYS) -> int:
    """Delete log files in log_dir last modified more than ``days`` days ago.

    Returns the number of files removed. A file that cannot be deleted (e.g.
    held open by another process) is skipped and logged, never fatal.
    """
    cutoff = datetime.now() - timedelta(days=days)
    removed = 0
    for path in log_dir.iterdir() if log_dir.is_dir() else ():
        if path.suffix.lower() not in LOG_SUFFIXES or not path.is_file():
            continue
        if datetime.fromtimestamp(path.stat().st_mtime) >= cutoff:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logging.getLogger(__name__).warning("Could not purge %s: %s", path, exc)
    return removed


def setup_logging(
    log_name: str,
    console_level: str = CONSOLE_LOG_LEVEL,
    log_dir: Path = LOG_DIR,
) -> Path:
    """Route the root logger to ``log_dir/YYYY-MM-DD-HH-MM_{log_name}.txt``
    (at config LOG_LEVEL) and to stdout (at console_level), then purge logs
    past retention. Replaces any handlers already on the root logger.

    Returns the log file path so callers can point the user at it.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{datetime.now():%Y-%m-%d-%H-%M}_{log_name}.txt"
    formatter = logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT)

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.DEBUG))
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, console_level.upper(), logging.INFO))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for handler in (file_handler, console):
        handler.setFormatter(formatter)
        root.addHandler(handler)

    purged = purge_old_logs(log_dir)
    root.info(
        "Logging to %s (purged %d log file(s) older than %d days)",
        log_path,
        purged,
        LOG_RETENTION_DAYS,
    )
    return log_path
