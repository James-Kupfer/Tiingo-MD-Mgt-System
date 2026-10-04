"""
clean_prices.py
----------------
Reconstructs synthetic adjusted prices from raw Tiingo OHLCV CSVs, run before
csv_to_pq conversion. The synthetic values REPLACE adjOpen/adjHigh/adjLow/
adjClose/adjVolume in place (same column names, output schema unchanged from
input) so downstream consumers need no changes. Raw open/high/low/close/
volume/divCash/splitFactor pass through untouched.

Why: Tiingo's own adjClose/adjOpen/adjHigh/adjLow/adjVolume columns are
occasionally wrong (bad prints, missed or duplicated corporate actions).
Rather than trust adjClose blindly, this module rebuilds an adjusted series
day-by-day, choosing between the raw-close return and the vendor-adjusted
return using the smaller-magnitude one as the more likely correct value,
except on genuine split/dividend days, where the expected adjustment is
independently derived from splitFactor/divCash and used to validate (or
override) Tiingo's adjClose.

Chaining runs backward from the most recent row (adjusted close == close on
the last day, since there are no future corporate actions to adjust for) so
the synthetic series is anchored to today's actual price, matching Tiingo's
own back-adjustment convention.

A single per-day price multiplier (synthetic close / close) is applied
uniformly to open/high/low/close so intraday OHLC ordering is preserved
exactly. Volume is adjusted separately using a split-only cumulative factor,
since dividends do not change shares outstanding.
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
import sys
from pathlib import Path

try:
    from config import RAW_DATA_DIR, CLEAN_DATA_DIR, MERGE_VARIANCE_THRESHOLD
except ImportError:
    print("ERROR: config.py not found in the src/ directory.")
    sys.exit(1)

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = (
    "symbol", "date", "close", "high", "low", "open", "volume",
    "adjClose", "adjHigh", "adjLow", "adjOpen", "adjVolume",
    "divCash", "splitFactor",
)
# "Repaired" = "True" on any row where a raw field was missing/unparseable
# and got filled in by a repair rule (see _repair_symbol_rows); left blank
# (not "False") on every other row -- absence means the record is assumed
# accurate. Does NOT cover the routine adjOpen/adjHigh/adjLow/adjClose/
# adjVolume reconstruction -- that's this tool's normal, always-on job, not
# an exception. Downstream csv2pq TOML schemas must declare this column too.
#
# Kept as a plain string ("True"/"") rather than a real boolean column: the
# installed csv_to_pq's Utf8->Boolean cast (polars 1.42.1) raises
# "casting from Utf8View to Boolean not supported" -- confirmed by testing
# directly, not a hypothetical -- so declaring this a bool column in the
# TOML schema would crash the conversion. "True"/blank is the closest
# boolean-shaped representation that survives the pipeline intact.
OUTPUT_COLUMNS = REQUIRED_COLUMNS + ("Repaired",)

# A ticker's live files can be split across multiple raw files (e.g. a
# duplicate export); merge_symbol_files combines files already confirmed to
# describe the same entity (see _connected_components: files sharing at
# least one date, transitively) into one continuous series. It's used for
# both a ticker's live series and, separately, its valid dlist series -- the
# two are never merged with each other (see clean_directory).
MERGE_COMPARE_FIELDS = ("close", "high", "low", "open", "volume", "divCash", "splitFactor")
DEFAULT_MERGE_TOLERANCE = MERGE_VARIANCE_THRESHOLD  # relative/absolute tolerance for "same data"

# Max allowed relative disagreement between Tiingo's adjClose-implied return
# and the independently-derived split/dividend return on a corporate-action
# day before we discard Tiingo's value and use our own.
DEFAULT_TOLERANCE = 0.01

# A real split/dividend MUST move the observed close by roughly the implied
# amount -- that's mechanical, not optional. So a splitFactor/divCash value
# implying a large move with NO corresponding price change is a reliable
# signal of a spurious/corrupted value, auto-corrected in _repair_symbol_rows
# rather than merely flagged: at scale (tens of thousands of files) nothing
# is manually reviewing these, so the rule needs to resolve them, not punt.
# Values with real price evidence behind them (even if imprecise -- same-day
# trading noise on top of the split is normal) are left untouched.
SPLIT_CONTRADICTION_RATIO = 2.0  # splitFactor outside [1/this, this] implies a real move
DIV_CASH_MAX_FRACTION_OF_CLOSE = 0.5  # a dividend bigger than half the price implies a real drop
NO_PRICE_MOVE_TOLERANCE = 0.10  # actual close ratio within this of 1.0 = "didn't move"


def _is_bad(d: dict, field: str) -> bool:
    try:
        float(d.get(field))
        return False
    except (TypeError, ValueError):
        return True


def _repair_symbol_rows(rows: list[dict], symbol: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Repair missing/blank/non-numeric OHLCV fields where a safe rule
    exists, otherwise skip the row. rows must already be sorted ascending by
    date -- the open repair rule needs the prior day's (already-resolved)
    close, and so does the split/dividend contradiction check below.

    Repair rules, applied in this order (each stage's inputs are guaranteed
    valid by the stage before it):
      1. close: if bad, and open/high/low are ALL valid, close =
         (open+high+low)/3. If close is bad AND any of open/high/low is also
         bad, there isn't enough same-day signal to estimate it -- skip the
         row entirely (close anchors every downstream computation in
         clean_symbol_rows; a wrong value there corrupts neighboring days
         too, not just this one).
      2. open: if bad, open = prior day's resolved close (no-gap
         assumption); same-day close if there's no prior day.
      3. low: if bad, low = min(open, close).
      4. high: if bad, high = max(open, close).
      5. volume: if bad, volume = 0.
      6. divCash / splitFactor: if bad, default to 0.0 / 1.0 (no corporate
         action) -- matches the fallback already used elsewhere in this file
         for these two fields.
      7. splitFactor / divCash contradiction check: a real split/dividend
         MUST move the observed close by roughly the implied amount. If
         splitFactor implies a >=2x move (or divCash implies a drop of more
         than half the close) but the actual close-to-close ratio shows the
         price didn't move (within NO_PRICE_MOVE_TOLERANCE of 1.0), the
         value has no supporting price evidence and is corrected to 1.0/0.0
         (no corporate action). Values with real price evidence behind them
         -- even imprecise, since same-day trading noise on top of a split
         is normal -- are left untouched. This can't be manual review at
         scale (tens of thousands of files), so it has to be a rule, not a
         flag: see conversation for validation against real examples
         (AREN/HOFV/JAGX kept as legitimate reverse splits; DXBGF/BIMI
         corrected -- both had splitFactor implying a large move with the
         close completely unchanged).

    Any repair or correction sets that row's "Repaired" field to "True";
    rows needing neither get "" (not "False" -- see OUTPUT_COLUMNS comment).

    Returns:
        (repaired_rows, skipped, corrections) — skipped lists one dict per
        dropped row (symbol, date, fields, reason); corrections lists one
        dict per splitFactor/divCash value overridden by stage 7 (symbol,
        date, field, original_value, corrected_value, reason).
    """
    repaired: list[dict] = []
    skipped: list[dict] = []
    corrections: list[dict] = []
    last_valid_close: float | None = None

    for row in rows:
        out = dict(row)
        out["symbol"] = symbol  # normalize; see _normalize_symbol for why a row's own value may be wrong
        was_repaired = False

        close_bad = _is_bad(out, "close")
        open_bad = _is_bad(out, "open")
        high_bad = _is_bad(out, "high")
        low_bad = _is_bad(out, "low")

        if close_bad:
            if open_bad or high_bad or low_bad:
                bad = [f for f, b in (("close", True), ("open", open_bad), ("high", high_bad), ("low", low_bad)) if b]
                skipped.append({
                    "symbol": symbol,
                    "date": row.get("date", ""),
                    "fields": ";".join(bad),
                    "reason": "close missing and open/high/low insufficient to estimate it",
                })
                continue
            out["close"] = repr((float(out["open"]) + float(out["high"]) + float(out["low"])) / 3.0)
            was_repaired = True

        close_val = float(out["close"])

        if open_bad:
            out["open"] = repr(last_valid_close if last_valid_close is not None else close_val)
            was_repaired = True
        open_val = float(out["open"])

        if low_bad:
            out["low"] = repr(min(open_val, close_val))
            was_repaired = True

        if high_bad:
            out["high"] = repr(max(open_val, close_val))
            was_repaired = True

        if _is_bad(out, "volume"):
            out["volume"] = "0"
            was_repaired = True

        if _is_bad(out, "divCash"):
            out["divCash"] = "0.0"
            was_repaired = True

        if _is_bad(out, "splitFactor"):
            out["splitFactor"] = "1.0"
            was_repaired = True

        if last_valid_close is not None and last_valid_close > 0:
            actual_ratio = close_val / last_valid_close
            no_price_move = abs(actual_ratio - 1.0) < NO_PRICE_MOVE_TOLERANCE

            sf = float(out["splitFactor"])
            if no_price_move and sf != 1.0 and (sf > SPLIT_CONTRADICTION_RATIO or sf < 1.0 / SPLIT_CONTRADICTION_RATIO):
                corrections.append({
                    "symbol": symbol, "date": row.get("date", ""), "field": "splitFactor",
                    "original_value": out["splitFactor"], "corrected_value": "1.0",
                    "reason": f"implies {1/sf:.1f}x move but close unchanged ({actual_ratio:.3f}x)",
                })
                out["splitFactor"] = "1.0"
                was_repaired = True

            dc = float(out["divCash"])
            if no_price_move and close_val > 0 and dc > DIV_CASH_MAX_FRACTION_OF_CLOSE * close_val:
                corrections.append({
                    "symbol": symbol, "date": row.get("date", ""), "field": "divCash",
                    "original_value": out["divCash"], "corrected_value": "0.0",
                    "reason": f"implies a drop of {dc:.2f} but close unchanged ({actual_ratio:.3f}x)",
                })
                out["divCash"] = "0.0"
                was_repaired = True

        # csv2pq schemas volume as int64; raw Tiingo data sometimes formats it
        # as a float-string ("751.0"), and polars silently NULLs (not
        # truncates) a decimal-point string on a strict=False Int64 cast --
        # confirmed by testing directly. Normalize here so a value that was
        # never actually missing doesn't get silently dropped downstream.
        out["volume"] = str(int(float(out["volume"])))

        out["Repaired"] = "True" if was_repaired else ""
        last_valid_close = close_val
        repaired.append(out)

    return repaired, skipped, corrections


