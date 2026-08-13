from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TypeVar

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

# Preserve the concrete questionary Question type through ESC binding helpers.
_QuestionT = TypeVar("_QuestionT")

from .archive import export_projects, import_packages, load_manifest
from .cleaner import (
    CLEAN_PROFILES,
    PROFILE_CHOICES,
    PROFILE_ORDER,
    CleanPlan,
    CleanTarget,
    apply_clean_targets,
    discover_clean_targets,
    format_size,
    normalize_profiles,
    summarize_targets,
)
from .models import PackagingStrategy, ProjectReport, ProjectType, SafetyReport
from .scanner import check_local_roots_with_progress, default_scan_options, scan_local_roots_with_progress

app = typer.Typer(help="Local code archive importer/exporter")
console = Console()

# Named filters for `scan --status`. Multiple values are combined with OR.
SCAN_STATUS_CHOICES = (
    "dirty",
    "clean",
    "git",
    "not-git",
    "remote",
    "no-remote",
    "exportable",
    "skip",
    "bundle",
    "overlay",
    "zip",
)

# Sort keys for scan/clean result lists. See docs/research/scan-clean-result-sort.md.
SCAN_SORT_CHOICES = (
    "path",
    "name",
    "size",
    "type",
    "package",
    "export",
)
CLEAN_SORT_CHOICES = (
    "profile",
    "size",
    "project",
    "name",
    "path",
)


def _matches_scan_status(report: ProjectReport, status: str) -> bool:
    """Return True if a project report matches one named scan status filter."""
    match status:
        case "dirty":
            return report.is_git_repo and report.is_clean is False
        case "clean":
            return report.is_git_repo and report.is_clean is True
        case "git":
            return report.is_git_repo
        case "not-git":
            return not report.is_git_repo
        case "remote":
            return report.is_git_repo and report.has_remote
        case "no-remote":
            return report.is_git_repo and not report.has_remote
        case "exportable":
            return report.worth_exporting
        case "skip":
            return (not report.worth_exporting) or report.packaging_strategy == PackagingStrategy.SKIP
        case "bundle":
            return report.packaging_strategy == PackagingStrategy.BUNDLE
        case "overlay":
            return report.packaging_strategy == PackagingStrategy.BUNDLE_WITH_OVERLAY
        case "zip":
            return report.packaging_strategy == PackagingStrategy.ZIP
        case _:
            return False


def _filter_reports_by_status(
    reports: list[ProjectReport],
    statuses: list[str],
) -> list[ProjectReport]:
    """Filter reports by one or more status names (OR semantics)."""
    if not statuses:
        return reports
    wanted = {item.strip().lower() for item in statuses if item and item.strip()}
    if not wanted:
        return reports
    return [report for report in reports if any(_matches_scan_status(report, status) for status in wanted)]


def _normalize_sort_key(raw: str | None, allowed: tuple[str, ...]) -> str | None:
    """Normalize a --sort value; return None when unset, raise ValueError if unknown."""
    if raw is None:
        return None
    key = raw.strip().lower()
    if not key:
        return None
    if key not in allowed:
        allowed_text = ", ".join(allowed)
        raise ValueError(f"Unknown sort key: {raw!r}. Allowed: {allowed_text}")
    return key


def sort_project_reports(
    reports: list[ProjectReport],
    key: str | None,
    *,
    reverse: bool = False,
) -> list[ProjectReport]:
    """Sort scan reports by a named key; None keeps input order.

    Natural direction: ``size`` largest first, ``export`` exportable first; others ascending.
    ``reverse=True`` flips the full natural order.
    """
    if not key:
        return list(reports)

    def natural_key(report: ProjectReport) -> tuple:
        """Encode natural (field-default) order with stable secondary fields."""
        name = report.name.lower()
        path = report.path.lower()
        match key:
            case "path":
                return (path,)
            case "name":
                return (name, path)
            case "size":
                return (-report.size_bytes, name, path)
            case "type":
                return (report.project_type.value, name, path)
            case "package":
                return (report.packaging_strategy.value, name, path)
            case "export":
                # 0 = exportable first in natural order.
                return (0 if report.worth_exporting else 1, name, path)
            case _:
                return (path,)

    ordered = sorted(reports, key=natural_key)
    if reverse:
        ordered.reverse()
    return ordered


