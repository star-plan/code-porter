from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path

from .models import MigrationStrategy, ProjectReport, ProjectType

DEFAULT_RSYNC_EXCLUDES = [
    ".cache",
    ".next",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "target",
]


@dataclass(slots=True)
class MigrationPlan:
    project_name: str
    strategy: MigrationStrategy
    source_path: str
    destination_path: str
    command: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "project_name": self.project_name,
            "strategy": self.strategy.value,
            "source_path": self.source_path,
            "destination_path": self.destination_path,
            "command": self.command,
            "reason": self.reason,
        }


def load_reports(path: Path) -> list[ProjectReport]:
    data = json.loads(path.read_text(encoding="utf-8"))
    reports: list[ProjectReport] = []
    for item in data:
        reports.append(
            ProjectReport(
                name=item["name"],
                path=item["path"],
                project_type=ProjectType(item["project_type"]),
                is_git_repo=item["is_git_repo"],
                has_remote=item["has_remote"],
                is_clean=item["is_clean"],
                size_bytes=item["size_bytes"],
                large_directories=item.get("large_directories", []),
                ignored_directories_present=item.get("ignored_directories_present", []),
                migration_strategy=MigrationStrategy(item["migration_strategy"]),
                migration_reason=item.get("migration_reason", ""),
            )
        )
    return reports


def build_plans(
    reports: list[ProjectReport],
    destination_root: Path,
    source_host: str | None = None,
    bundle_temp_dir: str = r"$env:TEMP\migration-bundles",
) -> list[MigrationPlan]:
    destination_root = destination_root.expanduser().resolve()
    plans: list[MigrationPlan] = []

    for report in reports:
        destination_path = destination_root / report.name
        command = build_command(report, destination_path, source_host, bundle_temp_dir)
        plans.append(
            MigrationPlan(
                project_name=report.name,
                strategy=report.migration_strategy,
                source_path=report.path,
                destination_path=str(destination_path),
                command=command,
                reason=report.migration_reason,
            )
        )

    return plans


def build_command(
    report: ProjectReport,
    destination_path: Path,
    source_host: str | None,
    bundle_temp_dir: str,
) -> str:
    destination = shlex.quote(str(destination_path))
    source_path = report.path.replace("\\", "/")
    source_target = build_remote_path(source_host, source_path) if source_host else shlex.quote(source_path)

    if report.migration_strategy == MigrationStrategy.CLONE:
        return f"git clone {source_target} {destination}"

    if report.migration_strategy == MigrationStrategy.BUNDLE:
        bundle_name = f"{report.name}.bundle"
        remote_bundle_dir = bundle_temp_dir.replace("\\", "/")
        remote_bundle_path = f"{remote_bundle_dir}/{bundle_name}"
        if source_host:
            create_bundle = (
                "ssh "
                f"{shlex.quote(source_host)} "
                f"\"powershell -NoProfile -Command \\\"$dir = '{bundle_temp_dir}'; "
                "New-Item -ItemType Directory -Path $dir -Force | Out-Null; "
                f"git -C '{report.path}' bundle create '{remote_bundle_path}' --all\\\"\""
            )
            copy_bundle = f"scp {shlex.quote(f'{source_host}:{remote_bundle_path}')} {shlex.quote(bundle_name)}"
            clone_bundle = f"git clone {shlex.quote(bundle_name)} {destination}"
            cleanup_bundle = (
                "ssh "
                f"{shlex.quote(source_host)} "
                f"\"powershell -NoProfile -Command \\\"Remove-Item '{remote_bundle_path}' -Force\\\"\""
            )
            return " && ".join([create_bundle, copy_bundle, clone_bundle, cleanup_bundle])
        return (
            f"git -C {shlex.quote(report.path)} bundle create {shlex.quote(bundle_name)} --all"
            f" && git clone {shlex.quote(bundle_name)} {destination}"
        )

    if report.migration_strategy == MigrationStrategy.RSYNC:
        flags = " ".join(f"--exclude={shlex.quote(name)}" for name in DEFAULT_RSYNC_EXCLUDES)
        return f"rsync -avz {flags} {source_target.rstrip('/')} {destination}"

    return "# skip: inspect manually"


def build_remote_path(source_host: str | None, path: str) -> str:
    if not source_host:
        return shlex.quote(path)
    normalized = path.replace("\\", "/")
    return shlex.quote(f"{source_host}:{normalized}")