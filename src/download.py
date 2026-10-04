"""
Tiingo download engine.

Core HTTP download, CSV persistence, rate limiting, and parallel orchestration
for daily OHLCV data from Tiingo. download_ticker_data returns a normalized
CSV string on success, or None when there is no data or a request fails.
Callers treat None as a skip rather than a failure when the request completed
but Tiingo simply returned no rows for the requested date range.
"""

import csv
import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

logger = logging.getLogger(__name__)


class RateLimiter:
    """Thread-safe sliding-window rate limiter for API requests."""

    def __init__(self, max_requests_per_hour):
        """
        Initialize rate limiter.

        Args:
            max_requests_per_hour (int): Maximum requests allowed per rolling hour.
        """
        self.max_requests = max_requests_per_hour
        self.request_times = deque()
        self.lock = Lock()

    def acquire(self):
        """Block if necessary to respect the configured rate limit."""
        while True:
            with self.lock:
                now = time.time()
                while self.request_times and now - self.request_times[0] > 3600:
                    self.request_times.popleft()
                if len(self.request_times) < self.max_requests:
                    self.request_times.append(now)
                    return
                oldest = self.request_times[0]
                sleep_time = 3600 - (now - oldest) + 1

            # Release the lock before sleeping so other threads are not blocked.
            if sleep_time > 0:
                logger.info("Rate limit reached, waiting %.0fs", sleep_time)
                time.sleep(sleep_time)


def build_api_url(ticker, api_key, base_url, start_date=None, end_date=None):
    """
    Build the Tiingo API URL for daily prices.

    Args:
        ticker (str): Ticker symbol.
        api_key (str): Tiingo API key.
        base_url (str): Base URL for Tiingo daily endpoint.
        start_date (str): Optional inclusive start date (YYYY-MM-DD).
        end_date (str): Optional inclusive end date (YYYY-MM-DD).

    Returns:
        str: Fully qualified request URL.
    """
    url = base_url + "/" + ticker + "/prices?token=" + api_key + "&format=csv"
    if start_date:
        url += "&startDate=" + start_date
    if end_date:
        url += "&endDate=" + end_date
    return url


def download_ticker_data(ticker, api_key, base_url, timeout, max_retries,
                         retry_delay, start_date=None, end_date=None):
    """
    Download CSV data for a single ticker with retry logic.

    Returns a normalized CSV string on success, or None when Tiingo returns
    no rows or the request ultimately fails after all retries.

    Args:
        ticker (str): Ticker symbol.
        api_key (str): Tiingo API key.
        base_url (str): Tiingo base URL.
        timeout (int): Per-request timeout in seconds.
        max_retries (int): Maximum retry attempts.
        retry_delay (int): Base delay between retries in seconds.
        start_date (str): Optional start date (YYYY-MM-DD).
        end_date (str): Optional end date (YYYY-MM-DD).

    Returns:
        str or None: Normalized CSV text on success, None otherwise.
    """
    url = build_api_url(ticker, api_key, base_url, start_date, end_date)

    date_range = ("from " + start_date) if start_date else "all history"
    if end_date:
        date_range = date_range + " to " + end_date
    logger.debug("%s: Requesting data %s", ticker, date_range)

    for attempt in range(max_retries):
        try:
            with urlopen(url, timeout=timeout) as response:
                raw = response.read().decode("utf-8")

            stripped = raw.strip()

            if stripped == "[]":
                logger.debug("%s: No data available for date range %s", ticker, date_range)
                return None

            if stripped.startswith("{") or stripped.startswith("["):
                logger.debug("%s: No CSV data available for date range %s", ticker, date_range)
                return None

            crlf = "\r\n"
            cr = "\r"
            lf = "\n"
            normalized = raw.replace(crlf, lf).replace(cr, lf)

            sep = "\n"
            lines = [line for line in normalized.split(sep) if line.strip()]

            if len(lines) < 2:
                logger.debug(
                    "%s: No price data for date range %s (header only, response: %d bytes)",
                    ticker, date_range, len(raw),
                )
                return None

            logger.debug("%s: Successfully downloaded %d rows", ticker, len(lines))
            return normalized

        except HTTPError as e:
            if e.code == 404:
                logger.debug("%s: Not found (404)", ticker)
                return None
            if e.code == 429:
                logger.warning("%s: Rate limited (429)", ticker)
                time.sleep(retry_delay * (attempt + 1))
            elif e.code in (500, 502, 503):
                logger.warning("%s: Server error (%s), retrying...", ticker, e.code)
                time.sleep(retry_delay * (attempt + 1))
            else:
                logger.error("%s: HTTP error %s", ticker, e.code)
                return None

        except (URLError, TimeoutError) as e:
            logger.warning("%s: Network error (attempt %d): %s", ticker, attempt + 1, e)
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))

        except Exception as e:
            logger.error("%s: Unexpected error: %s", ticker, e, exc_info=True)
            return None

    logger.error("%s: Failed after %d retries", ticker, max_retries)
    return None