def sort_clean_targets(
    targets: list[CleanTarget],
    key: str | None,
    *,
    reverse: bool = False,
) -> list[CleanTarget]:
    """Sort clean targets by a named key; None keeps input order.

    Natural direction: ``size`` largest first; ``profile`` follows PROFILE_ORDER
    with largest size within each profile; others ascending by label then size.
    """
    if not key:
        return list(targets)
    profile_rank = {name: index for index, name in enumerate(PROFILE_ORDER)}

    def natural_key(item: CleanTarget) -> tuple:
        """Encode natural (field-default) order with stable secondary fields."""
        path = item.path.lower()
        project = item.project_name.lower()
        name = item.name.lower()
        match key:
            case "profile":
                return (profile_rank.get(item.profile, 99), -item.size_bytes, path)
            case "size":
                return (-item.size_bytes, path)
            case "project":
                return (project, -item.size_bytes, path)
            case "name":
                return (name, -item.size_bytes, path)
            case "path":
                return (path,)
            case _:
                return (path,)

    ordered = sorted(targets, key=natural_key)
    if reverse:
        ordered.reverse()
    return ordered


def _report_row_style(report: ProjectReport) -> str:
    """Pick a Rich style for a scan row based on export risk."""
    if not report.worth_exporting or report.packaging_strategy.value == "skip":
        return "yellow"
    if report.is_git_repo and report.is_clean is False:
        return "yellow"
    if report.is_git_repo and not report.has_remote:
        return "yellow"
    return ""


def _format_git_cell(report: ProjectReport) -> str:
    """Render a compact Git status cell (repo / remote / clean)."""
    if not report.is_git_repo:
        return "no"
    parts = ["yes"]
    parts.append("remote" if report.has_remote else "no-remote")
    if report.is_clean is None:
        parts.append("clean?")
    elif report.is_clean:
        parts.append("clean")
    else:
        parts.append("dirty")
    return " / ".join(parts)


def _truncate(text: str, max_len: int = 48) -> str:
    """Truncate long reason text so the default table stays readable."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _render_reports(reports: list[ProjectReport], *, verbose: bool = False) -> None:
    """Render project scan results as a table.

    Default mode keeps columns compact for terminal scanning.
    Verbose mode adds remote/clean details, large/ignored dirs, and full reasons.
    """
    table = Table(title="Archive Candidates")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Git")
    if verbose:
        table.add_column("Remote")
        table.add_column("Clean")
    table.add_column("Size")
    table.add_column("Package")
    table.add_column("Export")
    if verbose:
        table.add_column("Large Dirs")
        table.add_column("Ignored")
        table.add_column("Reason")
    else:
        table.add_column("Note")

    for report in reports:
        style = _report_row_style(report)
        name = f"[{style}]{report.name}[/{style}]" if style else report.name
        export_cell = "yes" if report.worth_exporting else "no"
        if style and not report.worth_exporting:
            export_cell = f"[{style}]{export_cell}[/{style}]"

        if verbose:
            clean = "unknown" if report.is_clean is None else ("yes" if report.is_clean else "no")
            table.add_row(
                name,
                report.project_type.value,
                "yes" if report.is_git_repo else "no",
                "yes" if report.has_remote else "no",
                clean,
                report.size_human,
                report.packaging_strategy.value,
                export_cell,
                ", ".join(report.large_directories) or "-",
                ", ".join(report.ignored_directories_present) or "-",
                report.packaging_reason or report.worth_reason or "-",
            )
        else:
            note = report.worth_reason if not report.worth_exporting else report.packaging_reason
            table.add_row(
                name,
                report.project_type.value,
                _format_git_cell(report),
                report.size_human,
                report.packaging_strategy.value,
                export_cell,
                _truncate(note) or "-",
            )
    console.print(table)


def _render_scan_summary(
    reports: list[ProjectReport],
    *,
    scanned_total: int | None = None,
    status_filters: list[str] | None = None,
) -> None:
    """Print a one-line summary of scan results for quick overview."""
    total = len(reports)
    strategy_counts: dict[str, int] = {}
    for report in reports:
        key = report.packaging_strategy.value
        strategy_counts[key] = strategy_counts.get(key, 0) + 1
    worth = sum(1 for report in reports if report.worth_exporting)
    skip = total - worth
    strategy_part = ", ".join(f"{name}={count}" for name, count in sorted(strategy_counts.items())) or "none"

    if scanned_total is not None and status_filters and scanned_total != total:
        filter_label = ", ".join(status_filters)
        head = f"Found {scanned_total} project(s), showing {total} (status={filter_label})"
    else:
        head = f"Found {total} project(s)"

    console.print(
        f"{head}: {strategy_part}; "
        f"[green]{worth} worth exporting[/green]"
        + (f", [yellow]{skip} skip/review[/yellow]" if skip else ""),
        highlight=False,
    )


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


def _reports_to_json_text(reports: list[ProjectReport] | list[SafetyReport]) -> str:
    """Serialize project/safety reports to pretty-printed JSON text."""
    payload = [report.to_dict() for report in reports]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _write_json_file(reports: list[ProjectReport] | list[SafetyReport], output: Path) -> None:
    """Write scan/check reports to a JSON file and print a short confirmation."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_reports_to_json_text(reports) + "\n", encoding="utf-8")
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


