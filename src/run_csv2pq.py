"""
run_csv2pq.py
-------------
Runs ``csv2pq --config <toml>`` with its output captured line-by-line into the
run log (``logs\\YYYY-MM-DD-HH-MM_csv2pq.txt``), so conversion errors are kept
after the console window closes. Exit code mirrors csv2pq's (1 if it could not
be launched), so the calling .bat can branch on it.

Usage: python run_csv2pq.py --config <path to convert_tiingo.toml>
"""

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from log_utils import setup_logging

logger = logging.getLogger(__name__)

CSV2PQ_EXE = "csv2pq"


def run_csv2pq(config: Path) -> int:
    """Run csv2pq against config, logging every output line; return its exit code.

    Returns 1 without launching if csv2pq is not on PATH or config is missing;
    both are logged with the PATH/path involved.
    """
    exe = shutil.which(CSV2PQ_EXE)
    if exe is None:
        logger.error(
            "%s not found on PATH; is the csv_to_pq environment active?", CSV2PQ_EXE
        )
        return 1
    if not config.is_file():
        logger.error("csv2pq config not found: %s", config)
        return 1

    command = [exe, "--config", str(config)]
    logger.info("Running: %s", command)
    try:
        with subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as proc:
            for line in proc.stdout:
                logger.info("csv2pq: %s", line.rstrip())
    except OSError as exc:
        logger.error("Could not launch %s: %s", command, exc, exc_info=True)
        return 1

    level = logging.INFO if proc.returncode == 0 else logging.ERROR
    logger.log(level, "csv2pq exited with code %d", proc.returncode)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: set up logging, run csv2pq, return its exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, required=True, help="csv2pq TOML config path"
    )
    args = parser.parse_args(argv)

    log_path = setup_logging("csv2pq")
    code = run_csv2pq(args.config)
    logger.info("csv2pq step finished with code %d; log: %s", code, log_path)
    return code


if __name__ == "__main__":
    sys.exit(main())
