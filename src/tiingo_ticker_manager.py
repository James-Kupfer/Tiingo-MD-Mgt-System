"""
tiingo_ticker_manager.py
------------------------
Downloads, extracts, and filters the Tiingo supported-tickers list,
producing Tickers_to_Update.csv and active_list.csv.
"""

import csv
import logging
import shutil
import sys
from datetime import datetime, timedelta
from urllib.request import urlopen
from zipfile import ZipFile

try:
    from config import (
        TIINGO_TICKER_LIST_URL,
        FORCE_DOWNLOAD_SYMBOLS,
        SUPPORTED_TICKERS_ZIP,
        SUPPORTED_TICKERS_DIR,
        TICKERS_TO_UPDATE_FILE,
        PRICE_CURRENCY,
        ASSET_TYPES,
        EXCHANGES,
        EXCHANGES_STARTSWITH,
        EXCLUDED_CHARS,
        EXCLUDE_TICKERS,
        DOWNLOAD_TIMEOUT,
        LOG_LEVEL,
        LOG_FILE,
    )
except ImportError:
    print("ERROR: config.py not found. Ensure it is in the src/ directory.")
    sys.exit(1)


def setup_logging() -> logging.Logger:
    log_format = "%(asctime)s | %(levelname)-8s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    root.handlers.clear()

    fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter(log_format, date_format))
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter(log_format, date_format))
    root.addHandler(ch)

    return root


logger = setup_logging()


def download_ticker_list() -> bool:
    logger.info("=" * 70)
    logger.info("DOWNLOADING TIINGO SUPPORTED TICKERS LIST")
    logger.info("=" * 70)
    try:
        logger.info(f"Downloading from: {TIINGO_TICKER_LIST_URL}")
        with urlopen(TIINGO_TICKER_LIST_URL, timeout=DOWNLOAD_TIMEOUT) as response:
            zip_data = response.read()

        logger.info(f"Downloaded {len(zip_data) / 1024:.1f} KB")
        SUPPORTED_TICKERS_ZIP.parent.mkdir(parents=True, exist_ok=True)

        with open(SUPPORTED_TICKERS_ZIP, "wb") as f:
            f.write(zip_data)

        logger.info(f"Saved to: {SUPPORTED_TICKERS_ZIP}")
        return True
    except Exception as e:
        logger.error(f"Failed to download ticker list: {e}", exc_info=True)
        return False


def extract_ticker_list() -> bool:
    try:
        logger.info(f"Extracting ZIP to: {SUPPORTED_TICKERS_DIR}")
        if SUPPORTED_TICKERS_DIR.exists():
            shutil.rmtree(SUPPORTED_TICKERS_DIR)

        with ZipFile(SUPPORTED_TICKERS_ZIP, "r") as zip_ref:
            zip_ref.extractall(SUPPORTED_TICKERS_DIR)

        logger.info("Extraction successful")
        return True
    except Exception as e:
        logger.error(f"Failed to extract ZIP: {e}", exc_info=True)
        return False


def has_excluded_chars(ticker: str) -> bool:
    return any(c.isdigit() for c in ticker) or any(c in EXCLUDED_CHARS for c in ticker)


def is_valid_ticker(
    ticker: str,
    asset_type: str,
    exchange: str,
    price_currency: str,
    start_date: str,
    end_date: str,
) -> bool:
    if ticker.upper() in FORCE_DOWNLOAD_SYMBOLS:
        logger.info(f"Force downloading: {ticker} (bypasses all filters)")
        return True
    if ticker.upper() in EXCLUDE_TICKERS:
        return False
    if not start_date or not start_date.strip():
        return False
    if price_currency != PRICE_CURRENCY:
        return False
    if asset_type not in ASSET_TYPES:
        return False
    if exchange not in EXCHANGES and not exchange.startswith(EXCHANGES_STARTSWITH):
        return False
    if has_excluded_chars(ticker):
        return False
    return True


