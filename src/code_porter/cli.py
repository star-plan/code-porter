from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from .archive import export_projects, import_packages, load_manifest
from .models import ProjectReport, ProjectType, SafetyReport
from .scanner import check_local_roots_with_progress, default_scan_options, scan_local_roots_with_progress

app = typer.Typer(help="Local code archive importer/exporter")
console = Console()


def _render_reports(reports: list[ProjectReport]) -> None:
    table = Table(title="Archive Candidates")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Git")
    table.add_column("Remote")
    table.add_column("Clean")
    table.add_column("Export")
    table.add_column("Size")
    table.add_column("Large Dirs")
    table.add_column("Ignored")
    table.add_column("Package")
    table.add_column("Reason")

    for report in reports:
        clean = "unknown" if report.is_clean is None else ("yes" if report.is_clean else "no")
        table.add_row(
            report.name,
            report.project_type.value,
            "yes" if report.is_git_repo else "no",
            "yes" if report.has_remote else "no",
            clean,
            "yes" if report.worth_exporting else "no",
            report.size_human,
            ", ".join(report.large_directories) or "-",
            ", ".join(report.ignored_directories_present) or "-",
            report.packaging_strategy.value,
            report.packaging_reason,
        )
    console.print(table)


def _render_safety_reports(reports: list[SafetyReport]) -> None:
    table = Table(title="Pre-Reformat Safety Check")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Git")
    table.add_column("Remote")
    table.add_column("Branch")
    table.add_column("Clean")
    table.add_column("Unpushed")
    table.add_column("Issues")

    status_style = {"ok": "green", "warning": "yellow", "danger": "red"}

    for report in reports:
        clean = "-" if report.is_clean is None else ("✓" if report.is_clean else "✗")
        git = "✓" if report.is_git_repo else "✗"
        remote = "✓" if report.has_remote else "✗"
        branch = report.current_branch or "-"
        unpushed = str(report.unpushed_commit_count) if report.is_git_repo and report.has_remote else "-"
        issues = "; ".join(report.issues) if report.issues else "✓ all good"
        style = status_style.get(report.status, "")

        table.add_row(
            f"[{style}]{report.name}[/{style}]",
            report.project_type.value if report.project_type != ProjectType.UNKNOWN else "-",
            f"[{style}]{git}[/{style}]" if not report.is_git_repo else git,
            f"[{style}]{remote}[/{style}]" if not report.has_remote else remote,
            branch,
            f"[{style}]{clean}[/{style}]" if report.is_clean is False else clean,
            f"[{style}]{unpushed}[/{style}]" if report.unpushed_commit_count > 0 else unpushed,
            f"[{style}]{issues}[/{style}]",
        )

    console.print(table)

    ok_count = sum(1 for r in reports if r.status == "ok")
    warning_count = sum(1 for r in reports if r.status == "warning")
    danger_count = sum(1 for r in reports if r.status == "danger")
    total = len(reports)
    console.print(
        f"Checked {total} project(s): "
        f"[green]{ok_count} ok[/green], "
        f"[yellow]{warning_count} warning[/yellow], "
        f"[red]{danger_count} danger[/red]"
    )


def _write_json(reports: list[ProjectReport], output: Path | None) -> None:
    payload = [report.to_dict() for report in reports]
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output is None:
        console.print_json(text)
        return
    output.write_text(text + "\n", encoding="utf-8")
    console.print(f"Wrote JSON report to {output}")


def _write_manifest_json(payload: dict[str, object], output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output is None:
        console.print_json(text)
        return
    output.write_text(text + "\n", encoding="utf-8")
    console.print(f"Wrote manifest JSON to {output}")


def _render_batch_summary(title: str, rows: list[tuple[str, str, str, str]]) -> None:
    table = Table(title=title)
    table.add_column("Project")
    table.add_column("Kind")
    table.add_column("Status")
    table.add_column("Detail")
    for row in rows:
        table.add_row(*row)
    console.print(table)


def _render_status_totals(label: str, statuses: list[str]) -> None:
    counts: dict[str, int] = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    summary = ", ".join(f"{status}={count}" for status, count in sorted(counts.items())) or "no items"
    console.print(f"{label}: {summary}")


@app.command("scan")
def scan(
    roots: list[Path] = typer.Argument(..., exists=True, readable=True, resolve_path=True),
    exclude: list[str] = typer.Option([], "--exclude", help="Additional directory names to exclude"),
    large_dir_threshold_mb: int = typer.Option(500, "--large-dir-threshold-mb", min=1, help="Mark top-level directories larger than this threshold"),
    depth: int | None = typer.Option(None, "--depth", min=1, help="Max directory depth to scan from each root"),
    json_output: Path | None = typer.Option(None, "--json-output", help="Write scan result to a JSON file"),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable progress bars"),
) -> None:
    """Scan local folders and classify archive packaging strategy."""
    options = default_scan_options(exclude, large_dir_threshold_mb, depth)
    
    if no_progress:
        reports = scan_local_roots_with_progress(roots, options, None)
    else:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TextColumn("{task.fields[current]}", justify="left"),
            console=console,
        ) as progress:
            task_id = progress.add_task("Scanning roots", total=len(roots), current="")

            def on_root_scanned(root: Path, completed: int, total: int) -> None:
                progress.update(task_id, completed=completed, total=total, current=str(root))

            reports = scan_local_roots_with_progress(roots, options, on_root_scanned)
    
    _render_reports(reports)
    _write_json(reports, json_output)


