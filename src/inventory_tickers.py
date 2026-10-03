"""
inventory_tickers.py
--------------------
Writes Tickers_Inventory.csv: the symbols on the Investment Portfolio
workbook's Inventory sheet, plus the DDC source symbols, in the layout of
Tickers_to_Update.csv so tiingo_data_downloader.py can consume it unchanged.

The workbook path, sheet, columns, exclusions and sources come from the DDC
project's config.toml, and rows are parsed by its workbook.py, so "which
symbols are in Inventory" has a single definition. Symbols are assumed to be
valid Tiingo tickers; startDate/endDate are left blank, so the downloader
appends to an existing raw file or pulls full history for a new symbol.
"""

import csv
import importlib.util
import logging
import re
import sys
import time
import tomllib
from pathlib import Path
from types import ModuleType

from config import (
    DDC_DIR,
    INVENTORY_READ_ATTEMPTS,
    INVENTORY_READ_RETRY_SECONDS,
    INVENTORY_TICKERS_FILE,
    LOG_FILE,
)

logger = logging.getLogger("inventory_tickers")

FIELDNAMES = [
    "ticker",
    "exchange",
    "assetType",
    "priceCurrency",
    "startDate",
    "endDate",
]

# Symbols are placed in a URL path unescaped, so anything outside this set
# (broker contract descriptors, fund-network ids with spaces) cannot be requested.
URL_SAFE_TICKER = re.compile(r"[A-Za-z0-9._-]+")


def load_ddc_workbook_module(ddc_dir: Path) -> ModuleType:
    """Import the DDC project's workbook.py under a private module name.

    Loaded by path because both projects have a top-level ``config`` module,
    so putting the DDC ``src`` directory on ``sys.path`` would shadow ours.

    Raises:
        FileNotFoundError: workbook.py is missing from ``ddc_dir\\src``.
    """
    path = ddc_dir / "src" / "workbook.py"
    if not path.is_file():
        raise FileNotFoundError(f"DDC workbook parser not found: {path}")
    spec = importlib.util.spec_from_file_location("ddc_workbook", path)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: its dataclass resolves annotations via sys.modules.
    sys.modules["ddc_workbook"] = module
    spec.loader.exec_module(module)
    return module


def collect_symbols(ddc_dir: Path) -> list[str]:
    """Return the sorted, de-duplicated symbols to download.

    Inventory rows resolve through the Mapped Symbol override, drop the DDC
    exclusion list, and are joined by the DDC source symbols (the down-day
    sample, which DDC cannot run without). Symbols that cannot be requested
    from Tiingo are logged and skipped.

    Args:
        ddc_dir: Root of the DDC project (holds config.toml and src\\workbook.py).

    Raises:
        FileNotFoundError, ValueError, KeyError: config or workbook unreadable.
    """
    settings = tomllib.loads((ddc_dir / "config.toml").read_text(encoding="utf-8"))[
        "symbols"
    ]
    parser = load_ddc_workbook_module(ddc_dir)
    rows = parser.read_inventory(
        ddc_dir / settings["workbook_path"],  # absolute paths pass through unchanged
        settings["workbook_sheet"],
        settings["symbol_column"],
        settings["mapped_symbol_column"],
    )

    def fold(text: str) -> str:
        return parser.normalize_symbol(text).casefold()

    excluded = {fold(s) for s in settings["exclude"]}
    symbols = {s.upper() for s in settings["sources"]}
    for row in rows:
        if any(fold(spelling) in excluded for spelling in row.spellings):
            continue
        symbols.add(row.effective)

    unusable = sorted(s for s in symbols if not URL_SAFE_TICKER.fullmatch(s))
    for symbol in unusable:
        logger.warning("Skipping %r: not a requestable Tiingo ticker", symbol)
    return sorted(symbols.difference(unusable))


def collect_symbols_with_retry(ddc_dir: Path) -> list[str]:
    """Run ``collect_symbols``, retrying while the workbook is unreadable.

    Only OSError/ValueError are retried (a workbook caught mid-save or held by
    Excel); a broken config or missing key fails immediately.

    Raises:
        The last OSError/ValueError once ``INVENTORY_READ_ATTEMPTS`` are spent.
    """
    for attempt in range(1, INVENTORY_READ_ATTEMPTS + 1):
        try:
            return collect_symbols(ddc_dir)
        except (OSError, ValueError) as exc:
            if attempt == INVENTORY_READ_ATTEMPTS:
                raise
            logger.warning(
                "Inventory read failed (attempt %d/%d): %s; retrying in %ds",
                attempt,
                INVENTORY_READ_ATTEMPTS,
                exc,
                INVENTORY_READ_RETRY_SECONDS,
            )
            time.sleep(INVENTORY_READ_RETRY_SECONDS)


def write_ticker_file(symbols: list[str], path: Path) -> None:
    """Write ``symbols`` to ``path`` in Tickers_to_Update.csv layout."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows({"ticker": s} for s in symbols)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    try:
        symbols = collect_symbols_with_retry(DDC_DIR)
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
        logger.error("Could not read the Inventory symbol list: %s", exc)
        return 1
    if not symbols:
        logger.error("Inventory produced no symbols; nothing to download.")
        return 1

    write_ticker_file(symbols, INVENTORY_TICKERS_FILE)
    logger.info(
        "Wrote %d Inventory symbols to %s", len(symbols), INVENTORY_TICKERS_FILE
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