def save_ticker_data(ticker, data, raw_data_dir, is_delisted=False):
    """
    Save downloaded ticker data to CSV, prepending a symbol column.

    Args:
        ticker (str): Ticker symbol.
        data (str): Raw Tiingo CSV text.
        raw_data_dir (Path): Output directory.
        is_delisted (bool): If True, use the _dlist.csv filename suffix.

    Returns:
        True on success, False on error, None when there are no data rows.
    """
    try:
        if not data or not data.strip():
            logger.error("%s: No data to save (empty)", ticker)
            return False

        filename = (ticker + "_dlist.csv") if is_delisted else (ticker + ".csv")
        filepath = raw_data_dir / filename

        sep = "\n"
        lines = data.strip().split(sep)
        if len(lines) < 2:
            logger.debug("%s: No new data available for date range", ticker)
            return None

        new_header = "symbol," + lines[0]
        new_lines = [new_header]
        for line in lines[1:]:
            if line.strip():
                new_lines.append(ticker + "," + line)

        if len(new_lines) < 2:
            logger.error("%s: No valid data rows after processing", ticker)
            return False

        join_sep = "\n"
        with open(filepath, "w", encoding="utf-8", newline="") as f:
            f.write(join_sep.join(new_lines))
            if new_lines[-1]:
                f.write(join_sep)

        logger.debug("%s: Saved %d data rows", ticker, len(new_lines) - 1)
        return True

    except Exception as e:
        logger.error("%s: Failed to save: %s", ticker, e)
        return False


