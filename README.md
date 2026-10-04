# Tiingo-MD-Mgt-System

A Windows-based utility for downloading, filtering, and maintaining daily
OHLCV market data from the [Tiingo API](https://www.tiingo.com).

The system is designed around a single entry point, `Download_MarketData.bat`,
which runs the entire pipeline: ticker-list refresh, cleanup of temporary
files, and full or incremental per-ticker price downloads.

## Features

- Automatic full and incremental download modes.
- Parallel data downloads with thread-safe rate limiting.
- Configurable filtering by exchange, asset type, currency, ticker pattern,
  and custom inclusion/exclusion lists.
- Separate handling for delisted tickers.
- Duplicate-safe incremental appends based on trading date.
- Timestamped logging for each run.
- Standard-library-only Python implementation.

## How it works

The pipeline has three stages:

1. **Ticker update**
   - `src\tiingo_ticker_manager.py` downloads Tiingo's supported tickers ZIP.
   - The ticker list is extracted and filtered according to `src\config.py`.
   - Three files are written:
     - `Tickers_to_Update.csv`
     - `active_list.csv`
     - `ETFs.xlsx` (in the Price store, `ETF_LIST_FILE` in `src\config.py`): the
       `assetType == "ETF"` rows of the filtered list, same columns as
       `Tickers_to_Update.csv`. Consumed by the Crash Prediction project's
       `etf_excess_return.py`.

2. **Cleanup**
   - Temporary ZIP and extracted staging files under `tiingo\` are deleted.

3. **Market data download**
   - `src\tiingo_data_downloader.py` loads the filtered ticker list and API key.
   - `src\download.py` performs the actual HTTP downloads, rate limiting,
     incremental detection, file saves, and parallel orchestration.

## Project structure

```text
<project_root>\
├── Download_MarketData.bat
├── apikey.txt
├── Tickers_to_Update.csv
├── active_list.csv
├── logs\
├── tiingo\
└── src\
    ├── config.py
    ├── tiingo_ticker_manager.py
    ├── tiingo_data_downloader.py
    └── download.py
```

## Requirements

- Windows
- Python 3.10+
- Tiingo account and API key
- Internet access for Tiingo API calls

No third-party Python packages are required, except `openpyxl` for the `ETFs.xlsx` export.

## Setup

1. **API Key:** Create a file named `apikey.txt` in the root directory and paste your Tiingo API key inside it on a single line.
2. **Directories:** The application will automatically create the `logs\` and output data directories if they do not exist; you do not need to create them manually.

## Configuration

All user-editable settings live in `src\config.py`.

Key settings include:

- `ETF_LIST_FILE`: Path of the ETF list workbook written on every ticker refresh (if it cannot be written, e.g. open in Excel, an error is logged, the old file is kept and the run continues).
- `RAW_DATA_DIR`: The destination directory path for downloaded CSV files. **Note:** Use Python raw strings (e.g., `r"C:\Path"`) or double backslashes to avoid Windows unicode escape errors.
- `MAX_REQUESTS_PER_HOUR`: Maximum allowed API calls per hour. Note: This counter does not persist across multiple runs. It resets every time the script is executed.
- `DOWNLOAD_TIMEOUT`: Maximum time (in seconds) to wait for an HTTP response.
- `MAX_RETRIES`: Number of retry attempts after a failure or timeout.
- `RETRY_DELAY`: Wait time (in seconds) between retries.
- `PRICE_CURRENCY`: The base currency filter (e.g., `"USD"`).
- `ASSET_TYPES`: List of asset classifications to include (e.g., `["Stock", "ETF"]`).
- `EXCHANGES`: List of exact exchange acronyms to include (e.g., `["NASDAQ", "AMEX"]`).
- `EXCHANGES_STARTSWITH`: Prefix string used to capture related exchanges (e.g., `"NYSE"`).
- `EXCLUDED_CHARS`: Set of characters that, if found in a ticker symbol, cause that ticker to be skipped.
- `FORCE_DOWNLOAD_SYMBOLS`: Specific set of ticker symbols that will always be downloaded, bypassing all exclusion filters.
- `EXCLUDE_TICKERS`: Specific set of ticker symbols to explicitly ignore.
- `LOG_LEVEL`: Minimum severity level for messages written to the log file (e.g., `"DEBUG"`).
- `CONSOLE_LOG_LEVEL`: Minimum severity level for messages printed to the command prompt console (e.g., `"INFO"`).
- `CSV_DELIMITER`: Character used to separate values in saved market data files (default `","`).
- `SHOW_PROGRESS`: Boolean (`True`/`False`) toggling the console progress tracker.
- `DISPLAY_INTERVAL`: Number of tickers processed before printing a progress update.

### Current default output path

The current configuration writes downloaded CSV files to:

```text
C:\Beaker\Market Data\Raw
```

If you want repository-local output instead, change `RAW_DATA_DIR` in
`src\config.py`.

## Ticker filtering

The ticker manager applies filters in this order:

1. `FORCE_DOWNLOAD_SYMBOLS` - always included.
2. `EXCLUDE_TICKERS` - always excluded.
3. Missing `startDate` - excluded.
4. `PRICE_CURRENCY` - must match.
5. `ASSET_TYPES` - must match.
6. `EXCHANGES` or `EXCHANGES_STARTSWITH` - must match.
7. `EXCLUDED_CHARS` - excluded if any disallowed character or digit is present.

This design keeps the downloader focused on securities relevant to the target
universe while still allowing specific exceptions.

## Download modes

### Incremental mode

Default behavior when running:

```cmd
Download_MarketData.bat
```

For each active ticker:

- If no file exists, full history is downloaded.
- If a file exists, only data newer than the latest stored trading date is
  requested.
- If the file is already current, the ticker is skipped.

### Full mode

```cmd
Download_MarketData.bat full
```

For each active ticker, the existing CSV is overwritten with a complete
download from the ticker's `startDate` through today.

### Tickers-only mode

```cmd
Download_MarketData.bat tickers
```

Refreshes and filters the ticker universe without downloading market data.

### Inventory update

```cmd
C:\Documents\Investments\Systems\run_inventory_update.bat
```

A partial update for the symbols on the Investment Portfolio workbook's
Inventory sheet (plus the DDC source symbols SPY/IWM/QQQ). The ticker list is
not refreshed and the symbols are assumed valid. The batch file runs, in order:

1. `src\inventory_tickers.py` - writes `Tickers_Inventory.csv`. The workbook
   path, sheet, columns, exclusions and sources are read from the DDC project's
   `config.toml` (`DDC_DIR` in `src\config.py`) and parsed by its `workbook.py`,
   so Inventory has one definition. A Mapped Symbol overrides Symbol; symbols
   that cannot go into a Tiingo URL (e.g. `MES CME`) are logged and skipped.
   An unreadable workbook (mid-save, held by Excel) is retried
   `INVENTORY_READ_ATTEMPTS` times, `INVENTORY_READ_RETRY_SECONDS` apart.
2. `src\tiingo_data_downloader.py incremental --tickers Tickers_Inventory.csv`
   - existing raw files are appended to; symbols with no raw file get full
   history. Symbols Tiingo does not carry return 404 and are counted as
   skipped, not failed.
3. `Clean_And_Convert.bat` and DDC's `run_ddc.bat` (full-universe steps,
   roughly 30 minutes).

`Tickers_Inventory.csv` is separate from `Tickers_to_Update.csv` so a full run's
file is never overwritten. Do not run this at the same time as
`Run_Update_Pipeline.bat`; both write the same Raw, Clean and Price stores.

Task Scheduler imports for the daily 16:20 inventory run and the daily 18:00
full run are in `C:\Documents\Investments\Systems\Scheduled Tasks\`.

## Output files

### Ticker universe

- `Tickers_to_Update.csv` - all valid filtered tickers, including delisted.
- `active_list.csv` - tickers considered currently active.
- `Tickers_Inventory.csv` - Inventory symbols only (inventory update).

### Price history

- `<RAW_DATA_DIR>\<TICKER>.csv` - active ticker history.
- `<RAW_DATA_DIR>\<TICKER>_dlist.csv` - delisted ticker history.

Saved price files include a leading `symbol` column added by the downloader.

## Logging

Each run creates a timestamped log file in `logs\`:

```text
logs\tiingo_download_YYYY-MM-DD_HH-MM.log
```

The file logger captures detailed activity, while console logging can be kept
brief by setting `CONSOLE_LOG_LEVEL = "WARNING"`.

## Typical usage

Incremental daily update:

```cmd
Download_MarketData.bat
```

Forced rebuild of all price history:

```cmd
Download_MarketData.bat full
```

Ticker universe refresh only:

```cmd
Download_MarketData.bat tickers
```

## Operational notes

- The batch file is the intended entry point for normal operation.
- **Delisted ticker transitions:** If an active ticker becomes delisted, a separate `<TICKER>_dlist.csv` file is created. The original `<TICKER>.csv` remains as-is, meaning your old data is not lost or overwritten by the new suffix.
- Incremental appends are date-deduplicated to avoid duplicate rows.
- The downloader uses a heartbeat log during long runs so large jobs do not appear stalled.
- **Rate Limit Tracking:** The `MAX_REQUESTS_PER_HOUR` limit tracking is local to the current execution. It does not persist if you stop and restart the script multiple times within an hour.


