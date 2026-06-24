"""Paper CLI tests (Stage 6A Task 7)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tradingagents.paper.cli import app
from tradingagents.paper.config import PaperPaths
from tradingagents.paper.repository import PaperRepository
from tests.paper.conftest import seed_demo_account

runner = CliRunner()


def database_hash(home_dir: Path) -> str:
    db_path = PaperPaths(home_dir=home_dir).paper_db_path
    return hashlib.sha256(db_path.read_bytes()).hexdigest()


@pytest.fixture
def seeded_home(tmp_path) -> Path:
    repo = PaperRepository(PaperPaths(home_dir=tmp_path))
    seed_demo_account(repo, account_id="demo")
    repo.close()
    return tmp_path


def test_status_is_read_only(seeded_home: Path) -> None:
    before = database_hash(seeded_home)
    result = runner.invoke(
        app,
        ["status", "--account-id", "demo", "--home-dir", str(seeded_home)],
    )
    assert result.exit_code == 0
    assert database_hash(seeded_home) == before
    assert "demo" in result.stdout


def test_init_creates_account(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "init",
            "--account-id",
            "alpha",
            "--initial-cash",
            "500000",
            "--home-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    repo = PaperRepository(PaperPaths(home_dir=tmp_path))
    snapshot = repo.load_account_snapshot("alpha")
    repo.close()
    assert snapshot.account.account_id == "alpha"


def test_report_command_is_read_only(seeded_home: Path) -> None:
    before = database_hash(seeded_home)
    result = runner.invoke(
        app,
        [
            "report",
            "--account-id",
            "demo",
            "--trade-date",
            "2026-06-22",
            "--logical-run-key",
            "demo:2026-06-22:uni-1",
            "--revision",
            "1",
            "--home-dir",
            str(seeded_home),
        ],
    )
    assert result.exit_code == 0
    assert database_hash(seeded_home) == before
