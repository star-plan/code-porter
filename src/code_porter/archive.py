from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pathspec

from .models import ArchiveKind, ExportManifest, PackageEntry, PackagingStrategy, ProjectReport, ProjectType
from .scanner import DEFAULT_EXCLUDES


@dataclass(slots=True)
class ImportResult:
    project_name: str
    status: str
    detail: str


@dataclass(slots=True)
class ExportResult:
    project_name: str
    status: str
    detail: str


@dataclass(slots=True)
class ExportOutcome:
    manifest: ExportManifest
    results: list[ExportResult]


def manifest_relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def resolve_manifest_path(archive_root: Path, manifest_path: str) -> Path:
    return archive_root / Path(manifest_path.replace("\\", "/"))


def bundle_export_unsupported_reason(project_path: Path) -> str | None:
    shallow_result = subprocess.run(
        ["git", "-C", str(project_path), "rev-parse", "--is-shallow-repository"],
        capture_output=True,
        text=True,
        check=False,
    )
    if shallow_result.returncode == 0 and shallow_result.stdout.strip() == "true":
        return "Git 仓库为浅克隆，bundle 无法保证完整，已降级为 zip"
    return None


def format_command_error(error: subprocess.CalledProcessError) -> str:
    stderr = (error.stderr or "").strip()
    stdout = (error.stdout or "").strip()
    message = stderr or stdout or str(error)
    return f"命令失败({error.returncode}): {message}"


def format_exception_detail(error: Exception) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        return format_command_error(error)
    return str(error) or error.__class__.__name__


def cleanup_partial_destination(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)


def export_projects(
    reports: list[ProjectReport],
    output_dir: Path,
    source_roots: list[Path],
    on_project_processed: Callable[[ProjectReport, int, int], None] | None = None,
) -> ExportOutcome:
    output_dir = output_dir.expanduser().resolve()
    artifacts_dir = output_dir / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    packages: list[PackageEntry] = []
    results: list[ExportResult] = []
    total = len(reports)
    for index, report in enumerate(reports, start=1):
        if not report.worth_exporting or report.packaging_strategy == PackagingStrategy.SKIP:
            results.append(ExportResult(report.name, "skipped", report.worth_reason or report.packaging_reason))
            if on_project_processed is not None:
                on_project_processed(report, index, total)
            continue

        project_path = Path(report.path)
        slug = build_package_slug(report)
        package_kind: ArchiveKind
        overlay_path: Path | None = None
        effective_strategy = report.packaging_strategy
        effective_reason = report.packaging_reason
        bundle_unsupported_reason = bundle_export_unsupported_reason(project_path)

        try:
            if report.packaging_strategy == PackagingStrategy.BUNDLE:
                if bundle_unsupported_reason is not None:
                    package_path = artifacts_dir / f"{slug}.zip"
                    create_zip_archive(project_path, package_path)
                    package_kind = ArchiveKind.ZIP
                    effective_strategy = PackagingStrategy.ZIP
                    effective_reason = bundle_unsupported_reason
                else:
                    package_path = artifacts_dir / f"{slug}.bundle"
                    try:
                        create_git_bundle(project_path, package_path)
                        package_kind = ArchiveKind.BUNDLE
                    except subprocess.CalledProcessError:
                        package_path = artifacts_dir / f"{slug}.zip"
                        create_zip_archive(project_path, package_path)
                        package_kind = ArchiveKind.ZIP
                        effective_strategy = PackagingStrategy.ZIP
                        effective_reason = "bundle 导出失败，已降级为 zip"
            elif report.packaging_strategy == PackagingStrategy.BUNDLE_WITH_OVERLAY:
                if bundle_unsupported_reason is not None:
                    package_path = artifacts_dir / f"{slug}.zip"
                    overlay_path = None
                    create_zip_archive(project_path, package_path)
                    package_kind = ArchiveKind.ZIP
                    effective_strategy = PackagingStrategy.ZIP
                    effective_reason = bundle_unsupported_reason
                else:
                    package_path = artifacts_dir / f"{slug}.bundle"
                    overlay_path = artifacts_dir / f"{slug}.worktree.zip"
                    try:
                        create_git_bundle(project_path, package_path)
                        create_zip_archive(project_path, overlay_path)
                        package_kind = ArchiveKind.BUNDLE
                    except subprocess.CalledProcessError:
                        package_path = artifacts_dir / f"{slug}.zip"
                        overlay_path = None
                        create_zip_archive(project_path, package_path)
                        package_kind = ArchiveKind.ZIP
                        effective_strategy = PackagingStrategy.ZIP
                        effective_reason = "bundle 导出失败，已降级为 zip"
            else:
                package_path = artifacts_dir / f"{slug}.zip"
                create_zip_archive(project_path, package_path)
                package_kind = ArchiveKind.ZIP
            packages.append(
                PackageEntry(
                    name=report.name,
                    project_type=report.project_type,
                    source_path=report.path,
                    package_kind=package_kind,
                    package_path=manifest_relative_path(package_path, output_dir),
                    packaging_strategy=effective_strategy,
                    is_git_repo=report.is_git_repo,
                    is_clean=report.is_clean,
                    has_remote=report.has_remote,
                    size_bytes=report.size_bytes,
                    packaging_reason=effective_reason,
                    remote_url=report.remote_url,
                    overlay_path=manifest_relative_path(overlay_path, output_dir) if overlay_path else None,
                    ignored_patterns=collect_ignore_patterns(project_path),
                )
            )
            results.append(ExportResult(report.name, "exported", effective_reason))
        except Exception as error:
            results.append(ExportResult(report.name, "failed", format_exception_detail(error)))
        if on_project_processed is not None:
            on_project_processed(report, index, total)

    manifest = ExportManifest.create([str(path) for path in source_roots], packages)
    (output_dir / "manifest.json").write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ExportOutcome(manifest=manifest, results=results)


