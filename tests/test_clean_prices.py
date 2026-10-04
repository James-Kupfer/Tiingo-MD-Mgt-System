"""Tests for src/clean_prices.py's ticker-subset run (--tickers)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import clean_prices  # noqa: E402

COLS = "symbol,date,close,high,low,open,volume,adjClose,adjHigh,adjLow,adjOpen,adjVolume,divCash,splitFactor"


def write_raw(raw: Path, name: str, symbol: str, dates: list[str]) -> None:
    rows = [f"{symbol},{d},10,10,10,10,100,10,10,10,10,100,0,1" for d in dates]
    (raw / f"{name}.csv").write_text("\n".join([COLS, *rows]) + "\n", encoding="utf-8")


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    d = tmp_path / "raw"
    d.mkdir()
    write_raw(d, "AAA", "AAA", ["2026-10-01", "2026-10-02"])
    write_raw(d, "BBB", "BBB", ["2026-10-01", "2026-10-02"])
    write_raw(d, "BBB_dlist", "BBB", ["2020-01-02"])
    return d


def test_subset_cleans_only_listed_tickers_with_their_dlist(
    raw: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    clean_prices.clean_directory(raw, out, tickers={"BBB", "ZZZ"})
    assert sorted(p.name for p in out.glob("*.csv")) == ["BBB.csv", "BBB_DLIST.csv"]


def test_no_filter_cleans_everything(raw: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    clean_prices.clean_directory(raw, out)
    assert sorted(p.name for p in out.glob("*.csv")) == [
        "AAA.csv",
        "BBB.csv",
        "BBB_DLIST.csv",
    ]


def test_load_ticker_filter_rejects_file_without_ticker_column(tmp_path: Path) -> None:
    f = tmp_path / "t.csv"
    f.write_text("symbol\nAAA\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ticker"):
        clean_prices.load_ticker_filter(f)


def test_main_returns_1_on_missing_ticker_file(raw: Path, tmp_path: Path) -> None:
    rc = clean_prices.main(
        [
            "--input",
            str(raw),
            "--output",
            str(tmp_path / "o"),
            "--tickers",
            str(tmp_path / "nope.csv"),
        ]
    )
    assert rc == 1