@app.command("check")
def check(
    roots: list[Path] = typer.Argument(..., exists=True, readable=True, resolve_path=True),
    exclude: list[str] = typer.Option([], "--exclude", help="Additional directory names to exclude"),
    depth: int | None = typer.Option(None, "--depth", min=1, help="Max directory depth to scan from each root"),
    json_output: Path | None = typer.Option(None, "--json-output", help="Write check result to a JSON file"),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable progress bars"),
) -> None:
    """Check if all projects are safe before reformatting. Flags unpushed commits, missing remotes, and dirty worktrees."""
    options = default_scan_options(exclude, depth=depth)

    if no_progress:
        reports = check_local_roots_with_progress(roots, options, None)
    else:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TextColumn("{task.fields[current]}", justify="left"),
            console=console,
        ) as progress:
            task_id = progress.add_task("Checking roots", total=len(roots), current="")

            def on_root_scanned(root: Path, completed: int, total: int) -> None:
                progress.update(task_id, completed=completed, total=total, current=str(root))

            reports = check_local_roots_with_progress(roots, options, on_root_scanned)

    _render_safety_reports(reports)
    if json_output is not None:
        payload = [report.to_dict() for report in reports]
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        json_output.write_text(text + "\n", encoding="utf-8")
        console.print(f"Wrote JSON report to {json_output}")


@app.command("export")
def export(
    roots: list[Path] = typer.Argument(..., exists=True, readable=True, resolve_path=True),
    output_dir: Path = typer.Argument(..., resolve_path=True, help="Directory for manifest and archive artifacts"),
    exclude: list[str] = typer.Option([], "--exclude", help="Additional directory names to exclude"),
    large_dir_threshold_mb: int = typer.Option(500, "--large-dir-threshold-mb", min=1, help="Mark top-level directories larger than this threshold"),
    depth: int | None = typer.Option(None, "--depth", min=1, help="Max directory depth to scan from each root"),
    manifest_output: Path | None = typer.Option(None, "--manifest-output", help="Optional extra path to write manifest JSON"),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable progress bars"),
) -> None:
    """Scan local folders and export bundle/zip archives."""
    options = default_scan_options(exclude, large_dir_threshold_mb, depth)
    
    if no_progress:
        reports = scan_local_roots_with_progress(roots, options, None)
    else:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TextColumn("{task.fields[current]}", justify="left"),
            console=console,
        ) as progress:
            scan_task = progress.add_task("Scanning roots", total=len(roots), current="")

            def on_root_scanned(root: Path, completed: int, total: int) -> None:
                progress.update(scan_task, completed=completed, total=total, current=str(root))

            reports = scan_local_roots_with_progress(roots, options, on_root_scanned)

    _render_reports(reports)

    if no_progress:
        outcome = export_projects(
            reports,
            output_dir=output_dir,
            source_roots=roots,
            on_project_processed=None,
        )
    else:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TextColumn("{task.fields[current]}", justify="left"),
            console=console,
        ) as progress:
            export_task = progress.add_task("Exporting projects", total=len(reports), current="")

            def on_project_processed(report: ProjectReport, completed: int, total: int) -> None:
                progress.update(export_task, completed=completed, total=total, current=report.name)

            outcome = export_projects(
                reports,
                output_dir=output_dir,
                source_roots=roots,
                on_project_processed=on_project_processed,
            )

    manifest = outcome.manifest
    _render_batch_summary(
        "Export Result",
        [
            (item.project_name, package_index.get(item.project_name, "-"), item.status, item.detail)
            for item, package_index in [
                (
                    result,
                    {
                        package.name: package.package_kind.value
                        for package in manifest.packages
                    },
                )
                for result in outcome.results
            ]
        ],
    )
    _render_status_totals("Export summary", [item.status for item in outcome.results])
    console.print(f"Wrote {len(manifest.packages)} package(s) to {output_dir}")
    if manifest_output is not None:
        _write_manifest_json(manifest.to_dict(), manifest_output)


@app.command("import")
def import_archives(
    manifest_path: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True, help="manifest.json produced by export"),
    destination_root: Path = typer.Argument(..., resolve_path=True, help="Directory to restore projects into"),
    on_existing: str = typer.Option("skip", "--on-existing", help="How to handle existing directories: skip or replace"),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable progress bars"),
) -> None:
    """Import bundle/zip archives from a manifest."""
    manifest = load_manifest(manifest_path)
    
    if no_progress:
        results = import_packages(
            manifest_path,
            destination_root=destination_root,
            on_existing=on_existing,
            on_package_processed=None,
        )
    else:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TextColumn("{task.fields[current]}", justify="left"),
            console=console,
        ) as progress:
            import_task = progress.add_task("Importing packages", total=len(manifest.packages), current="")

            def on_package_processed(package, completed: int, total: int) -> None:
                progress.update(import_task, completed=completed, total=total, current=package.name)

            results = import_packages(
                manifest_path,
                destination_root=destination_root,
                on_existing=on_existing,
                on_package_processed=on_package_processed,
            )

    package_index = {item.name: item for item in manifest.packages}
    _render_batch_summary(
        "Import Result",
        [
            (item.project_name, package_index[item.project_name].package_kind.value, item.status, item.detail)
            for item in results
        ],
    )
    _render_status_totals("Import summary", [item.status for item in results])


def main() -> None:
    app()