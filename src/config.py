"""
config.py
---------
Centralised configuration for the Tiingo-MD-Mgt-System.
"""

from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
TIINGO_DIR = SCRIPT_DIR / "tiingo"
RAW_DATA_DIR = Path(r"C:\documents\investments\market data\raw")
CLEAN_DATA_DIR = Path(r"C:\documents\investments\market data\clean")
WEEKLY_DATA_DIR = Path(r"C:\documents\investments\market data\clean_weekly")
LOG_DIR = SCRIPT_DIR / "logs"

for _d in (RAW_DATA_DIR, CLEAN_DATA_DIR, WEEKLY_DATA_DIR, LOG_DIR, TIINGO_DIR):
    _d.mkdir(parents=True, exist_ok=True)

APIKEY_FILE = SCRIPT_DIR / "apikey.txt"
SUPPORTED_TICKERS_ZIP = TIINGO_DIR / "supported_tickers.zip"
SUPPORTED_TICKERS_DIR = TIINGO_DIR / "supported_tickers"
TICKERS_TO_UPDATE_FILE = SCRIPT_DIR / "Tickers_to_Update.csv"
# Absolute: the Price store lives outside this repo. Written by tiingo_ticker_manager.py
# on every ticker refresh (assetType == "ETF" rows of the filtered ticker list).
ETF_LIST_FILE = Path(r"C:\Documents\Investments\Market Data\Price\ETFs.xlsx")
LOG_FILE = LOG_DIR / "tiingo_download.log"

# --- inventory update (inventory_tickers.py) ---
INVENTORY_TICKERS_FILE = SCRIPT_DIR / "Tickers_Inventory.csv"  # Kept apart from Tickers_to_Update.csv so a full run's file is never clobbered
# Absolute: the DDC project lives outside this repo. Its config.toml supplies the
# workbook path/sheet/columns/exclusions and its src\workbook.py parses the sheet.
DDC_DIR = Path(r"C:\Documents\Investments\Systems\Correlation Analysis")
# The workbook can be mid-save or held by Excel (the 16:16 IBKR snapshot writes it),
# which surfaces as a read error. Retry before failing the run.
INVENTORY_READ_ATTEMPTS = 5
INVENTORY_READ_RETRY_SECONDS = 60

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
"ACIZX", "ADANX", "AEMGX", "ALARX", "ALCFX", "ALGRX", "ALAFX", "ANFCX", "ANEFX", "ANFFX", "AQGNX", "AQMNX", "AQRNX", "ARCNX", "AUERX", "AUENX", "AVALX", "BALFX", "BBTGX", "BINCX", "BISAX", "BLDG", "BPLEX", "BPLSX", "BRNY", "BRXAX", "BRXIX", "CFIT", "CFIPX", "CEMDX", "CEMFX", "CHASX", "CLSE", "CVISX", "CWSGX", "DAINX", "DARP", "DEMAX", "DEMCX", "DMAGX", "DRES", "DYNF", "EACOX", "EAPCX", "EICOX", "EISAX", "EISIX", "EKBAX", "EKBCX", "ENDW", "EYLD", "FASMX", "FBGRX", "FBMPX", "FBALX", "FCNTX", "FCVSX", "FGINX", "FGIPX", "FGRTX", "FHKCX", "FFGCX", "FFNOX", "FIUIX", "FLCSX", "FNARX", "FOCPX", "FPURX", "FSDAX", "FSAGX", "FSANX", "FSTRX", "FSTKX", "FWD", "FYLD", "GAA", "GAAAX", "GAAHX", "GAAWX", "GBMSX", "GCCAX", "GCSLX", "GEMNX", "GEQ", "GEW", "GLLAX", "GLOSX", "GMOC", "GMOD", "GMOI", "GMOM", "GMOV", "GMWRX", "GQESX", "GRHAX", "GSXMX", "GVAL", "HRIIX", "HRIOX", "HTUS", "IDEQ", "INVG", "JAKUX", "KWH", "LZEMX", "LZOEX", "LYLD", "MDLRX", "MFUT", "MGLBX", "MMEAX", "MIOFX", "MLX", "MSTQ", "MXXIX", "MYLD", "PAGDX", "PAGHX", "PAGRX", "PEMX", "PGEOX", "PFSLX", "PJFV", "PKAAX", "POGRX", "PRCPX", "PWLBX", "PWRD", "QAACX", "QCENX", "QCFNX", "QDSNX", "QGMNX", "QHFNX", "QIACX", "QICNX", "QILGX", "QLENX", "QLFNX", "QLTI", "QLTY", "QMFNX", "QMHNX", "QMNNX", "QNZNX", "QRPNX", "QSMNX", "QSPNX", "QTENX", "SBHEX", "SEIV", "SEIM", "SIXH", "SMYIX", "SPIT", "SPY", "SVXAX", "SYLD", "TAIL", "TAX", "TAAGX", "TEBRX", "TEDMX", "THOAX", "TIBAX", "TIBCX", "TRTY", "TYLD", "USEW", "VAMO", "VFMO", "VPMCX", "VPCCX", "VPMAX", "VVOAX", "VVOCX", "WFGGX", "WWWEX"
}

EXCLUDE_TICKERS = {
    "ZVZZT", "ZWZZT", "ZXZZT", "ZJZZT", "ZVZZC",
    "ZAZZT", "ZBZZT", "ZCZZT", "ZXYZ.A",
    "NTEST", "NTEST.B", "NTEST.C", "NTEST.G", "NTEST.H", "NTEST.L",
    "ATEST", "ATEST.B", "ATEST.C", "ATEST.G", "ATEST.H", "ATEST.L",
    "CTEST", "CTEST.V",
    "ZTEST", "ZBZX", "ZTST",
    "ZIEXT", "ZEXIT", "ZXIET",
    "ZVV", "ZZK", "ZZZ", "IGZ",
    "CBO", "CBX", "IBO",
}

TEST_TICKER_LIMIT = 0  # Cap tickers processed; 0 = no limit

# --- clean_prices.py ---
# Relative variance threshold for treating same-date rows from different
# source files (e.g. TICKER.csv vs TICKER_dlist.csv) as describing the same
# trading day. Below this, differences (vendor rounding/snapshot noise) are
# immaterial; above it, the date is logged to _merge_conflicts.csv.
MERGE_VARIANCE_THRESHOLD = 0.01

LOG_LEVEL = "DEBUG"  # Minimum severity written to the log file
LOG_RETENTION_DAYS = 90  # Log files older than this are purged at the start of each logged run
CONSOLE_LOG_LEVEL = "INFO"
CSV_DELIMITER = ","
DISPLAY_INTERVAL = 250
SHOW_PROGRESS = True
