"""CLI output tests for the scan command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

import code_porter.cli as cli_module
from code_porter.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def plain_wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a wide, plain console so table headers are not truncated in tests."""
    monkeypatch.setattr(
        cli_module,
        "console",
        Console(force_terminal=False, no_color=True, width=200, highlight=False),
    )


def make_sample_project(root: Path) -> Path:
    """Create a small non-git Python project for CLI scan tests."""
    project_dir = root / "demo-app"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text("[project]\nname='demo-app'\n", encoding="utf-8")
    (project_dir / "main.py").write_text("print('hi')\n", encoding="utf-8")
    return project_dir


def test_scan_default_prints_table_and_summary_without_json(tmp_path: Path) -> None:
    """Default scan should show a compact table + summary, not dump full JSON."""
    make_sample_project(tmp_path)

    result = runner.invoke(app, ["scan", str(tmp_path), "--no-progress"])

    assert result.exit_code == 0, result.output
    assert "Archive Candidates" in result.output
    assert "demo-app" in result.output
    assert "Found 1 project(s)" in result.output
    assert "worth exporting" in result.output
    assert "Note" in result.output
    # Compact mode should not show the full verbose columns.
    assert "Large Dirs" not in result.output
    # Full JSON dump should not appear by default.
    assert '"packaging_strategy"' not in result.output
    assert '"size_bytes"' not in result.output


def test_scan_json_flag_prints_only_json(tmp_path: Path) -> None:
    """--json should emit machine-readable JSON without the human table."""
    make_sample_project(tmp_path)

    result = runner.invoke(app, ["scan", str(tmp_path), "--json", "--no-progress"])

    assert result.exit_code == 0, result.output
    assert "Archive Candidates" not in result.output
    assert "Found 1 project(s)" not in result.output
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["name"] == "demo-app"
    assert payload[0]["packaging_strategy"] == "zip_archive"


def test_scan_json_output_writes_file_and_keeps_table(tmp_path: Path) -> None:
    """--json-output should write a file while still rendering the table."""
    make_sample_project(tmp_path)
    output = tmp_path / "reports" / "scan.json"

    result = runner.invoke(
        app,
        ["scan", str(tmp_path), "--json-output", str(output), "--no-progress"],
    )

    assert result.exit_code == 0, result.output
    assert "Archive Candidates" in result.output
    assert "Found 1 project(s)" in result.output
    assert "Wrote JSON report to" in result.output
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload[0]["name"] == "demo-app"


def test_scan_verbose_includes_extra_columns(tmp_path: Path) -> None:
    """--verbose should expose detailed columns such as Reason / Large Dirs."""
    make_sample_project(tmp_path)

    result = runner.invoke(app, ["scan", str(tmp_path), "--verbose", "--no-progress"])

    assert result.exit_code == 0, result.output
    assert "Reason" in result.output
    assert "Large Dirs" in result.output
    assert "Ignored" in result.output
    assert "Remote" in result.output
