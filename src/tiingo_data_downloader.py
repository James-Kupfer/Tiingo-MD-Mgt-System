"""
tiingo_data_downloader.py
-------------------------
Orchestrates parallel download of daily OHLCV price data from the Tiingo
API for all tickers listed in Tickers_to_Update.csv.
"""

import csv
import logging
import sys
from datetime import datetime, timedelta

try:
    from download import download_all_tickers_parallel
except ImportError:
    print("ERROR: download.py not found in the src/ directory.")
    sys.exit(1)

try:
    from config import (
        TIINGO_BASE_URL,
        TICKERS_TO_UPDATE_FILE,
        RAW_DATA_DIR,
        APIKEY_FILE,
        MAX_REQUESTS_PER_HOUR,
        DOWNLOAD_TIMEOUT,
        MAX_RETRIES,
        RETRY_DELAY,
        LOG_LEVEL,
        CONSOLE_LOG_LEVEL,
        LOG_FILE,
        CSV_DELIMITER,
        SHOW_PROGRESS,
        DISPLAY_INTERVAL,
    )
except ImportError:
    print("ERROR: config.py not found in the src/ directory.")
    sys.exit(1)


def setup_logging() -> logging.Logger:
    log_format = "%(asctime)s | %(levelname)-8s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    log_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    log_filepath = LOG_FILE.parent / f"tiingo_download_{log_timestamp}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    fh = logging.FileHandler(log_filepath, mode="a", encoding="utf-8")
    fh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    fh.setFormatter(logging.Formatter(log_format, date_format))
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(getattr(logging, CONSOLE_LOG_LEVEL, logging.WARNING))
    ch.setFormatter(logging.Formatter(log_format, date_format))
    root.addHandler(ch)

    root.info(f"Logging to: {log_filepath}")
    print(f"Log file: {log_filepath}")
    return root


logger = setup_logging()


def load_api_key() -> str | None:
    try:
        if not APIKEY_FILE.exists():
            logger.error(f"API key file not found: {APIKEY_FILE}")
            return None

        api_key = APIKEY_FILE.read_text(encoding="utf-8").strip()
        if not api_key:
            logger.error("API key file is empty")
            return None

        logger.info("API key loaded successfully")
        return api_key
    except Exception as e:
        logger.error(f"Failed to load API key: {e}")
        return None


def load_tickers() -> list[dict]:
    try:
        if not TICKERS_TO_UPDATE_FILE.exists():
            logger.error(f"Ticker file not found: {TICKERS_TO_UPDATE_FILE}")
            logger.error("Run tiingo_ticker_manager.py first to generate this file.")
            return []

        with open(TICKERS_TO_UPDATE_FILE, "r", encoding="utf-8") as f:
            tickers = list(csv.DictReader(f))

        logger.info(f"Loaded {len(tickers)} tickers from {TICKERS_TO_UPDATE_FILE}")
        return tickers
    except Exception as e:
        logger.error(f"Failed to load tickers: {e}", exc_info=True)
        return []


def ticker_exists(ticker: str) -> bool:
    return (
        (RAW_DATA_DIR / f"{ticker}.csv").exists()
        or (RAW_DATA_DIR / f"{ticker}_dlist.csv").exists()
    )


def parse_date_flexible(value: str) -> datetime | None:
    if not value or not value.strip():
        return None

    v = value.strip()
    # Strip ISO 8601 time component if present (e.g. "2023-01-02T00:00:00+00:00")
    if "T" in v:
        v = v.split("T")[0]

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%m-%d-%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(v, fmt)
        except (ValueError, TypeError):
            continue

    return None


def get_latest_date_in_file(ticker: str) -> str | None:
    data_file = RAW_DATA_DIR / f"{ticker}.csv"
    if not data_file.exists():
        return None

    try:
        with open(data_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter=CSV_DELIMITER)
            header = next(reader, None)
            if not header:
                return None

            date_col = next(
                (i for i, c in enumerate(header) if c.strip().lower() == "date"),
                1 if header[0].strip().lower() in ("symbol", "ticker") else 0,
            )

            latest = None
            for row in reader:
                if not row or len(row) <= date_col:
                    continue
                dt = parse_date_flexible(row[date_col].strip())
                if dt and (latest is None or dt > latest):
                    latest = dt

            return latest.strftime("%Y-%m-%d") if latest else None

    except Exception as e:
        logger.warning(f"{ticker}: Failed to read dates from CSV: {e}")
        return None


def get_max_end_date(tickers: list[dict]) -> datetime | None:
    max_date = None
    for row in tickers:
        dt = parse_date_flexible(row.get("endDate", "").strip())
        if dt and (max_date is None or dt > max_date):
            max_date = dt
    return max_date


def main() -> int:
    try:
        mode = "incremental"
        if len(sys.argv) > 1:
            arg = sys.argv[1].lower()
            if arg in ("full", "incremental"):
                mode = arg
            else:
                logger.error(f"Invalid mode: {arg}. Use 'full' or 'incremental'.")
                return 1

        logger.info("=" * 70)
        logger.info(f"TIINGO MARKET DATA DOWNLOAD - MODE: {mode.upper()}")
        logger.info("=" * 70)

        tickers = load_tickers()
        api_key = load_api_key()

        if not tickers:
            logger.error("No tickers to download.")
            return 1
        if not api_key:
            logger.error("No API key available.")
            return 1

        RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

        max_end_date = get_max_end_date(tickers)
        cutoff_date = (max_end_date - timedelta(days=7)) if max_end_date else None
        if cutoff_date:
            max_str = max_end_date.strftime("%Y-%m-%d")
            cutoff_str = cutoff_date.strftime("%Y-%m-%d")
            logger.info(f"Max endDate: {max_str}, delisted cutoff: {cutoff_str}")
        else:
            logger.info("No endDate values found; delisted logic disabled.")

        config = {
            "mode": mode,
            "api_key": api_key,
            "base_url": TIINGO_BASE_URL,
            "raw_data_dir": RAW_DATA_DIR,
            "csv_delimiter": CSV_DELIMITER,
            "timeout": DOWNLOAD_TIMEOUT,
            "max_retries": MAX_RETRIES,
            "retry_delay": RETRY_DELAY,
            "max_requests_per_hour": MAX_REQUESTS_PER_HOUR,
            "today": datetime.now().strftime("%Y-%m-%d"),
            "cutoff_date": cutoff_date,
            "show_progress": SHOW_PROGRESS,
            "display_interval": DISPLAY_INTERVAL,
            "ticker_exists_func": ticker_exists,
            "get_latest_date_func": get_latest_date_in_file,
            "parse_date_func": parse_date_flexible,
        }

        total, successful, failed, skipped, request_count, elapsed = (
            download_all_tickers_parallel(tickers, config)
        )

        logger.info("=" * 70)
        logger.info("DOWNLOAD COMPLETE")
        logger.info("=" * 70)
        logger.info(f"Total tickers : {total}")
        logger.info(f"Successful    : {successful}")
        logger.info(f"Failed        : {failed}")
        logger.info(f"Skipped       : {skipped}")
        logger.info(f"API requests  : {request_count}")
        logger.info(f"Elapsed       : {elapsed:.1f}s ({elapsed / 60:.1f} min)")
        if request_count > 0:
            logger.info(f"Avg per request: {elapsed / request_count:.2f}s")

        if failed > 0:
            logger.warning(f"{failed} ticker(s) failed — check log for details.")
            return 1
        return 0

    except KeyboardInterrupt:
        logger.warning("Process interrupted by user.")
        return 1
    except Exception as e:
        logger.critical(f"Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