def import_packages(
    manifest_path: Path,
    destination_root: Path,
    on_existing: str = "skip",
    on_package_processed: Callable[[PackageEntry, int, int], None] | None = None,
) -> list[ImportResult]:
    manifest = load_manifest(manifest_path)
    archive_root = manifest_path.parent
    destination_root = destination_root.expanduser().resolve()
    destination_root.mkdir(parents=True, exist_ok=True)

    results: list[ImportResult] = []
    total = len(manifest.packages)
    for index, package in enumerate(manifest.packages, start=1):
        destination = destination_root / package.name
        try:
            if destination.exists():
                if on_existing == "replace":
                    shutil.rmtree(destination)
                else:
                    results.append(ImportResult(package.name, "skipped", "目标目录已存在"))
                    if on_package_processed is not None:
                        on_package_processed(package, index, total)
                    continue

            package_file = resolve_manifest_path(archive_root, package.package_path)
            if package.package_kind == ArchiveKind.BUNDLE:
                run_command(["git", "clone", str(package_file), str(destination)])
                if package.overlay_path:
                    extract_zip(resolve_manifest_path(archive_root, package.overlay_path), destination)
                results.append(ImportResult(package.name, "imported", "bundle 导入完成"))
                if on_package_processed is not None:
                    on_package_processed(package, index, total)
                continue

            destination.mkdir(parents=True, exist_ok=True)
            extract_zip(package_file, destination)
            results.append(ImportResult(package.name, "imported", "zip 导入完成"))
        except Exception as error:
            cleanup_partial_destination(destination)
            results.append(ImportResult(package.name, "failed", format_exception_detail(error)))
        if on_package_processed is not None:
            on_package_processed(package, index, total)

    return results


def load_manifest(path: Path) -> ExportManifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    packages = [
        PackageEntry(
            name=item["name"],
            project_type=ProjectType(item["project_type"]),
            source_path=item["source_path"],
            package_kind=ArchiveKind(item["package_kind"]),
            package_path=item["package_path"],
            packaging_strategy=PackagingStrategy(item["packaging_strategy"]),
            is_git_repo=item["is_git_repo"],
            is_clean=item["is_clean"],
            has_remote=item["has_remote"],
            size_bytes=item["size_bytes"],
            packaging_reason=item["packaging_reason"],
            remote_url=item.get("remote_url"),
            overlay_path=item.get("overlay_path"),
            ignored_patterns=item.get("ignored_patterns", []),
        )
        for item in data["packages"]
    ]
    return ExportManifest(
        version=data["version"],
        created_at=data["created_at"],
        source_roots=data["source_roots"],
        packages=packages,
    )


def build_package_slug(report: ProjectReport) -> str:
    digest = hashlib.sha1(report.path.encode("utf-8")).hexdigest()[:8]
    sanitized = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in report.name).strip("-") or "project"
    return f"{sanitized}-{digest}"


def create_git_bundle(project_path: Path, bundle_path: Path) -> None:
    run_command(["git", "-C", str(project_path), "bundle", "create", str(bundle_path), "--all"])


def create_zip_archive(project_path: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    matcher = build_ignore_matcher(project_path)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for current_path, dir_names, file_names in project_path.walk(top_down=True):
            relative_dir = current_path.relative_to(project_path)
            dir_names[:] = [
                name
                for name in dir_names
                if not matcher((relative_dir / name).as_posix(), is_dir=True)
            ]

            for file_name in file_names:
                file_path = current_path / file_name
                rel_path = file_path.relative_to(project_path).as_posix()
                if matcher(rel_path, is_dir=False):
                    continue
                zip_file.write(file_path, rel_path)


def build_ignore_matcher(project_path: Path):
    patterns = collect_ignore_patterns(project_path)
    spec = pathspec.PathSpec.from_lines("gitignore", patterns)

    def matcher(relative_path: str, is_dir: bool) -> bool:
        normalized = relative_path.strip("/")
        if not normalized:
            return False
        if any(part in DEFAULT_EXCLUDES for part in Path(normalized).parts):
            return True
        candidate = f"{normalized}/" if is_dir else normalized
        return spec.match_file(candidate)

    return matcher


def collect_ignore_patterns(project_path: Path) -> list[str]:
    patterns: list[str] = []
    for name in sorted(DEFAULT_EXCLUDES):
        patterns.extend([f"{name}", f"{name}/", f"**/{name}", f"**/{name}/"])

    gitignore_path = project_path / ".gitignore"
    if gitignore_path.exists():
        for line in gitignore_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            patterns.append(stripped)
    return patterns


def extract_zip(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path, "r") as zip_file:
        zip_file.extractall(destination)


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(args[0])
    if executable is None:
        raise RuntimeError(f"required command not found: {args[0]}")
    return subprocess.run(args, check=True, capture_output=True, text=True)