def clean_symbol_rows(rows: list[dict], tolerance: float = DEFAULT_TOLERANCE) -> tuple[list[dict], list[dict]]:
    """Compute synthetic adjusted OHLCV for one symbol's rows.

    Args:
        rows: CSV rows for a single symbol, each a dict with REQUIRED_COLUMNS
            keys (string values, as read by csv.DictReader). Must be sorted
            ascending by date before calling.
        tolerance: Relative tolerance for accepting Tiingo's adjClose return
            on a corporate-action day before falling back to the
            self-computed expected return.

    Returns:
        (output_rows, discrepancies) where output_rows has all original
        fields plus SYN_COLUMNS (as strings, ready to write), and
        discrepancies lists one dict per day where Tiingo's adjClose was
        overridden (symbol, date, vendor_ratio, expected_ratio, delta).
    """
    n = len(rows)
    if n == 0:
        return [], []

    parsed = [_parse_row(r) for r in rows]
    discrepancies: list[dict] = []

    # synClose multiplier chained backward from the last (most recent) row.
    syn_close = [0.0] * n
    syn_close[-1] = parsed[-1]["close"]

    for t in range(n - 2, -1, -1):
        cur = parsed[t]
        nxt = parsed[t + 1]
        is_corp_action = nxt["splitFactor"] != 1.0 or nxt["divCash"] != 0.0

        if not is_corp_action:
            raw_ratio = _safe_ratio(nxt["close"], cur["close"])
            adj_ratio = _safe_ratio(nxt["adjClose"], cur["adjClose"])
            raw_ret = raw_ratio - 1.0 if raw_ratio is not None else None
            adj_ret = adj_ratio - 1.0 if adj_ratio is not None else None
            if raw_ret is None and adj_ret is None:
                chosen_ratio = 1.0
            elif adj_ret is None:
                chosen_ratio = raw_ratio
            elif raw_ret is None:
                chosen_ratio = adj_ratio
            else:
                chosen_ratio = raw_ratio if abs(raw_ret) <= abs(adj_ret) else adj_ratio
        else:
            split_factor = nxt["splitFactor"] if nxt["splitFactor"] > 0 else 1.0
            expected_prior_close = (cur["close"] - nxt["divCash"]) / split_factor
            expected_ratio = _safe_ratio(nxt["close"], expected_prior_close)
            vendor_ratio = _safe_ratio(nxt["adjClose"], cur["adjClose"])

            if expected_ratio is None:
                chosen_ratio = vendor_ratio if vendor_ratio is not None else 1.0
            elif vendor_ratio is None:
                chosen_ratio = expected_ratio
            else:
                delta = abs(vendor_ratio - expected_ratio)
                rel_delta = delta / expected_ratio if expected_ratio else delta
                if rel_delta <= tolerance:
                    chosen_ratio = vendor_ratio
                else:
                    chosen_ratio = expected_ratio
                    discrepancies.append({
                        "symbol": cur["symbol"],
                        "date": nxt["date"],
                        "vendor_ratio": vendor_ratio,
                        "expected_ratio": expected_ratio,
                        "rel_delta": rel_delta,
                    })
                    logger.warning(
                        "%s %s: adjClose disagrees with split/div-derived adjustment "
                        "by %.2f%% (vendor=%.6f expected=%.6f); using expected.",
                        cur["symbol"], nxt["date"], rel_delta * 100, vendor_ratio, expected_ratio,
                    )

        syn_close[t] = syn_close[t + 1] / chosen_ratio if chosen_ratio else syn_close[t + 1]

    # Split-only cumulative factor for volume (dividends don't change share count).
    split_factor_cum = [1.0] * n
    for t in range(n - 2, -1, -1):
        nxt_split = parsed[t + 1]["splitFactor"]
        split_factor_cum[t] = split_factor_cum[t + 1] * (nxt_split if nxt_split > 0 else 1.0)

    output_rows = []
    for i, row in enumerate(rows):
        close = parsed[i]["close"]
        multiplier = syn_close[i] / close if close else 1.0
        out = dict(row)
        out["adjOpen"] = repr(parsed[i]["open"] * multiplier)
        out["adjHigh"] = repr(parsed[i]["high"] * multiplier)
        out["adjLow"] = repr(parsed[i]["low"] * multiplier)
        out["adjClose"] = repr(syn_close[i])
        out["adjVolume"] = repr(round(parsed[i]["volume"] * split_factor_cum[i]))
        output_rows.append(out)

    return output_rows, discrepancies


