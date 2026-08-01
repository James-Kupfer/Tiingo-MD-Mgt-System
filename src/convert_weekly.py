"""
convert_weekly.py
------------------
Converts daily OHLCV CSVs (as produced by clean_prices.py) into weekly bars.

Weeks are grouped by ISO calendar week (Monday-Sunday). Each weekly bar:
  open       = first trading day's open that week
  high       = max high that week
  low        = min low that week
  close      = last trading day's close that week
  volume     = sum of volume that week
  adjOpen/adjHigh/adjLow/adjClose/adjVolume = same aggregation, adjusted series
  divCash    = sum of dividends paid that week
  splitFactor = product of that week's split factors (usually 1.0; a week
                with a split multiplies through)
  date       = the week's last trading day (week-ending convention)
  Repaired   = "True" if any underlying daily row was "True", else ""
    (audit flag carried forward, not recomputed -- a week built from a
    repaired day is itself only as trustworthy as that repair)

Reads the same schema clean_prices.py writes (see OUTPUT_COLUMNS there) and
writes the identical column set, so weekly files are drop-in compatible with
anything that reads the daily Clean/ output.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import date
from pathlib import Path

try:
    from config import CLEAN_DATA_DIR, WEEKLY_DATA_DIR
except ImportError:
    print("ERROR: config.py not found in the src/ directory.")
    sys.exit(1)

try:
    from clean_prices import OUTPUT_COLUMNS
except ImportError:
    print("ERROR: clean_prices.py not found in the src/ directory.")
    sys.exit(1)

logger = logging.getLogger(__name__)


def group_by_iso_week(rows: list[dict]) -> list[list[dict]]:
    """Group daily rows (sorted ascending by date) into ISO calendar weeks
    (Monday-Sunday), preserving chronological order across weeks and within
    each week.
    """
    weeks: dict[tuple[int, int], list[dict]] = {}
    for row in rows:
        iso_year, iso_week, _ = date.fromisoformat(row["date"]).isocalendar()
        weeks.setdefault((iso_year, iso_week), []).append(row)
    return [weeks[key] for key in sorted(weeks)]


def aggregate_week(week_rows: list[dict]) -> dict:
    """Roll one ISO week's daily rows up into a single weekly bar.
    week_rows must be sorted ascending by date.
    """
    first, last = week_rows[0], week_rows[-1]

    split_factor = 1.0
    for r in week_rows:
        split_factor *= float(r["splitFactor"])

    return {
        "symbol": first["symbol"],
        "date": last["date"],
        "open": first["open"],
        "high": repr(max(float(r["high"]) for r in week_rows)),
        "low": repr(min(float(r["low"]) for r in week_rows)),
        "close": last["close"],
        "volume": str(int(sum(float(r["volume"]) for r in week_rows))),
        "adjOpen": first["adjOpen"],
        "adjHigh": repr(max(float(r["adjHigh"]) for r in week_rows)),
        "adjLow": repr(min(float(r["adjLow"]) for r in week_rows)),
        "adjClose": last["adjClose"],
        "adjVolume": str(int(sum(float(r["adjVolume"]) for r in week_rows))),
        "divCash": repr(sum(float(r["divCash"]) for r in week_rows)),
        "splitFactor": repr(split_factor),
        "Repaired": "True" if any(r.get("Repaired") == "True" for r in week_rows) else "",
    }


def convert_file_to_weekly(input_path: Path, output_path: Path) -> int:
    """Convert one daily symbol CSV to weekly bars. Returns the number of
    weekly bars written. Raises ValueError if the file has no rows.
    """
    with input_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"{input_path.name}: no rows")

    rows.sort(key=lambda r: r["date"])
    weekly_rows = [aggregate_week(week) for week in group_by_iso_week(rows)]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(weekly_rows)

    return len(weekly_rows)


def convert_directory(
    input_dir: Path,
    output_dir: Path,
    symbols: list[str] | None = None,
) -> None:
    """Convert every {SYMBOL}.csv in input_dir to weekly bars in output_dir.
    If symbols is given, only those tickers are converted (missing files are
    logged and skipped, not an error) -- otherwise every *.csv in input_dir
    (excluding the _reports subdirectory, which isn't ticker data) is done.
    """
    if symbols:
        files = []
        for s in symbols:
            path = input_dir / f"{s.upper()}.csv"
            if path.exists():
                files.append(path)
            else:
                logger.warning("Skipping %s: no %s in %s", s.upper(), path.name, input_dir)
    else:
        files = sorted(input_dir.glob("*.csv"))

    logger.info("Converting %d file(s) from %s -> %s", len(files), input_dir, output_dir)

    n_ok = 0
    n_failed = 0
    for path in files:
        try:
            n_weeks = convert_file_to_weekly(path, output_dir / path.name)
            logger.info("%s: %d weekly bar(s) written", path.stem, n_weeks)
            n_ok += 1
        except (ValueError, OSError) as exc:
            logger.error("Failed to convert %s: %s", path.name, exc)
            n_failed += 1

    logger.info("Done: %d symbol(s) converted, %d failed.", n_ok, n_failed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=CLEAN_DATA_DIR,
                         help=f"Directory of daily cleaned CSVs (default: {CLEAN_DATA_DIR})")
    parser.add_argument("--output", type=Path, default=WEEKLY_DATA_DIR,
                         help=f"Directory to write weekly CSVs (default: {WEEKLY_DATA_DIR})")
    parser.add_argument("--symbols", nargs="+", default=None,
                         help="Specific tickers to convert (default: every *.csv in --input)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    convert_directory(args.input, args.output, symbols=args.symbols)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
