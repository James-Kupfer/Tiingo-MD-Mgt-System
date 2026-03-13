# Quick Start

This project downloads and maintains daily Tiingo market data with a single
Windows batch launcher. The default setup targets US equities and ETFs, but
the filters are configurable in `src\config.py`.

## 1. Get the code

```bash
git clone https://github.com/<your-username>/Tiingo-MD-Mgt-System.git
cd Tiingo-MD-Mgt-System
```

## 2. Install Python

Install Python 3.10 or newer and ensure `python` is available on your PATH.

To verify:

```cmd
python --version
```

## 3. Add your Tiingo API key

Create `apikey.txt` in the project root and place your Tiingo API key on a
single line with no quotes:

```text
your_tiingo_api_key_here
```

Do not commit this file to source control.

## 4. Review configuration

Open `src\config.py` and confirm the settings match your environment.

Most important settings:

- `RAW_DATA_DIR` - output directory for downloaded ticker CSV files. *(Must use Python raw strings `r"C:\Path"` or double backslashes)*
- `MAX_REQUESTS_PER_HOUR` - set this to match your Tiingo subscription tier.
- `PRICE_CURRENCY` - currency filter for ticker selection.
- `ASSET_TYPES` - asset classes to include.
- `EXCHANGES` and `EXCHANGES_STARTSWITH` - exchange filters.
- `FORCE_DOWNLOAD_SYMBOLS` - symbols that bypass normal filters.
- `EXCLUDE_TICKERS` - symbols that are always excluded.

> Current default output path is:
>
> `C:\Beaker\Market Data\Raw`

## 5. Run the pipeline

From the project root, run:

```cmd
Download_MarketData.bat
```

This performs three steps automatically:

1. Download and filter the Tiingo supported tickers list.
2. Delete temporary staging files.
3. Incrementally update market data for all selected tickers.

## 6. Optional modes

Full re-download:

```cmd
Download_MarketData.bat full
```

Ticker refresh only:

```cmd
Download_MarketData.bat tickers
```

## 7. Check outputs

Generated files:

- `Tickers_to_Update.csv` - all filtered tickers, including delisted names.
- `active_list.csv` - actively trading subset.
- `<RAW_DATA_DIR>\<TICKER>.csv` - active ticker price history.
- `<RAW_DATA_DIR>\<TICKER>_dlist.csv` - delisted ticker price history.
- `logs\tiingo_download_YYYY-MM-DD_HH-MM.log` - run log.

## 8. Schedule daily updates

Recommended for Windows Task Scheduler:

- Program/script: full path to `Download_MarketData.bat`
- Start in: project root folder
- Schedule: once daily after market close