def clean_file(input_path: Path, output_path: Path, tolerance: float = DEFAULT_TOLERANCE) -> list[dict]:
    """Clean a single-source symbol CSV (no cross-file merging), writing the
    enriched file to output_path. Convenience wrapper for ad hoc use;
    clean_directory() uses group_files_by_symbol()/merge_symbol_files() so
    multi-file symbols (e.g. TICKER.csv + TICKER_dlist.csv) are merged first.

    Returns the list of discrepancies logged for this symbol (see
    clean_symbol_rows).
    """
    with input_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    missing = [c for c in REQUIRED_COLUMNS if rows and c not in rows[0]]
    if missing:
        raise ValueError(f"{input_path.name}: missing required columns {missing}")

    rows.sort(key=lambda r: r["date"])
    output_rows, discrepancies = clean_symbol_rows(rows, tolerance=tolerance)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(output_rows)

    return discrepancies


def _ticker_from_filename(path: Path) -> tuple[str, bool]:
    """Derive (base_ticker, is_dlist) from the filename alone.

    The filename's "_dlist" suffix is the reliable signal for identity --
    the CSV's own symbol column is NOT: ~17% of *_dlist.csv files (1,960 of
    11,611, confirmed by full-directory scan) carry symbol="TICKER_dlist" in
    every row, while most carry a clean "TICKER". The filename convention is
    consistent across all 34k+ files; the internal symbol column isn't, so
    grouping/validity decisions are made on the filename, not that column.
    """
    stem = path.stem
    if stem.upper().endswith("_DLIST"):
        return stem[: -len("_DLIST")].upper(), True
    return stem.upper(), False


