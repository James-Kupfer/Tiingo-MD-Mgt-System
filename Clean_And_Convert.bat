@echo off
rem ---------------------------------------------------------------------------
rem  Clean_And_Convert.bat
rem  Rebuild synthetic adjusted prices from raw Tiingo CSVs, then convert the
rem  cleaned CSVs to Parquet.
rem
rem  Step 1 - Clean     Raw\*.csv -> Clean\*.csv, replacing adjOpen/adjHigh/
rem                       adjLow/adjClose/adjVolume with reconstructed values
rem                       (src\clean_prices.py). Raw is untouched.
rem  Step 2 - Convert   Clean\*.csv -> Price\ Parquet dataset (csv2pq, using
rem                       convert_tiingo.toml from the csv_to_pq repo).
rem
rem  Usage:
rem    Clean_And_Convert.bat            -- clean + convert (default, output to Price)
rem    Clean_And_Convert.bat test        -- clean + convert into Price_Test instead
rem
rem  Raw/Clean paths are configured in src\config.py. The csv2pq config path
rem  below assumes the csv_to_pq repo is checked out at its usual location;
rem  edit CSV2PQ_CONFIG if that moves.
rem ---------------------------------------------------------------------------

setlocal
cd /d "%~dp0"

set CSV2PQ_CONFIG=C:\Users\James Kupfer\GitHub\Utilities\Utilities\etl\csv_to_pq\convert_tiingo.toml
if /i "%~1"=="test" set CSV2PQ_CONFIG=C:\Users\James Kupfer\GitHub\Utilities\Utilities\etl\csv_to_pq\convert_tiingo_test.toml

echo.
echo ============================================================
echo  STEP 1: Cleaning raw CSVs (rebuilding adjusted prices)...
echo ============================================================
python "%~dp0src\clean_prices.py"
if errorlevel 1 (
    echo ERROR: clean_prices.py failed. Check output above.
    pause
    exit /b 1
)
echo Cleaning complete.

echo.
echo ============================================================
echo  STEP 2: Converting cleaned CSVs to Parquet...
echo  Config: %CSV2PQ_CONFIG%
echo ============================================================
csv2pq --config "%CSV2PQ_CONFIG%"
if errorlevel 1 (
    echo ERROR: csv2pq conversion failed. Check output above.
    pause
    exit /b 1
)
echo Conversion complete.

endlocal
