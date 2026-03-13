"""
config.py
---------
Centralised configuration for the Tiingo-MD-Mgt-System.
"""

from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
TIINGO_DIR = SCRIPT_DIR / "tiingo"
RAW_DATA_DIR = Path(r"C:\Beaker\Market Data\Raw")
LOG_DIR = SCRIPT_DIR / "logs"

for _d in (RAW_DATA_DIR, LOG_DIR, TIINGO_DIR):
    _d.mkdir(parents=True, exist_ok=True)

APIKEY_FILE = SCRIPT_DIR / "apikey.txt"
SUPPORTED_TICKERS_ZIP = TIINGO_DIR / "supported_tickers.zip"
SUPPORTED_TICKERS_DIR = TIINGO_DIR / "supported_tickers"
TICKERS_TO_UPDATE_FILE = SCRIPT_DIR / "Tickers_to_Update.csv"
LOG_FILE = LOG_DIR / "tiingo_download.log"

TIINGO_BASE_URL = "https://api.tiingo.com/tiingo/daily"
TIINGO_TICKER_LIST_URL = "https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip"

MAX_REQUESTS_PER_HOUR = 20000
DOWNLOAD_TIMEOUT = 30
MAX_RETRIES = 5
RETRY_DELAY = 5

PRICE_CURRENCY = "USD"
ASSET_TYPES = ["Stock", "ETF"]
EXCHANGES = ["AMEX", "BATS", "NASDAQ", "PINK", "OTCQX"]
EXCHANGES_STARTSWITH = "NYSE"

EXCLUDED_CHARS = {
    "-", "$", "^", "&", "*", "(", ")", "[", "]", "{", "}", "|",
    "\\", "/", "<", ">", ",", ".", "?", ";", ":", "@", "#", "~",
    "`", "'", '"', " "
}

FORCE_DOWNLOAD_SYMBOLS = {
    "MLX", "FASMX", "FCVSX", "FSANX", "BALFX", "FFNOX",
    "PGEOX", "PRCPX", "FBALX", "FFGCX", "PWLBX", "EAPCX",
}

EXCLUDE_TICKERS = {
    "ZVZZT", "ZWZZT", "ZXZZT", "ZJZZT", "ZVZZC",
    "ZAZZT", "ZBZZT", "ZCZZT",
    "ZXYZ.A",
    "NTEST", "NTEST.B", "NTEST.C", "NTEST.G", "NTEST.H", "NTEST.L",
    "ATEST", "ATEST.B", "ATEST.C", "ATEST.G", "ATEST.H", "ATEST.L",
    "CTEST", "CTEST.V",
    "ZTEST", "ZBZX", "ZTST",
    "ZIEXT", "ZEXIT", "ZXIET",
    "ZVV", "ZZK", "ZZZ", "IGZ",
    "CBO", "CBX", "IBO",
}

LOG_LEVEL = "DEBUG"
CONSOLE_LOG_LEVEL = "INFO"
CSV_DELIMITER = ","
DISPLAY_INTERVAL = 250
SHOW_PROGRESS = True