def group_files_by_ticker(files: list[Path]) -> dict[str, dict[str, list[Path]]]:
    """Group raw CSV files by base ticker (from filename, see
    _ticker_from_filename), splitting each group into "live" (non-_dlist)
    and "dlist" file lists.

    clean_directory() uses this split to decide, per ticker, which _dlist
    files are valid (kept as their own TICKER_DLIST series) vs invalid
    (discarded -- see the dlist-validity rule in clean_directory's docstring).
    """
    groups: dict[str, dict[str, list[Path]]] = {}
    for path in files:
        ticker, is_dlist = _ticker_from_filename(path)
        bucket = groups.setdefault(ticker, {"live": [], "dlist": []})
        bucket["dlist" if is_dlist else "live"].append(path)
    return groups


def _connected_components(file_rows: dict[Path, list[dict]]) -> list[list[Path]]:
    """Partition files sharing a symbol into groups that actually overlap in
    date.

    Two files are linked if they share at least one date (transitively, so
    A-B-C all merge if A/B overlap and B/C overlap, even if A/C don't).
    Files with zero date overlap end up in separate components — sharing a
    symbol string alone (ticker reuse after delisting) is not evidence they
    describe the same underlying security.
    """
    paths = list(file_rows)
    date_sets = {p: {r["date"] for r in file_rows[p]} for p in paths}
    parent = {p: p for p in paths}

    def find(x: Path) -> Path:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: Path, b: Path) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            if date_sets[paths[i]] & date_sets[paths[j]]:
                union(paths[i], paths[j])

    components: dict[Path, list[Path]] = {}
    for p in paths:
        components.setdefault(find(p), []).append(p)
    return list(components.values())


ROLLING_WINDOW_DAYS = 5