def append_ticker_data(ticker, new_data, raw_data_dir, csv_delimiter, parse_date_func,
                       replace_existing=False):
    """
    Append non-duplicate rows to an existing active ticker CSV.

    Duplicate detection is date-based. If the existing header does not match
    the expected format (with a leading symbol column), the file is rewritten
    in full via save_ticker_data.

    With replace_existing, a downloaded row whose date is already stored but
    whose values differ replaces the stored row (the file is rewritten in
    place, original row order kept); this is how a refetch turns a
    preliminary bar into its final value. Without it, stored dates are never
    touched.

    Args:
        ticker (str): Ticker symbol.
        new_data (str): Newly-downloaded Tiingo CSV text.
        raw_data_dir (Path): Output directory.
        csv_delimiter (str): Delimiter used in stored CSV files.
        parse_date_func (callable): Date parser supplied by the caller.
        replace_existing (bool): Overwrite stored rows that the download revises.

    Returns:
        bool: True on success, False on failure.
    """
    try:
        filepath = raw_data_dir / (ticker + ".csv")

        if not filepath.exists():
            return bool(save_ticker_data(ticker, new_data, raw_data_dir))

        existing_text = filepath.read_text(encoding="utf-8").strip()
        if not existing_text:
            return bool(save_ticker_data(ticker, new_data, raw_data_dir))

        sep = "\n"
        existing_lines = existing_text.split(sep)
        new_lines = new_data.strip().split(sep)

        if not new_lines:
            return True

        existing_header = existing_lines[0]
        new_header = new_lines[0]
        expected_new_header = "symbol," + new_header

        if existing_header.strip() != expected_new_header.strip():
            if replace_existing:
                # new_data is only the refetch window here: save_ticker_data
                # would replace the whole history with a few days.
                logger.error(
                    "%s: Header mismatch on refetch - stored %r vs downloaded %r; "
                    "file left untouched (run a full download for this ticker)",
                    ticker, existing_header, expected_new_header,
                )
                return False
            logger.warning(
                "%s: Header mismatch - existing file may be in old format. "
                "Performing full rewrite with symbol column.", ticker
            )
            return bool(save_ticker_data(ticker, new_data, raw_data_dir))

        header_cols = next(csv.reader([existing_header], delimiter=csv_delimiter))
        date_col_idx = None
        for idx, col_name in enumerate(header_cols):
            if col_name.strip().lower() == "date":
                date_col_idx = idx
                break
        if date_col_idx is None:
            date_col_idx = 1

        existing_dates = {}  # date key -> stored line
        for line in existing_lines[1:]:
            if not line.strip():
                continue
            row = next(csv.reader([line], delimiter=csv_delimiter))
            if len(row) <= date_col_idx:
                continue
            dt = parse_date_func(row[date_col_idx].strip())
            if dt is not None:
                existing_dates[dt.strftime("%Y-%m-%d")] = line.rstrip("\r")

        new_header_cols = next(csv.reader([new_header], delimiter=csv_delimiter))
        new_date_col_idx = None
        for idx, col_name in enumerate(new_header_cols):
            if col_name.strip().lower() == "date":
                new_date_col_idx = idx
                break
        if new_date_col_idx is None:
            new_date_col_idx = 0

        downloaded = {}  # date key -> line as it would be stored
        for line in new_lines[1:]:
            if not line.strip():
                continue
            row = next(csv.reader([line], delimiter=csv_delimiter))
            if len(row) <= new_date_col_idx:
                continue
            dt = parse_date_func(row[new_date_col_idx].strip())
            if dt is None:
                continue
            downloaded[dt.strftime("%Y-%m-%d")] = ticker + "," + line.rstrip("\r")

        added = [line for key, line in downloaded.items() if key not in existing_dates]
        revised = {
            existing_dates[key]: line
            for key, line in downloaded.items()
            if key in existing_dates and existing_dates[key] != line
        }

        if replace_existing and revised:
            kept = [revised.get(line.rstrip("\r"), line) for line in existing_lines]
            filepath.write_text("\n".join(kept + added) + "\n", encoding="utf-8", newline="")
            logger.info("%s: Replaced %d revised row(s), added %d", ticker, len(revised), len(added))
            return True

        if added:
            with open(filepath, "a", encoding="utf-8", newline="") as f:
                f.writelines(line + "\n" for line in added)
            logger.debug("%s: Added %d new rows", ticker, len(added))

        return True

    except Exception as e:
        logger.error("%s: Failed to append: %s", ticker, e)
        return False


ADJ_STALE_MIN_EFFECT = 1e-4  # ignore ex-dates whose adjustment moves the return less than this


def stale_adjclose_dates(filepath, delimiter=","):
    """Ex-dates in a stored raw file whose adjClose was never restated.

    Tiingo restates adjClose for the whole history when a dividend or split
    occurs. A file that is only ever appended to keeps the adjClose values it
    was downloaded with, so an ex-date leaves the older rows on the previous
    basis. On an ex-date row the adjClose return should match the
    split/dividend-derived return; when it matches the raw close return
    instead (and the two differ measurably), the rows before it are stale.

    Returns:
        list[str]: dates (YYYY-MM-DD as stored) of such ex-dates, oldest first.
    """
    stale = []
    prev = None
    with open(filepath, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter=delimiter):
            try:
                cur = {
                    "date": row["date"].strip(),
                    "close": float(row["close"]),
                    "adj": float(row["adjClose"]),
                    "div": float(row.get("divCash") or 0.0),
                    "split": float(row.get("splitFactor") or 1.0) or 1.0,
                }
            except (KeyError, TypeError, ValueError):
                prev = None
                continue
            if prev and (cur["div"] != 0.0 or cur["split"] != 1.0) and min(prev["close"], prev["adj"], cur["close"]) > 0:
                raw = cur["close"] / prev["close"]
                expected_prior = (prev["close"] - cur["div"]) / cur["split"]
                if expected_prior > 0:
                    expected = cur["close"] / expected_prior
                    vendor = cur["adj"] / prev["adj"]
                    if (abs(expected - raw) / raw > ADJ_STALE_MIN_EFFECT
                            and abs(vendor - raw) < abs(vendor - expected)):
                        stale.append(cur["date"])
            prev = cur
    return stale


