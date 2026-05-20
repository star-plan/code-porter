from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .models import PlanAction
from .planner import DEFAULT_RSYNC_EXCLUDES, MigrationPlan


@dataclass(slots=True)
class ExecutionResult:
    project_name: str
    action: PlanAction
    status: str
    detail: str


def execute_plans(
    plans: list[MigrationPlan],
    dry_run: bool = True,
    continue_on_error: bool = False,
) -> list[ExecutionResult]:
    results: list[ExecutionResult] = []
    for plan in plans:
        if dry_run:
            results.append(
                ExecutionResult(
                    project_name=plan.project_name,
                    action=plan.action,
                    status="dry-run",
                    detail=plan.command,
                )
            )
            continue

        try:
            if plan.action == PlanAction.CLONE:
                execute_clone(plan)
            elif plan.action == PlanAction.PULL:
                execute_pull(plan)
            elif plan.action == PlanAction.BUNDLE:
                execute_bundle(plan)
            elif plan.action == PlanAction.SYNC:
                execute_sync(plan)
            results.append(
                ExecutionResult(
                    project_name=plan.project_name,
                    action=plan.action,
                    status="ok",
                    detail=plan.reason,
                )
            )
        except Exception as exc:
            results.append(
                ExecutionResult(
                    project_name=plan.project_name,
                    action=plan.action,
                    status="failed",
                    detail=str(exc),
                )
            )
            if not continue_on_error:
                break

    return results


def execute_clone(plan: MigrationPlan) -> None:
    if not plan.remote_url:
        raise RuntimeError("missing remote URL for clone")
    run_command(["git", "clone", plan.remote_url, plan.destination_path])


def execute_pull(plan: MigrationPlan) -> None:
    run_command(["git", "-C", plan.destination_path, "pull", "--ff-only"])


def execute_bundle(plan: MigrationPlan) -> None:
    destination = Path(plan.destination_path)
    if destination.exists():
        raise RuntimeError("destination already exists for bundle migration")

    with tempfile.TemporaryDirectory(prefix="code-porter-bundle-") as temp_dir:
        bundle_path = Path(temp_dir) / f"{plan.project_name}.bundle"
        if plan.source_host:
            remote_temp = get_remote_temp_dir(plan.source_host)
            remote_bundle = f"{remote_temp}\\migration-bundles\\{plan.project_name}.bundle"
            run_ssh_powershell(
                plan.source_host,
                (
                    f"$dir = '{remote_temp}\\migration-bundles'; "
                    "New-Item -ItemType Directory -Path $dir -Force | Out-Null; "
                    f"git -C '{plan.source_path}' bundle create '{remote_bundle}' --all"
                ),
            )
            try:
                scp_from_remote(plan.source_host, remote_bundle, bundle_path)
                run_command(["git", "clone", str(bundle_path), plan.destination_path])
            finally:
                run_ssh_powershell(
                    plan.source_host,
                    f"Remove-Item '{remote_bundle}' -Force -ErrorAction SilentlyContinue",
                )
            return

        run_command(["git", "-C", plan.source_path, "bundle", "create", str(bundle_path), "--all"])
        run_command(["git", "clone", str(bundle_path), plan.destination_path])


def execute_sync(plan: MigrationPlan) -> None:
    destination = Path(plan.destination_path)
    destination.mkdir(parents=True, exist_ok=True)

    if plan.source_host:
        execute_remote_sync(plan, destination)
        return

    command = ["rsync", "-av"]
    for name in DEFAULT_RSYNC_EXCLUDES:
        command.append(f"--exclude={name}")
    source = Path(plan.source_path)
    command.extend([f"{source}/", f"{destination}/"])
    run_command(command)


def execute_remote_sync(plan: MigrationPlan, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="code-porter-sync-") as temp_dir:
        archive_path = Path(temp_dir) / f"{plan.project_name}.zip"
        remote_temp = get_remote_temp_dir(plan.source_host or "")
        remote_archive_dir = f"{remote_temp}\\migration-archives"
        remote_stage_dir = f"{remote_temp}\\migration-stage\\{plan.project_name}"
        remote_archive = f"{remote_archive_dir}\\{plan.project_name}.zip"

        excludes = " ".join(DEFAULT_RSYNC_EXCLUDES)
        run_ssh_powershell(
            plan.source_host or "",
            (
                f"$archiveDir = '{remote_archive_dir}'; "
                f"$stageDir = '{remote_stage_dir}'; "
                "New-Item -ItemType Directory -Path $archiveDir -Force | Out-Null; "
                "Remove-Item $stageDir -Recurse -Force -ErrorAction SilentlyContinue; "
                "New-Item -ItemType Directory -Path $stageDir -Force | Out-Null; "
                f"robocopy '{plan.source_path}' $stageDir /E /R:1 /W:1 /XD {excludes} | Out-Null; "
                "if ($LASTEXITCODE -ge 8) { throw 'robocopy failed' }; "
                f"if (Test-Path '{remote_archive}') {{ Remove-Item '{remote_archive}' -Force }}; "
                f"Compress-Archive -Path (Join-Path $stageDir '*') -DestinationPath '{remote_archive}' -Force"
            ),
        )
        try:
            scp_from_remote(plan.source_host or "", remote_archive, archive_path)
            extract_zip(archive_path, destination)
        finally:
            run_ssh_powershell(
                plan.source_host or "",
                (
                    f"Remove-Item '{remote_stage_dir}' -Recurse -Force -ErrorAction SilentlyContinue; "
                    f"Remove-Item '{remote_archive}' -Force -ErrorAction SilentlyContinue"
                ),
            )


def extract_zip(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path, "r") as zip_file:
        zip_file.extractall(destination)


def run_ssh_powershell(host: str, script: str) -> subprocess.CompletedProcess[str]:
    return run_command(["ssh", host, "powershell", "-NoProfile", "-Command", script], capture_output=True)


def get_remote_temp_dir(host: str) -> str:
    result = run_ssh_powershell(host, "$env:TEMP")
    temp_dir = result.stdout.strip()
    if not temp_dir:
        raise RuntimeError("failed to resolve remote temp directory")
    return temp_dir.replace("/", "\\")


def scp_from_remote(host: str, remote_path: str, local_path: Path) -> None:
    normalized = normalize_scp_remote_path(remote_path)
    run_command(["scp", f"{host}:{normalized}", str(local_path)])


def normalize_scp_remote_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if len(normalized) >= 2 and normalized[1] == ":" and not normalized.startswith("/"):
        return f"/{normalized}"
    return normalized


def run_command(args: list[str], capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(args[0])
    if executable is None:
        raise RuntimeError(f"required command not found: {args[0]}")
    return subprocess.run(args, check=True, text=True, capture_output=capture_output)