def merge_symbol_files(
    file_rows: dict[Path, list[dict]],
    paths: list[Path],
    symbol: str,
    float_tolerance: float = DEFAULT_MERGE_TOLERANCE,
    window_days: int = ROLLING_WINDOW_DAYS,
) -> tuple[list[dict], list[dict]]:
    """Merge rows for one confirmed-same-entity component (paths that share
    at least one date, directly or transitively — see _connected_components).

    Rows are deduplicated by date, processed oldest to newest. When two+
    files report a row for the same date, each MERGE_COMPARE_FIELDS field is
    resolved independently: if that field disagrees beyond float_tolerance,
    the candidate value closer to the trailing window_days average of that
    field (over prior *resolved* rows) is kept — this is what catches
    magnitude errors like a 600-vs-60,000 volume mismatch, since the wrong
    one will be wildly off-trend while the right one won't. Fields with no
    trailing history yet (start of the series) fall back to preferring the
    file whose name doesn't end in "_dlist". Every field-level decision is
    recorded for audit.

    Returns:
        (merged_rows, conflicts) — merged_rows is one row per date (dicts
        with MERGE_COMPARE_FIELDS resolved, sorted ascending by date),
        conflicts lists one dict per resolved field disagreement (symbol,
        date, field, kept_value, kept_file, rejected_value, rejected_file,
        method).
    """
    by_date: dict[str, list[tuple[Path, dict]]] = {}
    for path in paths:
        for row in file_rows[path]:
            by_date.setdefault(row["date"], []).append((path, row))

    history: dict[str, list[float]] = {f: [] for f in MERGE_COMPARE_FIELDS}
    merged_rows: list[dict] = []
    conflicts: list[dict] = []

    for date_str in sorted(by_date):
        entries = by_date[date_str]
        entries_sorted = sorted(entries, key=lambda e: ("_dlist" in e[0].stem.lower(), e[0].name))
        preferred_path, preferred_row = entries_sorted[0]
        resolved_row = dict(preferred_row)

        if len(entries) > 1:
            for field in MERGE_COMPARE_FIELDS:
                candidates = []  # (path, row, float_value)
                for path, row in entries_sorted:
                    try:
                        candidates.append((path, row, float(row.get(field, ""))))
                    except (TypeError, ValueError):
                        pass
                if len(candidates) < 2:
                    continue
                values = [v for _, _, v in candidates]
                if math.isclose(min(values), max(values), rel_tol=float_tolerance, abs_tol=float_tolerance):
                    continue  # all candidates agree within tolerance

                trail = history[field][-window_days:]
                if trail:
                    trail_avg = sum(trail) / len(trail)
                    kept_path, kept_row, kept_val = min(candidates, key=lambda c: abs(c[2] - trail_avg))
                    method = f"rolling_avg({len(trail)}d)"
                else:
                    kept_path, kept_row, kept_val = candidates[0]  # already file-preference sorted
                    method = "file_preference (no trailing history)"

                resolved_row[field] = kept_row[field]
                for path, row, val in candidates:
                    if path == kept_path:
                        continue
                    conflicts.append({
                        "symbol": symbol,
                        "date": date_str,
                        "field": field,
                        "kept_value": kept_row.get(field, ""),
                        "kept_file": kept_path.name,
                        "rejected_value": row.get(field, ""),
                        "rejected_file": path.name,
                        "method": method,
                    })

        for field in MERGE_COMPARE_FIELDS:
            try:
                history[field].append(float(resolved_row.get(field, "")))
            except (TypeError, ValueError):
                pass

        merged_rows.append(resolved_row)

    return merged_rows, conflicts


def load_ticker_filter(path: Path) -> set[str]:
    """Read the upper-cased ``ticker`` column of a Tickers_*.csv file.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        ValueError: The file has no ``ticker`` column or lists no tickers.
    """
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "ticker" not in (reader.fieldnames or []):
            raise ValueError(f"{path} has no 'ticker' column")
        tickers = {r["ticker"].strip().upper() for r in reader if r["ticker"].strip()}
    if not tickers:
        raise ValueError(f"{path} lists no tickers")
    return tickers


