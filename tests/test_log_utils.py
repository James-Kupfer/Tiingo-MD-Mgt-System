"""Tests for src/log_utils.py, the logging added to src/clean_prices.py, and src/run_csv2pq.py."""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import clean_prices  # noqa: E402
import log_utils  # noqa: E402
import run_csv2pq  # noqa: E402

_popen_real = subprocess.Popen  # captured before tests patch subprocess.Popen
HEADER = "symbol,date,close,high,low,open,volume,adjClose,adjHigh,adjLow,adjOpen,adjVolume,divCash,splitFactor"


@pytest.fixture(autouse=True)
def restore_root_logger():
    """setup_logging replaces root handlers; put the originals back after each test."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level)


def _read_log(log_path: Path) -> str:
    for h in logging.getLogger().handlers:
        h.flush()
    return log_path.read_text(encoding="utf-8")


def _age(path: Path, days: int) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_setup_logging_writes_timestamped_file(tmp_path: Path) -> None:
    log_path = log_utils.setup_logging("demo", log_dir=tmp_path)
    logging.getLogger("x").debug("detail line")
    assert log_path.parent == tmp_path
    assert log_path.name.endswith("_demo.txt")
    assert "detail line" in _read_log(log_path)  # file handler captures DEBUG


def test_purge_old_logs_removes_only_expired_logs(tmp_path: Path) -> None:
    old_log, new_log, old_other = (
        tmp_path / "a.txt",
        tmp_path / "b.txt",
        tmp_path / "keep.xlsx",
    )
    for p in (old_log, new_log, old_other):
        p.write_text("x")
    _age(old_log, 91)
    _age(old_other, 91)
    assert log_utils.purge_old_logs(tmp_path, days=90) == 1
    assert not old_log.exists() and new_log.exists() and old_other.exists()


def test_purge_old_logs_missing_dir_is_noop(tmp_path: Path) -> None:
    assert log_utils.purge_old_logs(tmp_path / "nope") == 0


def test_failed_series_logs_traceback_and_continues(tmp_path: Path) -> None:
    raw, out = tmp_path / "raw", tmp_path / "out"
    raw.mkdir()
    (raw / "BAD.csv").write_text(
        "symbol,date\nBAD,2024-01-02\n"
    )  # missing required columns
    (raw / "OK.csv").write_text(
        f"{HEADER}\nOK,2024-01-02,10,11,9,10,100,10,11,9,10,100,0,1\n"
        f"OK,2024-01-03,10,11,9,10,100,10,11,9,10,100,0,1\n"
    )
    log_path = log_utils.setup_logging("t", log_dir=tmp_path / "logs")
    clean_prices.clean_directory(raw, out)
    text = _read_log(log_path)
    assert "Failed to clean BAD" in text and "BAD.csv" in text
    assert "Traceback" in text and "missing required columns" in text
    assert (out / "OK.csv").exists()
    assert "1 failed" in text


def test_main_logs_unexpected_exception_and_returns_1(
    tmp_path: Path, monkeypatch
) -> None:
    def boom(*args, **kwargs):
        raise KeyError("adjClose")

    monkeypatch.setattr(clean_prices, "clean_directory", boom)
    monkeypatch.setattr(
        clean_prices,
        "setup_logging",
        lambda name, console_level="INFO": log_utils.setup_logging(
            name, console_level, tmp_path
        ),
    )
    assert clean_prices.main(["--input", str(tmp_path), "--output", str(tmp_path)]) == 1
    text = _read_log(next(tmp_path.glob("*_clean_prices.txt")))
    assert "clean_prices aborted" in text and "KeyError" in text


def test_run_csv2pq_captures_output_and_exit_code(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "c.toml"
    config.write_text("")
    script = tmp_path / "fake_csv2pq.py"
    script.write_text(
        "import sys\nprint('converting')\nprint('boom', file=sys.stderr)\nsys.exit(3)\n"
    )
    monkeypatch.setattr(run_csv2pq.shutil, "which", lambda _: sys.executable)
    monkeypatch.setattr(
        run_csv2pq.subprocess,
        "Popen",
        lambda cmd, **kw: _popen_real([sys.executable, str(script)], **kw),
    )
    log_path = log_utils.setup_logging("t", log_dir=tmp_path / "logs")
    assert run_csv2pq.run_csv2pq(config) == 3
    text = _read_log(log_path)
    assert (
        "csv2pq: converting" in text
        and "csv2pq: boom" in text
        and "exited with code 3" in text
    )


def test_run_csv2pq_missing_executable_or_config(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    monkeypatch.setattr(run_csv2pq.shutil, "which", lambda _: None)
    assert run_csv2pq.run_csv2pq(tmp_path / "c.toml") == 1
    monkeypatch.setattr(run_csv2pq.shutil, "which", lambda _: "csv2pq")
    assert run_csv2pq.run_csv2pq(tmp_path / "missing.toml") == 1
    assert "config not found" in caplog.text
