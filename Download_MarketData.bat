@echo off
rem ---------------------------------------------------------------------------
rem  DownloadMarketData.bat
rem  Single entry point for the Tiingo-MD-Mgt-System pipeline.
rem
rem  Step 1 - Ticker update   Download, extract, filter, and save the Tiingo
rem                            supported-tickers list to Tickers_to_Update.csv
rem                            and active_list.csv.
rem  Step 2 - Cleanup         Delete the temporary ZIP and extracted folder.
rem  Step 3 - Data download   Download / incrementally update per-ticker
rem                            price CSVs in the raw directory.
rem  Step 4 - Final cleanup   Delete active_list.csv and Tickers_to_Update.csv
rem
rem  Usage:
rem    DownloadMarketData.bat              -- incremental data download (default)
rem    DownloadMarketData.bat full         -- full overwrite data download
rem    DownloadMarketData.bat incremental  -- incremental data download
rem    DownloadMarketData.bat tickers      -- update ticker list only, no download
rem    DownloadMarketData.bat incremental --refetch-days N
rem                                        -- also re-request the last N days and
rem                                           replace revised (preliminary) rows
rem
rem  All settings are configured in src\config.py.
rem ---------------------------------------------------------------------------

setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ------------------------------------------------------------------
rem  Python check
rem ------------------------------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

rem ------------------------------------------------------------------
rem  Parse argument
rem ------------------------------------------------------------------
set MODE=incremental
set TICKERSONLY=0
set REFETCH=
if /i "%~2"=="--refetch-days" set "REFETCH=--refetch-days %~3"

if /i "%~1"=="full"        set MODE=full
if /i "%~1"=="incremental" set MODE=incremental
if /i "%~1"=="tickers"     set TICKERSONLY=1

if not "%~1"=="" (
    if /i not "%~1"=="full" (
        if /i not "%~1"=="incremental" (
            if /i not "%~1"=="tickers" (
                echo ERROR: Invalid argument "%~1".
                echo Usage: DownloadMarketData.bat [full^|incremental^|tickers]
                pause
                exit /b 1
            )
        )
    )
)

rem ------------------------------------------------------------------
rem  STEP 1 -- Update ticker list
rem ------------------------------------------------------------------
echo.
echo ============================================================
echo  STEP 1: Updating ticker list...
echo ============================================================
python "%~dp0src\tiingo_ticker_manager.py"
set STEP1EXIT=%errorlevel%
if %STEP1EXIT% neq 0 (
    echo ERROR: Ticker list update failed ^(exit code %STEP1EXIT%^).
    echo Check the logs folder for details.
    pause
    exit /b %STEP1EXIT%
)
echo Ticker list updated successfully.

rem ------------------------------------------------------------------
rem  STEP 2 -- Clean up temporary ZIP and extracted staging folder
rem ------------------------------------------------------------------
echo.
echo ============================================================
echo  STEP 2: Cleaning up temporary staging files...
echo ============================================================

if exist "%~dp0tiingo\supported_tickers.zip" (
    del /f /q "%~dp0tiingo\supported_tickers.zip"
    echo   Deleted tiingo\supported_tickers.zip
)

if exist "%~dp0tiingo\supported_tickers" (
    rmdir /s /q "%~dp0tiingo\supported_tickers"
    echo   Deleted tiingo\supported_tickers\
)

echo   Cleanup complete.



rem ------------------------------------------------------------------
rem  STEP 3 -- Download market data (skipped for tickers mode)
rem ------------------------------------------------------------------
if %TICKERSONLY%==1 (
    echo.
    echo Tickers-only mode: skipping data download.
    goto cleanup_csv
)

echo.
echo ============================================================
echo  STEP 3: Downloading market data (mode: %MODE%)...
echo ============================================================
python "%~dp0src\tiingo_data_downloader.py" %MODE% %REFETCH%
set STEP3EXIT=%errorlevel%
if %STEP3EXIT% neq 0 (
    echo ERROR: Market data download failed ^(exit code %STEP3EXIT%^).
    echo Check the logs folder for details.
    pause
    exit /b %STEP3EXIT%
)
echo Market data download complete.

rem ------------------------------------------------------------------
rem  STEP 4 -- Delete ticker CSVs now that download is complete
rem ------------------------------------------------------------------
:cleanup_csv
echo.
echo ============================================================
echo  STEP 4: Removing ticker list CSVs...
echo ============================================================

if exist "%~dp0active_list.csv" (
    del /f /q "%~dp0active_list.csv"
    echo   Deleted active_list.csv
)

if exist "%~dp0Tickers_to_Update.csv" (
    del /f /q "%~dp0Tickers_to_Update.csv"
    echo   Deleted Tickers_to_Update.csv
)

echo   Done.

:done
echo.
echo ============================================================
echo  ALL STEPS COMPLETE
echo ============================================================
timeout /t 5 /nobreak >nul
exit /b 0