def clean_directory(
    input_dir: Path,
    output_dir: Path,
    pattern: str = "*.csv",
    tolerance: float = DEFAULT_TOLERANCE,
    merge_tolerance: float = DEFAULT_MERGE_TOLERANCE,
    tickers: set[str] | None = None,
) -> None:
    """Clean every CSV in input_dir matching pattern, writing up to two
    output files per ticker: ``{TICKER}.csv`` (live data, symbol=TICKER) and
    ``{TICKER}_DLIST.csv`` (delisted data, symbol=TICKER_DLIST) -- kept as
    permanently separate series, never merged into one another.

    Grouping is by ticker derived from the FILENAME (see
    _ticker_from_filename), not the CSV's own symbol column, which is
    unreliable (~17% of *_dlist.csv files carry symbol=TICKER_dlist in every
    row; most carry a clean TICKER).

    A ticker's live files (if any) are merged among themselves the usual way
    (_connected_components + merge_symbol_files) into ``{TICKER}.csv``. Its
    dlist files are validated against the live series' dates: a dlist file
    that shares ANY date with the live series is invalid -- a genuine
    delisting doesn't trade concurrently with a live listing under the same
    ticker, so overlap means the file doesn't describe a truly distinct
    delisted entity -- and is discarded whole (logged to
    ``_invalid_dlist_removed.csv``), not partially merged. Dlist files with
    no overlap (including when there's no live file at all) are valid and
    become ``{TICKER}_DLIST.csv``; if a ticker has multiple valid dlist files
    that are themselves mutually non-overlapping (rare -- multiple distinct
    delisted entities reusing the same old ticker), only the most recent is
    kept, the rest excluded and logged to ``_disjoint_series_excluded.csv``.

    Writes ``_discrepancies.csv`` (adjClose overrides, see clean_symbol_rows),
    ``_merge_conflicts.csv`` (same-date disagreements within a confirmed
    component), ``_disjoint_series_excluded.csv``, and
    ``_invalid_dlist_removed.csv`` into output_dir.

    ``tickers``, when given, restricts the run to those base tickers (see
    load_ticker_filter); None cleans every file matching ``pattern``.
    """
    files = sorted(input_dir.glob(pattern))
    groups = group_files_by_ticker(files)
    if tickers is not None:
        # Subset run (the Market Data Pipeline inventory step): only the listed
        # base tickers, each with its _dlist files, exactly as a full run would.
        missing = sorted(tickers - groups.keys())
        if missing:
            logger.warning("%d listed ticker(s) have no raw file: %s", len(missing), missing)
        groups = {t: b for t, b in groups.items() if t in tickers}
        files = [p for b in groups.values() for p in b["live"] + b["dlist"]]
    n_with_dlist = sum(1 for b in groups.values() if b["dlist"])
    logger.info(
        "Cleaning %d file(s) from %s -> %s (%d ticker(s), %d with a _dlist file)",
        len(files), input_dir, output_dir, len(groups), n_with_dlist,
    )

    all_discrepancies: list[dict] = []
    all_conflicts: list[dict] = []
    all_excluded: list[dict] = []
    all_skipped: list[dict] = []
    all_corrections: list[dict] = []
    all_invalid_dlist: list[dict] = []
    n_ok = 0
    n_failed = 0

    for ticker in sorted(groups):
        live_paths = groups[ticker]["live"]
        dlist_paths = groups[ticker]["dlist"]

        file_rows: dict[Path, list[dict]] = {}
        for path in live_paths + dlist_paths:
            try:
                with path.open(newline="", encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))
            except OSError as exc:
                logger.warning("Skipping %s: could not read (%s)", path.name, exc)
                continue
            if rows:
                file_rows[path] = rows
        live_paths = [p for p in live_paths if p in file_rows]
        dlist_paths = [p for p in dlist_paths if p in file_rows]

        # --- live series: TICKER.csv / symbol=TICKER ---
        live_dates: set[str] = set()
        if live_paths:
            canonical, excluded = _pick_canonical_component(file_rows, live_paths, ticker, "live")
            for comp in excluded:
                all_excluded.append(_excluded_record(file_rows, comp, ticker))
            live_dates = {r["date"] for p in canonical for r in file_rows[p]}

            try:
                discrepancies, conflicts, skipped, corrections = _clean_and_write_series(
                    file_rows, canonical, ticker, output_dir, tolerance, merge_tolerance,
                )
                all_discrepancies.extend(discrepancies)
                all_conflicts.extend(conflicts)
                all_skipped.extend(skipped)
                all_corrections.extend(corrections)
                n_ok += 1
            except (ValueError, TypeError, OSError) as exc:
                # TypeError is a backstop: a truncated CSV row (fewer fields
                # than the header) leaves missing fields as None, and
                # float(None) raises TypeError rather than ValueError.
                # Without this, one malformed row anywhere in a 30k+ file
                # batch would crash the whole run instead of just this series.
                logger.error("Failed to clean %s: %s", ticker, exc)
                n_failed += 1

        # --- dlist series: TICKER_DLIST.csv / symbol=TICKER_DLIST ---
        # A dlist file is valid only if its dates don't overlap the live
        # series (including when there's no live file at all -- trivially no
        # overlap). Any overlap means the "delisted" data coincides with data
        # also present in the live feed, which isn't how a genuine delisting
        # works -- that file is invalid and discarded whole, not partially
        # merged (see conversation: this is a deliberately coarse, automated
        # rule, since nothing reviews these individually at this volume).
        if dlist_paths:
            valid_dlist_paths = []
            for p in dlist_paths:
                dates = {r["date"] for r in file_rows[p]}
                overlap = dates & live_dates
                if overlap:
                    all_invalid_dlist.append({
                        "symbol": ticker,
                        "file": p.name,
                        "overlap_count": len(overlap),
                        "overlap_date_min": min(overlap),
                        "overlap_date_max": max(overlap),
                    })
                    logger.warning(
                        "%s: %s overlaps %d date(s) with the live file -- invalid dlist, discarding entirely",
                        ticker, p.name, len(overlap),
                    )
                else:
                    valid_dlist_paths.append(p)

            if valid_dlist_paths:
                dlist_symbol = f"{ticker}_DLIST"
                canonical, excluded = _pick_canonical_component(file_rows, valid_dlist_paths, dlist_symbol, "dlist")
                for comp in excluded:
                    all_excluded.append(_excluded_record(file_rows, comp, dlist_symbol))

                try:
                    discrepancies, conflicts, skipped, corrections = _clean_and_write_series(
                        file_rows, canonical, dlist_symbol, output_dir, tolerance, merge_tolerance,
                    )
                    all_discrepancies.extend(discrepancies)
                    all_conflicts.extend(conflicts)
                    all_skipped.extend(skipped)
                    all_corrections.extend(corrections)
                    n_ok += 1
                except (ValueError, TypeError, OSError) as exc:
                    logger.error("Failed to clean %s: %s", dlist_symbol, exc)
                    n_failed += 1

    # Reports go in a subdirectory, NOT output_dir itself: csv_to_pq's
    # file_pattern="*.csv" scan of Clean/ is non-recursive, but these report
    # files also end in .csv and (all but one) have a "symbol" column --
    # confirmed by testing directly that csv_to_pq was scanning them as if
    # they were ticker data, silently corrupting/nulling real rows that
    # happened to share a (symbol, date) with a report entry.
    reports_dir = output_dir / "_reports"

    if all_discrepancies:
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / "_discrepancies.csv"
        with report_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["symbol", "date", "vendor_ratio", "expected_ratio", "rel_delta"])
            writer.writeheader()
            writer.writerows(all_discrepancies)
        logger.info("Wrote %d discrepancies to %s", len(all_discrepancies), report_path)

    if all_conflicts:
        reports_dir.mkdir(parents=True, exist_ok=True)
        conflicts_path = reports_dir / "_merge_conflicts.csv"
        with conflicts_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "symbol", "date", "field", "kept_value", "kept_file",
                "rejected_value", "rejected_file", "method",
            ])
            writer.writeheader()
            writer.writerows(all_conflicts)
        logger.info("Wrote %d merge conflicts to %s", len(all_conflicts), conflicts_path)

    if all_excluded:
        reports_dir.mkdir(parents=True, exist_ok=True)
        excluded_path = reports_dir / "_disjoint_series_excluded.csv"
        with excluded_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["symbol", "files", "date_min", "date_max", "row_count"])
            writer.writeheader()
            writer.writerows(all_excluded)
        logger.info("Wrote %d excluded disjoint series to %s", len(all_excluded), excluded_path)

    if all_skipped:
        reports_dir.mkdir(parents=True, exist_ok=True)
        skipped_path = reports_dir / "_skipped_rows.csv"
        with skipped_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["symbol", "date", "fields", "reason"])
            writer.writeheader()
            writer.writerows(all_skipped)
        logger.info("Wrote %d skipped rows to %s", len(all_skipped), skipped_path)

    if all_corrections:
        reports_dir.mkdir(parents=True, exist_ok=True)
        corrections_path = reports_dir / "_split_div_corrections.csv"
        with corrections_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["symbol", "date", "field", "original_value", "corrected_value", "reason"])
            writer.writeheader()
            writer.writerows(all_corrections)
        logger.info("Wrote %d split/dividend corrections to %s", len(all_corrections), corrections_path)

    if all_invalid_dlist:
        reports_dir.mkdir(parents=True, exist_ok=True)
        invalid_dlist_path = reports_dir / "_invalid_dlist_removed.csv"
        with invalid_dlist_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["symbol", "file", "overlap_count", "overlap_date_min", "overlap_date_max"])
            writer.writeheader()
            writer.writerows(all_invalid_dlist)
        logger.info("Wrote %d invalid dlist files removed to %s", len(all_invalid_dlist), invalid_dlist_path)

    logger.info(
        "Done: %d series cleaned, %d failed, %d discrepancies flagged, %d merge conflicts flagged, "
        "%d disjoint series excluded, %d rows skipped, %d split/dividend corrections, %d invalid dlist files removed.",
        n_ok, n_failed, len(all_discrepancies), len(all_conflicts), len(all_excluded),
        len(all_skipped), len(all_corrections), len(all_invalid_dlist),
    )


