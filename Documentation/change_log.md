# Change Log

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
