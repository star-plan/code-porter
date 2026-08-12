"""CLI output tests for the scan command."""

from __future__ import annotations

import json
import subprocess
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


def init_git_repo(path: Path) -> None:
    """Initialize a local git repository on branch main with a test identity."""
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
        text=True,
    )


def make_sample_project(root: Path, name: str = "demo-app") -> Path:
    """Create a small non-git Python project for CLI scan tests."""
    project_dir = root / name
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text(f"[project]\nname='{name}'\n", encoding="utf-8")
    (project_dir / "main.py").write_text("print('hi')\n", encoding="utf-8")
    return project_dir


def make_git_project(root: Path, name: str, *, dirty: bool) -> Path:
    """Create a git Python project that is either clean or dirty."""
    project_dir = make_sample_project(root, name)
    init_git_repo(project_dir)
    subprocess.run(
        ["git", "-C", str(project_dir), "add", "pyproject.toml", "main.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(project_dir), "commit", "-m", "init"],
        check=True,
        capture_output=True,
        text=True,
    )
    if dirty:
        (project_dir / "main.py").write_text("print('dirty')\n", encoding="utf-8")
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


def test_scan_status_dirty_filters_projects(tmp_path: Path) -> None:
    """--status dirty should keep only unclean git worktrees."""
    make_git_project(tmp_path, "clean-app", dirty=False)
    make_git_project(tmp_path, "dirty-app", dirty=True)
    make_sample_project(tmp_path, "plain-app")

    result = runner.invoke(app, ["scan", str(tmp_path), "--status", "dirty", "--no-progress"])

    assert result.exit_code == 0, result.output
    assert "dirty-app" in result.output
    assert "clean-app" not in result.output
    assert "plain-app" not in result.output
    assert "status=dirty" in result.output
    assert "showing 1" in result.output
    assert "Found 3 project(s)" in result.output


def test_scan_status_not_git_and_json(tmp_path: Path) -> None:
    """--status not-git with --json should only return non-git projects."""
    make_git_project(tmp_path, "git-app", dirty=False)
    make_sample_project(tmp_path, "plain-app")

    result = runner.invoke(
        app,
        ["scan", str(tmp_path), "--status", "not-git", "--json", "--no-progress"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["name"] == "plain-app"


def test_scan_status_or_combines_filters(tmp_path: Path) -> None:
    """Multiple --status values should be combined with OR."""
    make_git_project(tmp_path, "dirty-app", dirty=True)
    make_sample_project(tmp_path, "plain-app")
    make_git_project(tmp_path, "clean-app", dirty=False)

    result = runner.invoke(
        app,
        ["scan", str(tmp_path), "--status", "dirty", "--status", "not-git", "--no-progress"],
    )

    assert result.exit_code == 0, result.output
    assert "dirty-app" in result.output
    assert "plain-app" in result.output
    assert "clean-app" not in result.output


def test_scan_status_unknown_exits_with_error(tmp_path: Path) -> None:
    """Unknown --status values should fail fast with a clear error."""
    make_sample_project(tmp_path)

    result = runner.invoke(app, ["scan", str(tmp_path), "--status", "weird", "--no-progress"])

    assert result.exit_code == 2
    assert "Unknown status filter" in result.output
    assert "dirty" in result.output