def _pick_canonical_component(
    file_rows: dict[Path, list[dict]],
    paths: list[Path],
    label: str,
    kind: str,
) -> tuple[list[Path], list[list[Path]]]:
    """Partition paths into date-overlap components (_connected_components)
    and pick the most-recent one as canonical when there's more than one --
    genuine ticker/file-set reuse, not the same security (rare for live
    files; possible for multiple valid-but-mutually-disjoint dlist files).

    Returns (canonical, excluded_components).
    """
    components = _connected_components({p: file_rows[p] for p in paths})
    if len(components) == 1:
        return components[0], []

    ranked = sorted(
        components,
        key=lambda comp: max(r["date"] for p in comp for r in file_rows[p]),
        reverse=True,
    )
    canonical, excluded = ranked[0], ranked[1:]
    logger.warning(
        "%s: %d %s file(s) split into %d non-overlapping series (reuse) -- keeping %s, excluding %s",
        label, len(paths), kind, len(components),
        ", ".join(p.name for p in canonical),
        ", ".join(p.name for comp in excluded for p in comp),
    )
    return canonical, excluded


def _excluded_record(file_rows: dict[Path, list[dict]], comp: list[Path], label: str) -> dict:
    dates = [r["date"] for p in comp for r in file_rows[p]]
    return {
        "symbol": label,
        "files": ";".join(p.name for p in comp),
        "date_min": min(dates),
        "date_max": max(dates),
        "row_count": len(dates),
    }


