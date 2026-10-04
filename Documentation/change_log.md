# Change Log

## 2026-10-04 - Hedge-signal symbols always downloaded

**Type**: feature
**Files**: src/config.py, src/inventory_tickers.py, tests/test_inventory_tickers.py

`EXTRA_INVENTORY_SYMBOLS = {"RSP", "SPY", "QQQ", "TAIL"}` is unioned into
`Tickers_Inventory.csv`: the Investments hedge signal's trigger symbols and
sleeve now price off this store (Clean_Inventory). RSP was not in it before.

Found, not fixed (needs a decision): `clean_prices.clean_symbol_rows` keeps the
vendor's adjClose return on a dividend day when it is within `DEFAULT_TOLERANCE`
(1%) of the dividend-derived one. Rows appended incrementally carry an
unadjusted vendor adjClose, so every dividend under 1% of price since the raw
files went incremental is silently dropped from the synthetic adjClose (SPY:
adjClose == close across four ex-dates, 1.08% off over a year). Affects DDC
and everything reading Price/Price_Inventory adjClose. The hedge signal avoids
it by adjusting from raw columns itself.

## 2026-10-04 - Portfolio symbols, ticker-subset clean, refetch of revised bars

**Type**: feature
**Files**: src/inventory_tickers.py, src/clean_prices.py, src/download.py,
src/tiingo_data_downloader.py, src/config.py, Download_MarketData.bat,
tests/test_inventory_tickers.py, tests/test_clean_prices.py (new),
tests/test_download.py (new), README.md

Supports `Systems\Market Data Pipeline` (replaces `Systems\run_inventory_update.bat`
and `Run_Update_Pipeline.bat`): a 16:20 run on Tiingo's preliminary bar for the
Inventory/Portfolio symbols only, and an 18:00 full run that must replace those
preliminary bars.

- `inventory_tickers.py` adds the Portfolio sheet's underlyings (option and
  warrant legs resolve to their stock, via Inventory where a row exists). On
  2026-10-04 this adds no symbol Inventory lacks; it guards a position opened
  before its Inventory row exists. A workbook without a Portfolio sheet now
  fails the step (ValueError, retried like any unreadable workbook).
- `clean_prices.py --tickers FILE` cleans only the listed base tickers, so the
  inventory run takes about a minute instead of the full ~25 min clean.
- `tiingo_data_downloader.py --refetch-days N` (forwarded by
  `Download_MarketData.bat`) re-requests the last N days even for a current
  ticker; `append_ticker_data(replace_existing=True)` replaces stored rows whose
  values changed, rewriting the file in place only when one did. Previously a
  ticker whose latest stored date was today was skipped, so a preliminary bar
  stored at 16:20 was permanent. The rewrite is not atomic; a crash mid-write
  leaves a file whose dates can't be read, which the next incremental run
  already handles by re-downloading full history. A temp file was rejected to
  keep anything but price files out of `Raw`.
- Removed a stray `'` line in `Download_MarketData.bat` (cmd printed an error
  and carried on).
- Review fixes: on a refetch, a header mismatch now leaves the file untouched
  and fails the ticker (the pre-existing branch would have rewritten the file
  from only the refetch window, cutting history to a few days); the replace
  rewrite writes LF like the append path; an option leg is dropped when its
  underlying is excluded, but a multi-token listing is no longer excluded by
  its first token alone.

## 2026-10-03 - Export the ETF list to Price\ETFs.xlsx

**Type**: feature
**Files**: src/tiingo_ticker_manager.py, src/config.py, tests/test_tiingo_ticker_manager.py, README.md

The ticker step now also writes the ETF rows of the filtered ticker list to
`Market Data\Price\ETFs.xlsx` (config.py `ETF_LIST_FILE`), so downstream
projects (Crash Prediction's per-ETF excess return) get the ETF universe
without re-parsing Tiingo's ZIP.

- Source is the filtered list (`valid_tickers`, same filters as
  `Tickers_to_Update.csv`), so the ETFs are exactly those this system downloads.
  Delisted ETFs are included; their `endDate` column is kept.
- Runs inside `process_ticker_list`, so every mode of `Download_MarketData.bat`
  (including `tickers`) refreshes it.
- An empty ETF set or a locked target file logs an error and keeps the old file; the
  step and the price download continue (a red-team review flagged a fatal side export
  as blocking the nightly update). The file is written to a temp name and swapped in.
  Adds an `openpyxl` dependency to a project that was stdlib-only.

Rejected: a separate bat step/script - would duplicate the ZIP read and filtering.

## 2026-09-19 - Added the Inventory-only update

**Type**: feature
**Files**: src/inventory_tickers.py, src/tiingo_data_downloader.py, src/config.py,
tests/test_inventory_tickers.py, README.md, .gitignore;
Systems/run_inventory_update.bat, Systems/Scheduled Tasks/*

The full pipeline refreshes the Tiingo ticker list and downloads ~42,800
tickers (~70 min). Symbols held in the portfolio can be stale for a day
because the full run only starts late. Added a partial update that downloads
just the Inventory sheet's symbols (plus the DDC source symbols), then runs
clean/convert and DDC.

- `inventory_tickers.py` writes `Tickers_Inventory.csv` in the
  `Tickers_to_Update.csv` layout with blank start/end dates, so the existing
  downloader logic applies unchanged (append for existing files, full history
  for new symbols, delisted logic off because no endDate is present).
- `tiingo_data_downloader.py` gained an optional `--tickers <file>`; the
  default and the positional `full|incremental` mode are unchanged.
- Workbook location, columns, exclusions and sources are read from the DDC
  project's `config.toml` and parsed by its `workbook.py`, so Inventory has one
  definition. Loaded by file path because both projects have a top-level
  `config` module.
- The workbook read retries (5 x 60 s, `config.py`) because the 16:16 IBKR
  snapshot writes the same workbook just before the 16:20 run; a mid-save or
  Excel-held file would otherwise fail the day's run.
- Two Task Scheduler imports: inventory at 16:20, full pipeline at 18:00, both
  with stdin from `nul` because the existing batch files `pause` on error.
- `Run_Update_Pipeline.bat` is unchanged apart from the DDC path fix.

Rejected: a lock preventing the inventory and full runs overlapping. The
inventory run takes ~30 min against a 100 min gap to 18:00, and a file lock
would have required editing the full pipeline. The constraint is documented in
the README instead.

Rejected: duplicating the workbook parser in this repo. It would drift from
DDC's Mapped Symbol / exclusion handling.