def redownload_if_adjclose_stale(ticker, start_date, config, rate_limiter, stats_lock, stats):
    """After an incremental update, re-download the full history when the stored
    adjClose is stale (see stale_adjclose_dates), so it is Tiingo's own
    consistent series again. Returns True when a re-download was done."""
    filepath = config["raw_data_dir"] / (ticker + ".csv")
    stale = stale_adjclose_dates(filepath, config["csv_delimiter"])
    if not stale:
        return False
    logger.info("%s: adjClose not restated at %d ex-date(s) (latest %s); re-downloading full history",
                ticker, len(stale), stale[-1])
    rate_limiter.acquire()
    data = download_ticker_data(
        ticker, config["api_key"], config["base_url"],
        config["timeout"], config["max_retries"], config["retry_delay"],
        start_date, config["today"],
    )
    with stats_lock:
        stats["request_count"] += 1
    if not data or save_ticker_data(ticker, data, config["raw_data_dir"]) is not True:
        logger.error("%s: full re-download for stale adjClose failed", ticker)
        with stats_lock:
            stats["failed"] += 1
        return False
    remaining = stale_adjclose_dates(filepath, config["csv_delimiter"])
    if remaining:
        logger.warning("%s: adjClose still looks unrestated at %s after a full re-download", ticker, remaining[-3:])
    return True


