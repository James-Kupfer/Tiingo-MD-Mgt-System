"""Tests for src/download.py: incremental append and the refetch/replace path."""

import sys
from datetime import datetime
from pathlib import Path
from threading import Lock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import download  # noqa: E402

HEADER = "date,close,volume"


def parse(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d")  # noqa: DTZ007 - date only
    except ValueError:
        return None


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    (tmp_path / "AAA.csv").write_text(
        "symbol,date,close,volume\n"
        "AAA,2026-10-01,10.0,100\n"
        "AAA,2026-10-02,11.0,50\n",  # preliminary bar
        encoding="utf-8",
    )
    return tmp_path


def lines(raw_dir: Path) -> list[str]:
    return (raw_dir / "AAA.csv").read_text(encoding="utf-8").splitlines()


def test_replace_mode_revises_preliminary_row_and_appends(raw_dir: Path) -> None:
    data = f"{HEADER}\n2026-10-01,10.0,100\n2026-10-02,11.5,900\n2026-10-05,12.0,700\n"
    assert download.append_ticker_data(
        "AAA", data, raw_dir, ",", parse, replace_existing=True
    )
    assert lines(raw_dir) == [
        "symbol,date,close,volume",
        "AAA,2026-10-01,10.0,100",
        "AAA,2026-10-02,11.5,900",
        "AAA,2026-10-05,12.0,700",
    ]


def test_default_mode_never_touches_stored_rows(raw_dir: Path) -> None:
    data = f"{HEADER}\n2026-10-02,11.5,900\n2026-10-05,12.0,700\n"
    assert download.append_ticker_data("AAA", data, raw_dir, ",", parse)
    assert lines(raw_dir)[2:] == ["AAA,2026-10-02,11.0,50", "AAA,2026-10-05,12.0,700"]


def test_replace_mode_with_unchanged_rows_does_not_rewrite(raw_dir: Path) -> None:
    before = (raw_dir / "AAA.csv").stat().st_mtime_ns
    data = f"{HEADER}\n2026-10-01,10.0,100\n2026-10-02,11.0,50\n"
    assert download.append_ticker_data(
        "AAA", data, raw_dir, ",", parse, replace_existing=True
    )
    assert (raw_dir / "AAA.csv").stat().st_mtime_ns == before
    assert len(lines(raw_dir)) == 3


def _config(raw_dir: Path, refetch_days: int) -> dict:
    return {
        "mode": "incremental",
        "api_key": "k",
        "base_url": "u",
        "raw_data_dir": raw_dir,
        "csv_delimiter": ",",
        "timeout": 1,
        "max_retries": 1,
        "retry_delay": 0,
        "today": "2026-10-02",
        "cutoff_date": None,
        "ticker_exists_func": lambda t: (raw_dir / f"{t}.csv").exists(),
        "get_latest_date_func": lambda t: "2026-10-02",
        "parse_date_func": parse,
        "refetch_days": refetch_days,
    }


class _NoLimit:
    def acquire(self) -> None:
        pass


@pytest.mark.parametrize(
    ("refetch_days", "expected_start"), [(0, None), (5, "2026-09-27")]
)
def test_up_to_date_ticker_is_refetched_only_when_asked(
    raw_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    refetch_days: int,
    expected_start: str | None,
) -> None:
    calls = []
    monkeypatch.setattr(
        download, "download_ticker_data", lambda *a, **k: calls.append(a[6]) or None
    )
    stats = {"successful": 0, "failed": 0, "skipped": 0, "request_count": 0}
    download.process_single_ticker(
        1, {"ticker": "AAA"}, _config(raw_dir, refetch_days), _NoLimit(), Lock(), stats
    )
    assert calls == ([expected_start] if expected_start else [])


def test_replace_mode_header_mismatch_leaves_file_untouched(raw_dir: Path) -> None:
    before = (raw_dir / "AAA.csv").read_bytes()
    data = "date,close,volume,extra\n2026-10-02,11.5,900,1\n"
    assert not download.append_ticker_data(
        "AAA", data, raw_dir, ",", parse, replace_existing=True
    )
    assert (raw_dir / "AAA.csv").read_bytes() == before


def test_replace_mode_writes_lf_line_endings(raw_dir: Path) -> None:
    data = f"{HEADER}\n2026-10-02,11.5,900\n"
    download.append_ticker_data("AAA", data, raw_dir, ",", parse, replace_existing=True)
    assert b"\r\n" not in (raw_dir / "AAA.csv").read_bytes()


FULL_HEADER = "symbol,date,close,adjClose,divCash,splitFactor"


def _write_full(path: Path, rows: list[str]) -> None:
    path.write_text("\n".join([FULL_HEADER, *rows]) + "\n", encoding="utf-8")


def test_stale_adjclose_detected_when_dividend_not_restated(tmp_path: Path) -> None:
    f = tmp_path / "AAA.csv"
    # $5 dividend on 10-02: adjClose return == raw return (100 -> 100) means
    # the older row was never restated.
    _write_full(f, ["AAA,2026-10-01,100,100,0,1", "AAA,2026-10-02,100,100,5,1"])
    assert download.stale_adjclose_dates(f) == ["2026-10-02"]


def test_restated_history_and_non_ex_dates_are_not_stale(tmp_path: Path) -> None:
    f = tmp_path / "AAA.csv"
    _write_full(
        f,
        [
            "AAA,2026-10-01,100,95.0,0,1",  # restated: 100 * (1 - 5/100)
            "AAA,2026-10-02,100,100,5,1",
            "AAA,2026-10-05,101,101,0,1",
        ],
    )
    assert download.stale_adjclose_dates(f) == []


def test_stale_split_detected(tmp_path: Path) -> None:
    f = tmp_path / "AAA.csv"
    _write_full(f, ["AAA,2026-10-01,100,100,0,1", "AAA,2026-10-02,50,50,0,2"])
    assert download.stale_adjclose_dates(f) == ["2026-10-02"]


def test_incremental_update_with_stale_adjclose_redownloads_full_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path
    _write_full(raw / "AAA.csv", ["AAA,2026-10-01,100,100,0,1", "AAA,2026-10-02,100,100,5,1"])
    calls = []

    def fake_download(ticker, key, url, timeout, retries, delay, start, end):
        calls.append(start)
        if not start:  # the full history, restated by the vendor
            return "date,close,adjClose,divCash,splitFactor\n2026-10-01,100,95.0,0,1\n2026-10-02,100,100,5,1\n"
        return "date,close,adjClose,divCash,splitFactor\n2026-10-02,100,100,5,1\n"

    monkeypatch.setattr(download, "download_ticker_data", fake_download)
    cfg = _config(raw, refetch_days=5)
    stats = {"successful": 0, "failed": 0, "skipped": 0, "request_count": 0}
    download.process_single_ticker(1, {"ticker": "AAA"}, cfg, _NoLimit(), Lock(), stats)
    assert calls == ["2026-09-27", ""]
    assert download.stale_adjclose_dates(raw / "AAA.csv") == []
    assert "95.0" in (raw / "AAA.csv").read_text(encoding="utf-8")
    assert stats["request_count"] == 2


def test_consistent_file_is_not_redownloaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_full(tmp_path / "AAA.csv", ["AAA,2026-10-01,100,95.0,0,1", "AAA,2026-10-02,100,100,5,1"])
    calls = []
    monkeypatch.setattr(
        download, "download_ticker_data",
        lambda *a, **k: calls.append(a[6]) or "date,close,adjClose,divCash,splitFactor\n2026-10-02,100,100,5,1\n",
    )
    stats = {"successful": 0, "failed": 0, "skipped": 0, "request_count": 0}
    download.process_single_ticker(1, {"ticker": "AAA"}, _config(tmp_path, 5), _NoLimit(), Lock(), stats)
    assert calls == ["2026-09-27"]