def process_ticker_list() -> bool:
    logger.info("=" * 70)
    logger.info("PROCESSING TICKER LIST")
    logger.info("=" * 70)
    logger.info(f"Force download symbols: {', '.join(sorted(FORCE_DOWNLOAD_SYMBOLS))}")

    try:
        csv_files = list(SUPPORTED_TICKERS_DIR.glob("*.csv"))
        if not csv_files:
            logger.error(f"No CSV files found in {SUPPORTED_TICKERS_DIR}")
            return False

        source_csv = csv_files[0]
        logger.info(f"Processing: {source_csv}")

        valid_tickers = []
        total = 0
        filtered = 0
        force_count = 0
        missing_start = 0
        missing_end = 0
        max_end_date = None

        with open(source_csv, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                total += 1
                ticker = row.get("ticker", "").strip()
                asset_type = row.get("assetType", "").strip()
                exchange = row.get("exchange", "").strip()
                currency = row.get("priceCurrency", "").strip()
                start_date = row.get("startDate", "").strip()
                end_date = row.get("endDate", "").strip()

                if ticker.upper() in FORCE_DOWNLOAD_SYMBOLS:
                    valid_tickers.append(row)
                    force_count += 1
                    if end_date and (max_end_date is None or end_date > max_end_date):
                        max_end_date = end_date
                    continue

                if ticker.upper() in EXCLUDE_TICKERS:
                    filtered += 1
                    continue

                if not start_date:
                    missing_start += 1
                    filtered += 1
                    continue

                if not end_date:
                    missing_end += 1

                if is_valid_ticker(ticker, asset_type, exchange, currency, start_date, end_date):
                    valid_tickers.append(row)
                    if end_date and (max_end_date is None or end_date > max_end_date):
                        max_end_date = end_date
                else:
                    filtered += 1

        logger.info(f"Total tickers in source:          {total}")
        logger.info(f"Force-included (filter bypassed): {force_count}")
        logger.info(f"Missing start date (excluded):    {missing_start}")
        logger.info(f"Missing end date (kept - active): {missing_end}")
        logger.info(f"Other filtered out:               {filtered - missing_start}")
        logger.info(f"Valid tickers:                    {len(valid_tickers)}")

        if not valid_tickers:
            logger.error("No valid tickers found after filtering")
            return False

        valid_tickers.sort(key=lambda x: x.get("ticker", "").upper())

        fieldnames = list(valid_tickers[0].keys())
        with open(TICKERS_TO_UPDATE_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(valid_tickers)
        logger.info(f"Saved all valid tickers to: {TICKERS_TO_UPDATE_FILE}")

        cutoff_date = None
        if max_end_date:
            try:
                cutoff_date = (
                    datetime.strptime(max_end_date, "%Y-%m-%d") - timedelta(days=7)
                ).strftime("%Y-%m-%d")
                logger.info(f"Active cutoff: {cutoff_date} (7 days before {max_end_date})")
            except ValueError:
                logger.warning(f"Could not parse max_end_date: {max_end_date}")

        active_tickers = [
            row for row in valid_tickers
            if (
                row.get("ticker", "").upper() in FORCE_DOWNLOAD_SYMBOLS
                or not row.get("endDate", "").strip()
                or (cutoff_date and row.get("endDate", "").strip() >= cutoff_date)
                or (not cutoff_date and row.get("endDate", "").strip() == max_end_date)
            )
        ]

        active_file = TICKERS_TO_UPDATE_FILE.with_name("active_list.csv")
        with open(active_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(active_tickers)
        logger.info(f"Saved active tickers to: {active_file}")

        logger.info("=" * 70)
        logger.info("SUMMARY")
        logger.info("=" * 70)
        logger.info(f"Tickers_to_Update.csv : {len(valid_tickers)} (all valid)")
        logger.info(f"active_list.csv         : {len(active_tickers)} (actively trading)")
        logger.info(f"Delisted / inactive   : {len(valid_tickers) - len(active_tickers)}")
        logger.info("=" * 70)
        return True

    except Exception as e:
        logger.error(f"Failed to process ticker list: {e}", exc_info=True)
        return False


def main() -> int:
    try:
        if not download_ticker_list():
            logger.critical("Failed to download ticker list.")
            return 1
        if not extract_ticker_list():
            logger.critical("Failed to extract ticker list.")
            return 1
        if not process_ticker_list():
            logger.critical("Failed to process ticker list.")
            return 1

        logger.info("=" * 70)
        logger.info("TICKER LIST PROCESSING COMPLETE")
        logger.info("=" * 70)
        return 0
    except KeyboardInterrupt:
        logger.warning("Process interrupted by user.")
        return 1
    except Exception as e:
        logger.critical(f"Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