def process_single_ticker(idx, ticker_row, config, rate_limiter, stats_lock, stats):
    """
    Download and persist data for one ticker within the worker pool.

    Args:
        idx (int): 1-based ticker row index.
        ticker_row (dict): Ticker metadata row from Tickers_to_Update.csv.
        config (dict): Shared configuration dictionary.
        rate_limiter (RateLimiter): Shared rate limiter instance.
        stats_lock (Lock): Lock guarding shared statistics.
        stats (dict): Shared statistics dictionary.
    """
    try:
        ticker = ticker_row.get("ticker", "").strip()
        start_date = ticker_row.get("startDate", "").strip()
        end_date = ticker_row.get("endDate", "").strip()

        if not ticker:
            logger.warning("Row %d: Empty ticker", idx)
            with stats_lock:
                stats["failed"] += 1
            return

        is_delisted = False
        if config["cutoff_date"] and end_date:
            dt = config["parse_date_func"](end_date)
            if dt and dt < config["cutoff_date"]:
                is_delisted = True

        # ---- Delisted ----
        if is_delisted:
            if config["ticker_exists_func"](ticker):
                logger.debug("%s: Already have delisted data, skipping", ticker)
                with stats_lock:
                    stats["skipped"] += 1
                return

            logger.info("%s: Downloading delisted data", ticker)
            rate_limiter.acquire()
            data = download_ticker_data(
                ticker, config["api_key"], config["base_url"],
                config["timeout"], config["max_retries"], config["retry_delay"],
                start_date, end_date,
            )
            with stats_lock:
                stats["request_count"] += 1

            if data:
                result = save_ticker_data(ticker, data, config["raw_data_dir"], is_delisted=True)
                if result is True:
                    with stats_lock:
                        stats["successful"] += 1
                    logger.info("%s: Delisted data saved", ticker)
                elif result is None:
                    with stats_lock:
                        stats["skipped"] += 1
                    logger.debug("%s: No delisted price data rows returned", ticker)
                else:
                    with stats_lock:
                        stats["failed"] += 1
            else:
                with stats_lock:
                    stats["skipped"] += 1
                logger.debug("%s: No delisted price data returned", ticker)
            return

        # ---- Active tickers ----
        today = config["today"]
        mode = config["mode"]

        if mode == "full":
            logger.info("%s: Full mode download", ticker)
            rate_limiter.acquire()
            data = download_ticker_data(
                ticker, config["api_key"], config["base_url"],
                config["timeout"], config["max_retries"], config["retry_delay"],
                start_date, today,
            )
            with stats_lock:
                stats["request_count"] += 1

            if data:
                result = save_ticker_data(ticker, data, config["raw_data_dir"])
                if result is True:
                    with stats_lock:
                        stats["successful"] += 1
                    logger.info("%s: Full download successful", ticker)
                elif result is None:
                    with stats_lock:
                        stats["skipped"] += 1
                    logger.debug("%s: No price data rows for full download range", ticker)
                else:
                    with stats_lock:
                        stats["failed"] += 1
            else:
                with stats_lock:
                    stats["skipped"] += 1
                logger.debug("%s: No price data returned for full download range", ticker)
            return

        # ---- Incremental mode ----
        file_exists = config["ticker_exists_func"](ticker)

        if file_exists:
            latest_date = config["get_latest_date_func"](ticker)

            if latest_date:
                dt = config["parse_date_func"](latest_date)

                if not dt:
                    # Bad date in file - fallback to full download
                    logger.warning("%s: Invalid date in file, falling back to full download", ticker)
                    rate_limiter.acquire()
                    data = download_ticker_data(
                        ticker, config["api_key"], config["base_url"],
                        config["timeout"], config["max_retries"], config["retry_delay"],
                        start_date, today,
                    )
                    with stats_lock:
                        stats["request_count"] += 1
                    if data:
                        result = save_ticker_data(ticker, data, config["raw_data_dir"])
                        if result is True:
                            with stats_lock:
                                stats["successful"] += 1
                            logger.info("%s: Full download successful", ticker)
                        elif result is None:
                            with stats_lock:
                                stats["skipped"] += 1
                            logger.debug("%s: No price data rows after fallback full download", ticker)
                        else:
                            with stats_lock:
                                stats["failed"] += 1
                    else:
                        with stats_lock:
                            stats["skipped"] += 1
                        logger.debug("%s: No price data returned after fallback full download", ticker)
                    return

                refetch_days = config.get("refetch_days", 0)
                if latest_date >= today and refetch_days <= 0:
                    with stats_lock:
                        stats["skipped"] += 1
                    logger.debug("%s: Already up-to-date (latest: %s)", ticker, latest_date)
                    return

                # refetch_days > 0 re-requests the last N calendar days as well,
                # so a bar stored preliminary by an earlier same-day run is
                # replaced by Tiingo's final value (append_ticker_data replace mode).
                start_dt = dt - timedelta(days=refetch_days) if refetch_days > 0 else dt + timedelta(days=1)
                next_date = start_dt.strftime("%Y-%m-%d")
                rate_limiter.acquire()
                data = download_ticker_data(
                    ticker, config["api_key"], config["base_url"],
                    config["timeout"], config["max_retries"], config["retry_delay"],
                    next_date, today,
                )
                with stats_lock:
                    stats["request_count"] += 1

                if data:
                    sep = "\n"
                    lines = data.strip().split(sep)
                    if len(lines) > 1:
                        if append_ticker_data(
                            ticker, data, config["raw_data_dir"],
                            config["csv_delimiter"], config["parse_date_func"],
                            replace_existing=refetch_days > 0,
                        ):
                            with stats_lock:
                                stats["successful"] += 1
                            logger.info("%s: Incremental update successful", ticker)
                            redownload_if_adjclose_stale(
                                ticker, start_date, config, rate_limiter, stats_lock, stats)
                        else:
                            with stats_lock:
                                stats["failed"] += 1
                    else:
                        with stats_lock:
                            stats["skipped"] += 1
                        logger.debug("%s: No new data rows in response", ticker)
                else:
                    with stats_lock:
                        stats["skipped"] += 1
                    logger.debug("%s: No new data returned for incremental range", ticker)

            else:
                # Cannot read latest date - fallback to full download
                logger.warning("%s: Cannot read date from file, falling back to full download", ticker)
                rate_limiter.acquire()
                data = download_ticker_data(
                    ticker, config["api_key"], config["base_url"],
                    config["timeout"], config["max_retries"], config["retry_delay"],
                    start_date, today,
                )
                with stats_lock:
                    stats["request_count"] += 1
                if data:
                    result = save_ticker_data(ticker, data, config["raw_data_dir"])
                    if result is True:
                        with stats_lock:
                            stats["successful"] += 1
                        logger.info("%s: Full download successful", ticker)
                    elif result is None:
                        with stats_lock:
                            stats["skipped"] += 1
                        logger.debug("%s: No price data rows after fallback full download", ticker)
                    else:
                        with stats_lock:
                            stats["failed"] += 1
                else:
                    with stats_lock:
                        stats["skipped"] += 1
                    logger.debug("%s: No price data returned after fallback full download", ticker)

        else:
            # No local file - first-time download
            logger.info("%s: First download", ticker)
            rate_limiter.acquire()
            data = download_ticker_data(
                ticker, config["api_key"], config["base_url"],
                config["timeout"], config["max_retries"], config["retry_delay"],
                start_date, today,
            )
            with stats_lock:
                stats["request_count"] += 1

            if data:
                result = save_ticker_data(ticker, data, config["raw_data_dir"])
                if result is True:
                    with stats_lock:
                        stats["successful"] += 1
                    logger.info("%s: Initial download successful", ticker)
                elif result is None:
                    with stats_lock:
                        stats["skipped"] += 1
                    logger.debug("%s: No price data rows for initial download range", ticker)
                else:
                    with stats_lock:
                        stats["failed"] += 1
            else:
                with stats_lock:
                    stats["skipped"] += 1
                logger.debug("%s: No price data returned for initial download range", ticker)

    except Exception as e:
        ticker = ticker_row.get("ticker", "UNKNOWN")
        logger.error(
            "%s (row %d): Unhandled exception in worker thread: %s",
            ticker, idx, e, exc_info=True,
        )
        with stats_lock:
            stats["failed"] += 1


