"""Tests for the ETF export in src/tiingo_ticker_manager.py."""

import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import tiingo_ticker_manager as tm

ROWS = [
    {
        "ticker": "SPY",
        "exchange": "NYSE ARCA",
        "assetType": "ETF",
        "startDate": "1993-01-29",
    },
    {
        "ticker": "AAPL",
        "exchange": "NASDAQ",
        "assetType": "Stock",
        "startDate": "1980-12-12",
    },
    {
        "ticker": "QQQ",
        "exchange": "NASDAQ",
        "assetType": "ETF",
        "startDate": "1999-03-10",
    },
]


def test_export_etf_list_writes_only_etfs(tmp_path):
    out = tmp_path / "sub" / "ETFs.xlsx"

    count = tm.export_etf_list(ROWS, out)

    sheet = openpyxl.load_workbook(out).active
    values = [[c.value for c in row] for row in sheet.iter_rows()]
    assert count == 2
    assert values[0] == ["ticker", "exchange", "assetType", "startDate"]
    assert [row[0] for row in values[1:]] == ["SPY", "QQQ"]


def test_export_etf_list_refuses_empty(tmp_path):
    out = tmp_path / "ETFs.xlsx"
    with pytest.raises(ValueError, match="No ETF rows"):
        tm.export_etf_list([ROWS[1]], out)
    assert not out.exists()


def test_export_etf_list_locked_file_keeps_old_file(tmp_path, monkeypatch):
    out = tmp_path / "ETFs.xlsx"
    tm.export_etf_list(ROWS, out)
    before = out.read_bytes()

    def locked(src, dst):
        raise PermissionError("held by Excel")

    monkeypatch.setattr(tm.os, "replace", locked)
    with pytest.raises(PermissionError):
        tm.export_etf_list(ROWS[:1], out)
    assert out.read_bytes() == before
