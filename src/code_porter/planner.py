from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from .models import MigrationStrategy, PlanAction, ProjectReport, ProjectType

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
    action: PlanAction
    source_path: str
    destination_path: str
    command: str
    reason: str
    source_host: str | None = None
    remote_url: str | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            "project_name": self.project_name,
            "strategy": self.strategy.value,
            "action": self.action.value,
            "source_path": self.source_path,
            "destination_path": self.destination_path,
            "command": self.command,
            "reason": self.reason,
            "source_host": self.source_host or "",
            "remote_url": self.remote_url or "",
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
                remote_name=item.get("remote_name") or None,
                remote_url=item.get("remote_url") or None,
                is_clean=item["is_clean"],
                size_bytes=item["size_bytes"],
                large_directories=item.get("large_directories", []),
                ignored_directories_present=item.get("ignored_directories_present", []),
                migration_strategy=MigrationStrategy(item["migration_strategy"]),
                migration_reason=item.get("migration_reason", ""),
                worth_migrating=item.get("worth_migrating", True),
                worth_reason=item.get("worth_reason", ""),
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
        action, command, reason = build_command(report, destination_path, source_host, bundle_temp_dir)
        plans.append(
            MigrationPlan(
                project_name=report.name,
                strategy=report.migration_strategy,
                action=action,
                source_path=report.path,
                destination_path=str(destination_path),
                command=command,
                reason=reason,
                source_host=source_host,
                remote_url=report.remote_url,
            )
        )

    return plans


def build_command(
    report: ProjectReport,
    destination_path: Path,
    source_host: str | None,
    bundle_temp_dir: str,
) -> tuple[PlanAction, str, str]:
    destination = shlex.quote(str(destination_path))
    source_path = report.path.replace("\\", "/")
    source_target = build_remote_path(source_host, source_path) if source_host else shlex.quote(source_path)

    if not report.worth_migrating:
        return PlanAction.SKIP, "# skip: not worth migrating", report.worth_reason or report.migration_reason

    if report.migration_strategy == MigrationStrategy.CLONE:
        if not report.remote_url:
            return PlanAction.SKIP, "# skip: missing remote url", "缺少 remote URL，无法安全 clone"

        if destination_path.exists():
            remote_url = detect_local_remote_url(destination_path)
            if remote_url and remote_url == report.remote_url:
                return PlanAction.PULL, f"git -C {destination} pull --ff-only", "目标目录已存在同源仓库，执行 pull"
            return PlanAction.SKIP, "# skip: destination already exists", "目标目录已存在且无法确认可直接 pull"

        return PlanAction.CLONE, f"git clone {shlex.quote(report.remote_url)} {destination}", report.migration_reason

    if report.migration_strategy == MigrationStrategy.BUNDLE:
        if destination_path.exists():
            return PlanAction.SKIP, "# skip: destination already exists", "bundle 迁移要求目标目录不存在"
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
            return PlanAction.BUNDLE, " && ".join([create_bundle, copy_bundle, clone_bundle, cleanup_bundle]), report.migration_reason
        return PlanAction.BUNDLE, (
            f"git -C {shlex.quote(report.path)} bundle create {shlex.quote(bundle_name)} --all"
            f" && git clone {shlex.quote(bundle_name)} {destination}"
        ), report.migration_reason

    if report.migration_strategy == MigrationStrategy.RSYNC:
        if source_host:
            archive_name = f"{report.name}.zip"
            remote_archive = f"$env:TEMP/migration-archives/{archive_name}"
            create_archive = (
                "ssh "
                f"{shlex.quote(source_host)} "
                f"\"powershell -NoProfile -Command \\\"robocopy '{report.path}' '$env:TEMP\\migration-stage\\{report.name}' /E /XD {render_robocopy_excludes()} | Out-Null; "
                f"if ($LASTEXITCODE -ge 8) {{ throw 'robocopy failed' }}; "
                f"Compress-Archive -Path '$env:TEMP\\migration-stage\\{report.name}\\*' -DestinationPath '{remote_archive.replace('/', '\\')}' -Force\\\"\""
            )
            copy_archive = f"scp {shlex.quote(f'{source_host}:{remote_archive}')} {shlex.quote(archive_name)}"
            unpack_archive = f"mkdir -p {destination} && ditto -x -k {shlex.quote(archive_name)} {destination}"
            cleanup_archive = (
                "ssh "
                f"{shlex.quote(source_host)} "
                f"\"powershell -NoProfile -Command \\\"Remove-Item '$env:TEMP\\migration-stage\\{report.name}' -Recurse -Force -ErrorAction SilentlyContinue; Remove-Item '{remote_archive.replace('/', '\\')}' -Force -ErrorAction SilentlyContinue\\\"\""
            )
            return PlanAction.SYNC, " && ".join([create_archive, copy_archive, unpack_archive, cleanup_archive]), report.migration_reason

        flags = " ".join(f"--exclude={shlex.quote(name)}" for name in DEFAULT_RSYNC_EXCLUDES)
        local_source = shlex.quote(f"{source_path.rstrip('/')}/")
        return PlanAction.SYNC, f"mkdir -p {destination} && rsync -av {flags} {local_source} {destination}/", report.migration_reason

    return PlanAction.SKIP, "# skip: inspect manually", report.migration_reason


def detect_local_remote_url(path: Path) -> str | None:
    git_dir = path / ".git"
    if not git_dir.exists():
        return None

    import subprocess

    remote_result = subprocess.run(
        ["git", "-C", str(path), "remote"],
        capture_output=True,
        text=True,
        check=False,
    )
    if remote_result.returncode != 0 or not remote_result.stdout.strip():
        return None

    remote_name = remote_result.stdout.splitlines()[0].strip()
    url_result = subprocess.run(
        ["git", "-C", str(path), "remote", "get-url", remote_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if url_result.returncode != 0:
        return None
    return url_result.stdout.strip() or None


def build_remote_path(source_host: str | None, path: str) -> str:
    if not source_host:
        return shlex.quote(path)
    normalized = path.replace("\\", "/")
    return shlex.quote(f"{source_host}:{normalized}")


def render_robocopy_excludes() -> str:
    return " ".join(DEFAULT_RSYNC_EXCLUDES)