def download_all_tickers_parallel(tickers, config):
    """
    Download all tickers using a thread pool and shared rate limiter.

    Args:
        tickers (list): List of ticker dicts from Tickers_to_Update.csv.
        config (dict): Configuration dict from tiingo_data_downloader.py.

    Returns:
        tuple: (total, successful, failed, skipped, request_count, elapsed)
    """
    total = len(tickers)
    rate_limiter = RateLimiter(config["max_requests_per_hour"])

    stats_lock = Lock()
    stats = {
        "successful": 0,
        "failed": 0,
        "skipped": 0,
        "request_count": 0,
    }

    start_time = time.time()
    max_workers = min(16, max(1, config["max_requests_per_hour"] // 10))
    logger.info("Using %d parallel workers", max_workers)

    stop_event = threading.Event()

    def heartbeat():
        """Emit a periodic status message during long-running downloads."""
        while not stop_event.is_set():
            time.sleep(60)
            with stats_lock:
                logger.info(
                    "Heartbeat: %d successful, %d failed, %d skipped, %d requests",
                    stats["successful"], stats["failed"],
                    stats["skipped"], stats["request_count"],
                )

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    process_single_ticker,
                    idx,
                    ticker_row,
                    config,
                    rate_limiter,
                    stats_lock,
                    stats,
                ): idx
                for idx, ticker_row in enumerate(tickers, 1)
            }

            completed = 0
            display_interval = config.get("display_interval", 100)

            for future in as_completed(futures):
                completed += 1
                if config.get("show_progress", True) and completed % display_interval == 0:
                    logger.info("Progress: %d/%d tickers processed", completed, total)

                try:
                    future.result(timeout=120)
                except TimeoutError:
                    row_idx = futures[future]
                    logger.error("Timeout processing ticker at row %d", row_idx)
                    with stats_lock:
                        stats["failed"] += 1
                except Exception as e:
                    row_idx = futures[future]
                    logger.error(
                        "Error processing ticker at row %d: %s", row_idx, e, exc_info=True
                    )
                    with stats_lock:
                        stats["failed"] += 1

            logger.info("All %d tickers processed, waiting for threads to complete...", total)

    except KeyboardInterrupt:
        logger.warning("Download interrupted by user. Shutting down gracefully...")
        stop_event.set()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    except Exception as e:
        logger.error("Critical error in parallel processing: %s", e, exc_info=True)
        stop_event.set()
        raise
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=1)

    elapsed = time.time() - start_time

    return (
        total,
        stats["successful"],
        stats["failed"],
        stats["skipped"],
        stats["request_count"],
        elapsed,
    )
