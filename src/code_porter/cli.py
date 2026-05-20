from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .models import ProjectReport
from .planner import build_plans, load_reports
from .scanner import default_scan_options, scan_local_roots, scan_remote_host

app = typer.Typer(help="Git-first workspace migration planner")
console = Console()


def _render_reports(reports: list[ProjectReport]) -> None:
    table = Table(title="Migration Candidates")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Git")
    table.add_column("Remote")
    table.add_column("Clean")
    table.add_column("Size")
    table.add_column("Strategy")
    table.add_column("Reason")

    for report in reports:
        clean = "unknown" if report.is_clean is None else ("yes" if report.is_clean else "no")
        table.add_row(
            report.name,
            report.project_type.value,
            "yes" if report.is_git_repo else "no",
            "yes" if report.has_remote else "no",
            clean,
            report.size_human,
            report.migration_strategy.value,
            report.migration_reason,
        )
    console.print(table)


def _write_json(reports: list[ProjectReport], output: Path | None) -> None:
    payload = [report.to_dict() for report in reports]
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output is None:
        console.print_json(text)
        return
    output.write_text(text + "\n", encoding="utf-8")
    console.print(f"Wrote JSON report to {output}")


def _write_plan_json(payload: list[dict[str, str]], output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output is None:
        console.print_json(text)
        return
    output.write_text(text + "\n", encoding="utf-8")
    console.print(f"Wrote plan JSON to {output}")


@app.command("scan-local")
def scan_local(
    roots: list[Path] = typer.Argument(..., exists=True, readable=True, resolve_path=True),
    exclude: list[str] = typer.Option([], "--exclude", help="Additional directory names to exclude"),
    json_output: Path | None = typer.Option(None, "--json-output", help="Write scan result to a JSON file"),
) -> None:
    """Scan local folders and classify migration strategy."""
    reports = scan_local_roots(roots, default_scan_options(exclude))
    _render_reports(reports)
    _write_json(reports, json_output)


@app.command("scan-remote")
def scan_remote(
    host: str = typer.Argument(..., help="SSH host alias or user@host"),
    roots: list[str] = typer.Argument(..., help="Remote Windows roots such as D:/Projects"),
    exclude: list[str] = typer.Option([], "--exclude", help="Additional directory names to exclude"),
    json_output: Path | None = typer.Option(None, "--json-output", help="Write scan result to a JSON file"),
) -> None:
    """Scan a remote Windows machine over SSH and classify migration strategy."""
    reports = scan_remote_host(host, roots, default_scan_options(exclude))
    _render_reports(reports)
    _write_json(reports, json_output)


@app.command("plan")
def plan(
    report_file: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True, help="JSON report from scan-local or scan-remote"),
    destination_root: Path = typer.Argument(..., resolve_path=True, help="Local destination root on this Mac"),
    source_host: str | None = typer.Option(None, "--source-host", help="SSH host alias when report paths come from a remote machine"),
    json_output: Path | None = typer.Option(None, "--json-output", help="Write plan result to a JSON file"),
) -> None:
    """Generate migration commands from a scan report."""
    reports = load_reports(report_file)
    plans = build_plans(reports, destination_root=destination_root, source_host=source_host)

    table = Table(title="Migration Plan")
    table.add_column("Project")
    table.add_column("Strategy")
    table.add_column("Destination")
    table.add_column("Command")
    for item in plans:
        table.add_row(item.project_name, item.strategy.value, item.destination_path, item.command)
    console.print(table)
    _write_plan_json([item.to_dict() for item in plans], json_output)


def main() -> None:
    app()