def _is_interactive() -> bool:
    """Return True when stdin/stdout are TTYs suitable for prompts."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _enable_escape_cancel(question: _QuestionT) -> _QuestionT:
    """Bind ESC so it aborts the prompt the same way as Ctrl+C.

    questionary only wires Ctrl+C / Ctrl+Q by default. ESC is the expected
    cancel key for interactive UIs. Checkbox prompts expose a mutable
    KeyBindings object; confirm prompts wrap bindings in a merged/dynamic
    layer, so we always merge an extra ESC binding onto the Application.
    """
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.key_binding.key_bindings import merge_key_bindings
    from prompt_toolkit.keys import Keys

    application = getattr(question, "application", None)
    if application is None:
        return question

    extra = KeyBindings()

    @extra.add(Keys.Escape, eager=True)
    def _cancel_on_escape(event) -> None:  # type: ignore[no-untyped-def]
        """Exit the prompt with KeyboardInterrupt so .ask() returns None."""
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    existing = application.key_bindings
    if existing is None:
        application.key_bindings = extra
    else:
        application.key_bindings = merge_key_bindings([existing, extra])

    return question


def _render_clean_targets(targets: list[CleanTarget], *, title: str = "Clean Candidates") -> None:
    """Render discovered junk directories as a compact table."""
    table = Table(title=title)
    table.add_column("Project")
    table.add_column("Profile")
    table.add_column("Name")
    table.add_column("Size", justify="right")
    table.add_column("Path")
    for item in targets:
        style = {"deps": "cyan", "cache": "blue", "build": "magenta"}.get(item.profile, "")
        profile = f"[{style}]{item.profile}[/{style}]" if style else item.profile
        table.add_row(item.project_name, profile, item.name, item.size_human, item.path)
    console.print(table)


def _render_clean_profile_summary(targets: list[CleanTarget]) -> None:
    """Print per-profile reclaimable size summary."""
    summary = summarize_targets(targets)
    by_profile = summary["by_profile"]
    assert isinstance(by_profile, dict)
    parts: list[str] = []
    for profile in PROFILE_ORDER:
        data = by_profile.get(profile)
        if not isinstance(data, dict) or not data.get("count"):
            continue
        parts.append(f"{profile}={data['count']} ({data['size_human']})")
    detail = ", ".join(parts) if parts else "nothing to clean"
    console.print(
        f"Found {summary['target_count']} cleanable dir(s), "
        f"reclaimable [green]{summary['total_human']}[/green]: {detail}",
        highlight=False,
    )


def _render_clean_result(targets: list[CleanTarget]) -> None:
    """Render post-apply deletion results."""
    table = Table(title="Clean Result")
    table.add_column("Project")
    table.add_column("Profile")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Size", justify="right")
    table.add_column("Detail")
    status_style = {
        "deleted": "green",
        "failed": "red",
        "skipped": "yellow",
        "pending": "dim",
    }
    for item in targets:
        style = status_style.get(item.status, "")
        status = f"[{style}]{item.status}[/{style}]" if style else item.status
        table.add_row(
            item.project_name,
            item.profile,
            item.name,
            status,
            item.size_human,
            item.detail or "-",
        )
    console.print(table)

    deleted = [item for item in targets if item.status == "deleted"]
    failed = [item for item in targets if item.status == "failed"]
    skipped = [item for item in targets if item.status == "skipped"]
    freed = sum(item.size_bytes for item in deleted)
    console.print(
        f"Clean summary: deleted={len(deleted)}, failed={len(failed)}, skipped={len(skipped)}; "
        f"freed [green]{format_size(freed)}[/green]",
        highlight=False,
    )


def _prompt_clean_profiles(plan: CleanPlan) -> list[str] | None:
    """Interactively let the user pick clean profiles (checkbox).

    Returns selected profile names, or None if the user cancelled.
    """
    import questionary
    from questionary import Choice, Style

    summary = summarize_targets(plan.targets)
    by_profile = summary["by_profile"]
    assert isinstance(by_profile, dict)

    choices: list[Choice] = []
    for profile in PROFILE_ORDER:
        data = by_profile.get(profile) if isinstance(by_profile.get(profile), dict) else None
        count = int(data["count"]) if data else 0
        size_human = str(data["size_human"]) if data else "0B"
        names = ", ".join(sorted(CLEAN_PROFILES[profile]))
        label = f"{profile:5}  {count:3} dir(s)  {size_human:>8}  [{names}]"
        # Recommend deps by default; still show empty profiles as disabled-looking info.
        choices.append(
            Choice(
                title=label,
                value=profile,
                checked=(profile == "deps" and count > 0),
                disabled="no matches" if count == 0 else None,
            )
        )

    style = Style(
        [
            ("qmark", "fg:cyan bold"),
            ("question", "bold"),
            ("answer", "fg:cyan"),
            ("pointer", "fg:cyan bold"),
            ("highlighted", "fg:cyan bold"),
            ("selected", "fg:green"),
        ]
    )
    selected = _enable_escape_cancel(
        questionary.checkbox(
            "Select profiles to clean (space to toggle, enter to confirm, esc to cancel):",
            choices=choices,
            style=style,
            instruction="deps is recommended; cache/build are optional · esc cancels",
        )
    ).ask()
    if selected is None:
        return None
    return list(selected)


def _prompt_confirm_clean(targets: list[CleanTarget]) -> bool | None:
    """Ask the user to confirm destructive deletion.

    Returns True/False for yes/no, or None if the prompt was cancelled.
    """
    import questionary

    total = format_size(sum(item.size_bytes for item in targets))
    result = _enable_escape_cancel(
        questionary.confirm(
            f"Permanently delete {len(targets)} directory(ies) (~{total})?",
            default=False,
            instruction="(y/N, esc to cancel) ",
        )
    ).ask()
    if result is None:
        return None
    return bool(result)


def _prompt_apply_now() -> bool | None:
    """Ask whether to apply the selected dry-run plan now.

    Returns True/False for yes/no, or None if the prompt was cancelled.
    """
    import questionary

    result = _enable_escape_cancel(
        questionary.confirm(
            "Apply deletion for the selected profiles now?",
            default=False,
            instruction="(y/N, esc to cancel) ",
        )
    ).ask()
    if result is None:
        return None
    return bool(result)


@app.command("scan")
def scan(
    roots: list[Path] = typer.Argument(..., exists=True, readable=True, resolve_path=True),
    exclude: list[str] = typer.Option([], "--exclude", help="Additional directory names to exclude"),
    large_dir_threshold_mb: int = typer.Option(500, "--large-dir-threshold-mb", min=1, help="Mark top-level directories larger than this threshold"),
    depth: int | None = typer.Option(None, "--depth", min=1, help="Max directory depth to scan from each root"),
    status: list[str] = typer.Option(
        [],
        "--status",
        "-s",
        help=(
            "Only show projects matching this status "
            f"(repeatable, OR). Choices: {', '.join(SCAN_STATUS_CHOICES)}"
        ),
        case_sensitive=False,
    ),
    sort_by: str | None = typer.Option(
        None,
        "--sort",
        "-S",
        help=(
            "Sort results by field. "
            f"Choices: {', '.join(SCAN_SORT_CHOICES)}. "
            "size is largest-first; export lists exportable first"
        ),
        case_sensitive=False,
    ),
    reverse: bool = typer.Option(
        False,
        "--reverse",
        "-r",
        help="Reverse the sort order (flips the field's natural direction)",
    ),
    json_output: Path | None = typer.Option(None, "--json-output", help="Write scan result to a JSON file"),
    as_json: bool = typer.Option(False, "--json", help="Print only JSON to stdout (for scripts/pipes)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show full table columns (remote, clean, large dirs, reasons)"),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable progress bars"),
) -> None:
    """Scan local folders and classify archive packaging strategy."""
    normalized_status = [item.strip().lower() for item in status if item and item.strip()]
    unknown = sorted({item for item in normalized_status if item not in SCAN_STATUS_CHOICES})
    if unknown:
        allowed = ", ".join(SCAN_STATUS_CHOICES)
        console.print(
            f"[red]Unknown status filter(s): {', '.join(unknown)}. Allowed: {allowed}[/red]"
        )
        raise typer.Exit(code=2)

    try:
        sort_key = _normalize_sort_key(sort_by, SCAN_SORT_CHOICES)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=2) from error

    options = default_scan_options(exclude, large_dir_threshold_mb, depth)
    # Pure JSON mode should stay quiet aside from the payload itself.
    show_progress = not no_progress and not as_json

    if not show_progress:
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

    scanned_total = len(reports)
    reports = _filter_reports_by_status(reports, normalized_status)
    reports = sort_project_reports(reports, sort_key, reverse=reverse)

    if as_json:
        # Machine-readable path: plain JSON only (no Rich styling / tables).
        print(_reports_to_json_text(reports))
    else:
        _render_reports(reports, verbose=verbose)
        _render_scan_summary(
            reports,
            scanned_total=scanned_total,
            status_filters=normalized_status,
        )

    if json_output is not None:
        _write_json_file(reports, json_output)


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
        _write_json_file(reports, json_output)


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


@app.command("clean")
def clean(
    roots: list[Path] = typer.Argument(..., exists=True, readable=True, resolve_path=True),
    profile: list[str] = typer.Option(
        [],
        "--profile",
        "-p",
        help=(
            "Clean profile to include (repeatable). "
            f"Choices: {', '.join(PROFILE_CHOICES)}. "
            "If omitted in an interactive terminal, a checkbox UI is shown."
        ),
        case_sensitive=False,
    ),
    apply_changes: bool = typer.Option(
        False,
        "--apply",
        help="Actually delete directories (default is dry-run preview only)",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompts when used with --apply",
    ),
    depth: int | None = typer.Option(None, "--depth", min=1, help="Max directory depth to discover projects from each root"),
    exclude: list[str] = typer.Option([], "--exclude", help="Additional directory names to skip while discovering projects"),
    sort_by: str | None = typer.Option(
        None,
        "--sort",
        "-S",
        help=(
            "Sort clean candidates by field. "
            f"Choices: {', '.join(CLEAN_SORT_CHOICES)}. "
            "size is largest-first; profile follows deps -> cache -> build"
        ),
        case_sensitive=False,
    ),
    reverse: bool = typer.Option(
        False,
        "--reverse",
        "-r",
        help="Reverse the sort order (flips the field's natural direction)",
    ),
    json_output: Path | None = typer.Option(None, "--json-output", help="Write clean plan/result to a JSON file"),
    as_json: bool = typer.Option(False, "--json", help="Print only JSON to stdout (implies non-interactive)"),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable progress bars"),
    no_interactive: bool = typer.Option(
        False,
        "--no-interactive",
        help="Disable prompts; require --profile for filtering and --yes with --apply",
    ),
) -> None:
    """Preview or delete regenerable junk dirs (node_modules, .venv, caches, builds).

    Default mode is dry-run: lists deps/cache/build candidates with sizes.
    Use --apply to delete. Interactive terminals can checkbox-select profiles.
    """
    interactive = _is_interactive() and not no_interactive and not as_json

    raw_profiles = [item.strip().lower() for item in profile if item and item.strip()]
    unknown = sorted({item for item in raw_profiles if item not in PROFILE_CHOICES})
    if unknown:
        allowed = ", ".join(PROFILE_CHOICES)
        console.print(f"[red]Unknown profile(s): {', '.join(unknown)}. Allowed: {allowed}[/red]")
        raise typer.Exit(code=2)

    try:
        sort_key = _normalize_sort_key(sort_by, CLEAN_SORT_CHOICES)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=2) from error

    options = default_scan_options(exclude, depth=depth)
    show_progress = not no_progress and not as_json

    if not show_progress:
        plan = discover_clean_targets(roots, options, on_project_scanned=None)
    else:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TextColumn("{task.fields[current]}", justify="left"),
            console=console,
        ) as progress:
            task_id = progress.add_task("Scanning projects", total=None, current="")

            def on_project_scanned(project_path: Path, completed: int, total: int) -> None:
                progress.update(
                    task_id,
                    total=total,
                    completed=completed,
                    current=str(project_path),
                )

            plan = discover_clean_targets(roots, options, on_project_scanned=on_project_scanned)

    # Optional re-order of the discovery plan (default order already PROFILE_ORDER + size).
    if sort_key is not None:
        plan = CleanPlan(
            projects=list(plan.projects),
            targets=sort_clean_targets(plan.targets, sort_key, reverse=reverse),
        )

    if not plan.targets:
        if as_json:
            print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
        else:
            console.print("No cleanable directories found under the given roots.")
        if json_output is not None:
            json_output.parent.mkdir(parents=True, exist_ok=True)
            json_output.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            console.print(f"Wrote JSON report to {json_output}")
        return

    # Always preview the full inventory first (all profiles), unless pure JSON mode.
    if not as_json:
        _render_clean_targets(plan.targets, title="Clean Candidates (all profiles)")
        _render_clean_profile_summary(plan.targets)
        console.print(
            "[dim]Profiles: deps=reinstallable packages, cache=tool caches, "
            "build=build outputs (higher risk)[/dim]"
        )

    selected_profiles: list[str]
    if raw_profiles:
        selected_profiles = sorted(normalize_profiles(raw_profiles))
    elif interactive:
        picked = _prompt_clean_profiles(plan)
        if picked is None:
            console.print("Cancelled.")
            raise typer.Exit(code=1)
        if not picked:
            console.print("No profiles selected.")
            raise typer.Exit(code=0)
        selected_profiles = picked
    else:
        # Non-interactive dry-run without --profile: show everything, do not delete.
        selected_profiles = list(PROFILE_ORDER)
        if apply_changes:
            console.print(
                "[red]Refusing --apply without --profile in non-interactive mode. "
                "Pass -p deps (and/or cache, build, all) explicitly.[/red]"
            )
            raise typer.Exit(code=2)

    selected_plan = plan.filter_profiles(selected_profiles)
    if not selected_plan.targets:
        if as_json:
            print(json.dumps(selected_plan.to_dict(), ensure_ascii=False, indent=2))
        else:
            console.print(
                f"No targets matched selected profile(s): {', '.join(selected_profiles)}"
            )
        return

    if not as_json and (raw_profiles or interactive):
        _render_clean_targets(
            selected_plan.targets,
            title=f"Selected ({', '.join(selected_profiles)})",
        )
        _render_clean_profile_summary(selected_plan.targets)

    should_apply = apply_changes
    if not should_apply and interactive and not as_json:
        # Dry-run default, but offer to apply after profile selection.
        answer = _prompt_apply_now()
        if answer is None:
            console.print("Cancelled.")
            raise typer.Exit(code=1)
        should_apply = answer

    if not should_apply:
        if as_json:
            print(json.dumps(selected_plan.to_dict(), ensure_ascii=False, indent=2))
        else:
            console.print(
                "[yellow]Dry-run only[/yellow] — no files deleted. "
                "Re-run with [bold]--apply[/bold] (and optionally [bold]-p/--profile[/bold]) to delete."
            )
        if json_output is not None:
            json_output.parent.mkdir(parents=True, exist_ok=True)
            json_output.write_text(
                json.dumps(selected_plan.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            console.print(f"Wrote JSON report to {json_output}")
        return

    if not yes:
        if interactive:
            confirmed = _prompt_confirm_clean(selected_plan.targets)
            if confirmed is None or not confirmed:
                console.print("Cancelled.")
                raise typer.Exit(code=1)
        else:
            console.print(
                "[red]Refusing --apply without --yes in non-interactive mode.[/red]"
            )
            raise typer.Exit(code=2)

    if show_progress:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TextColumn("{task.fields[current]}", justify="left"),
            console=console,
        ) as progress:
            task_id = progress.add_task("Deleting", total=len(selected_plan.targets), current="")

            def on_target_processed(target: CleanTarget, completed: int, total: int) -> None:
                progress.update(task_id, completed=completed, total=total, current=target.path)

            apply_clean_targets(selected_plan.targets, on_target_processed=on_target_processed)
    else:
        apply_clean_targets(selected_plan.targets, on_target_processed=None)

    if as_json:
        print(json.dumps(selected_plan.to_dict(), ensure_ascii=False, indent=2))
    else:
        _render_clean_result(selected_plan.targets)

    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(selected_plan.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if not as_json:
            console.print(f"Wrote JSON report to {json_output}")

    if any(item.status == "failed" for item in selected_plan.targets):
        raise typer.Exit(code=1)


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