def _clean_and_write_series(
    file_rows: dict[Path, list[dict]],
    canonical: list[Path],
    output_symbol: str,
    output_dir: Path,
    tolerance: float,
    merge_tolerance: float,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Merge, repair, and write one output series (a live ticker or a valid
    dlist series) from its canonical (confirmed-same-entity) file component.

    Returns (discrepancies, conflicts, skipped, corrections) for the caller
    to accumulate into the batch-level reports. Raises ValueError/TypeError/
    OSError on failure (missing columns, no valid rows, write failure) --
    caller catches and counts it as a failed series.
    """
    merged_rows, conflicts = merge_symbol_files(file_rows, canonical, output_symbol, float_tolerance=merge_tolerance)
    if conflicts:
        logger.warning(
            "%s: %d field-level disagreement(s) beyond %.2f%% resolved across source file(s): %s",
            output_symbol, len(conflicts), merge_tolerance * 100, ", ".join(p.name for p in canonical),
        )

    missing = [c for c in REQUIRED_COLUMNS if merged_rows and c not in merged_rows[0]]
    if missing:
        raise ValueError(f"missing required columns {missing}")

    merged_rows.sort(key=lambda r: r["date"])
    clean_rows, skipped, corrections = _repair_symbol_rows(merged_rows, output_symbol)
    for skip_record in skipped:
        logger.warning(
            "%s %s: skipping row, bad field(s) %s (%s)",
            output_symbol, skip_record["date"], skip_record["fields"], skip_record["reason"],
        )
    for c in corrections:
        logger.warning(
            "%s %s: %s=%s has no supporting price move -- corrected to %s (%s)",
            output_symbol, c["date"], c["field"], c["original_value"], c["corrected_value"], c["reason"],
        )

    if not clean_rows:
        raise ValueError("no valid rows remain after row-level validation")

    output_rows, discrepancies = clean_symbol_rows(clean_rows, tolerance=tolerance)

    output_path = output_dir / f"{output_symbol}.csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(output_rows)

    return discrepancies, conflicts, skipped, corrections


def _parse_row(row: dict) -> dict:
    return {
        "symbol": row["symbol"],
        "date": row["date"],
        "close": float(row["close"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "open": float(row["open"]),
        "volume": float(row["volume"]),
        "adjClose": float(row["adjClose"]) if row.get("adjClose") not in (None, "") else None,
        "divCash": float(row["divCash"]) if row.get("divCash") not in (None, "") else 0.0,
        "splitFactor": float(row["splitFactor"]) if row.get("splitFactor") not in (None, "") else 1.0,
    }


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=RAW_DATA_DIR,
                         help=f"Directory of raw Tiingo CSVs (default: {RAW_DATA_DIR})")
    parser.add_argument("--output", type=Path, default=CLEAN_DATA_DIR,
                         help=f"Directory to write cleaned CSVs (default: {CLEAN_DATA_DIR})")
    parser.add_argument("--pattern", default="*.csv", help="Glob pattern for input files (default: *.csv)")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                         help="Relative tolerance for accepting vendor adjClose on split/dividend days")
    parser.add_argument("--merge-tolerance", type=float, default=DEFAULT_MERGE_TOLERANCE,
                         help="Tolerance for treating same-date rows from different source files as agreeing")
    parser.add_argument("--tickers", type=Path, default=None,
                         help="Clean only the tickers in this Tickers_*.csv (column 'ticker'); default: all")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        tickers = load_ticker_filter(args.tickers) if args.tickers else None
    except (OSError, ValueError) as exc:
        logger.error("Could not read ticker filter: %s", exc)
        return 1

    clean_directory(
        args.input, args.output, pattern=args.pattern,
        tolerance=args.tolerance, merge_tolerance=args.merge_tolerance,
        tickers=tickers,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
