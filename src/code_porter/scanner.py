from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .models import PackagingStrategy, ProjectReport, ProjectType

MARKERS: dict[str, ProjectType] = {
    "package.json": ProjectType.NODE,
    "pyproject.toml": ProjectType.PYTHON,
    "go.mod": ProjectType.GO,
    "Cargo.toml": ProjectType.RUST,
}
DEFAULT_EXCLUDES = {
    ".cache",
    ".git",
    ".next",
    ".venv",
    ".uv-cache",
    "build",
    "dist",
    "node_modules",
    "target",
}


@dataclass(slots=True)
class ScanOptions:
    excludes: set[str]
    large_dir_threshold_mb: int = 500


def default_scan_options(
    extra_excludes: list[str] | None = None,
    large_dir_threshold_mb: int = 500,
) -> ScanOptions:
    excludes = set(DEFAULT_EXCLUDES)
    if extra_excludes:
        excludes.update(item for item in extra_excludes if item)
    return ScanOptions(excludes=excludes, large_dir_threshold_mb=large_dir_threshold_mb)


def scan_local_roots(paths: list[Path], options: ScanOptions) -> list[ProjectReport]:
    project_roots: dict[Path, ProjectType] = {}
    for root in paths:
        for candidate, project_type in discover_projects(root, options.excludes).items():
            current_type = project_roots.get(candidate)
            if current_type is None or current_type == ProjectType.UNKNOWN:
                project_roots[candidate] = project_type
    return [inspect_local_project(path, project_roots[path], options) for path in sorted(project_roots)]


def scan_local_roots_with_progress(
    paths: list[Path],
    options: ScanOptions,
    on_root_scanned: Callable[[Path, int, int], None] | None = None,
) -> list[ProjectReport]:
    project_roots: dict[Path, ProjectType] = {}
    total = len(paths)
    for index, root in enumerate(paths, start=1):
        for candidate, project_type in discover_projects(root, options.excludes).items():
            current_type = project_roots.get(candidate)
            if current_type is None or current_type == ProjectType.UNKNOWN:
                project_roots[candidate] = project_type
        if on_root_scanned is not None:
            on_root_scanned(root, index, total)
    return [inspect_local_project(path, project_roots[path], options) for path in sorted(project_roots)]


def discover_projects(root: Path, excludes: set[str]) -> dict[Path, ProjectType]:
    if not root.exists():
        raise FileNotFoundError(f"Scan root does not exist: {root}")

    project_roots: dict[Path, ProjectType] = {}
    for current_path, dir_names, file_names in root.walk(top_down=True):
        has_git_dir = ".git" in dir_names
        dir_names[:] = [name for name in dir_names if name not in excludes]
        file_set = set(file_names)

        if has_git_dir or (current_path / ".git").exists():
            project_roots.setdefault(current_path, infer_project_type(file_set))

        detected_type = infer_project_type(file_set)
        if detected_type != ProjectType.UNKNOWN:
            project_root = resolve_project_root(current_path, root)
            existing = project_roots.get(project_root)
            if existing is None or existing == ProjectType.UNKNOWN:
                project_roots[project_root] = detected_type

    return project_roots


def resolve_project_root(path: Path, scan_root: Path) -> Path:
    current = path
    while True:
        if (current / ".git").exists():
            return current
        if current == scan_root:
            return path
        current = current.parent


def infer_project_type(file_set: set[str]) -> ProjectType:
    if any(name.endswith(".sln") for name in file_set):
        return ProjectType.DOTNET
    for marker, project_type in MARKERS.items():
        if marker in file_set:
            return project_type
    return ProjectType.UNKNOWN


def inspect_local_project(path: Path, project_type: ProjectType, options: ScanOptions) -> ProjectReport:
    git_dir = path / ".git"
    is_git_repo = git_dir.exists()
    remote_name, remote_url, has_remote, is_clean = inspect_local_git_state(path, is_git_repo)
    has_commits = git_has_commits(path, is_git_repo)

    size_bytes, large_directories, ignored_present = summarize_directory(path, options)
    packaging_strategy, packaging_reason = choose_packaging_strategy(is_git_repo, is_clean, has_commits)
    worth_exporting, worth_reason = assess_export_value(packaging_strategy, size_bytes)

    return ProjectReport(
        name=path.name,
        path=str(path),
        project_type=project_type,
        is_git_repo=is_git_repo,
        has_remote=has_remote,
        is_clean=is_clean,
        size_bytes=size_bytes,
        remote_name=remote_name,
        remote_url=remote_url,
        large_directories=large_directories,
        ignored_directories_present=ignored_present,
        packaging_strategy=packaging_strategy,
        packaging_reason=packaging_reason,
        worth_exporting=worth_exporting,
        worth_reason=worth_reason,
    )


def inspect_local_git_state(path: Path, is_git_repo: bool) -> tuple[str | None, str | None, bool, bool | None]:
    if not is_git_repo:
        return None, None, False, None

    remote_name: str | None = None
    remote_url: str | None = None
    remote_result = run_git(path, ["remote"])
    has_remote = remote_result.returncode == 0 and bool(remote_result.stdout.strip())
    if has_remote:
        remote_name = remote_result.stdout.splitlines()[0].strip()
        url_result = run_git(path, ["remote", "get-url", remote_name])
        if url_result.returncode == 0:
            remote_url = url_result.stdout.strip() or None

    status_result = run_git(path, ["status", "--porcelain"])
    is_clean: bool | None = None
    if status_result.returncode == 0:
        is_clean = not bool(status_result.stdout.strip())
    return remote_name, remote_url, has_remote, is_clean


def git_has_commits(path: Path, is_git_repo: bool) -> bool:
    if not is_git_repo:
        return False
    result = run_git(path, ["rev-parse", "--verify", "HEAD"])
    return result.returncode == 0


def summarize_directory(path: Path, options: ScanOptions) -> tuple[int, list[str], list[str]]:
    total_size = 0
    directory_sizes: dict[str, int] = {}
    ignored_present: set[str] = set()

    for current_path, dir_names, file_names in path.walk(top_down=True):
        ignored_here = [name for name in dir_names if name in options.excludes]
        ignored_present.update(ignored_here)
        dir_names[:] = [name for name in dir_names if name not in options.excludes]

        relative = current_path.relative_to(path)
        bucket = "." if str(relative) == "." else relative.parts[0]
        for file_name in file_names:
            file_path = current_path / file_name
            try:
                size = file_path.stat().st_size
            except OSError:
                continue
            total_size += size
            directory_sizes[bucket] = directory_sizes.get(bucket, 0) + size

    threshold = options.large_dir_threshold_mb * 1024 * 1024
    large_directories = sorted(name for name, size in directory_sizes.items() if name != "." and size >= threshold)
    return total_size, large_directories, sorted(ignored_present)


def choose_packaging_strategy(
    is_git_repo: bool,
    is_clean: bool | None,
    has_commits: bool,
) -> tuple[PackagingStrategy, str]:
    if is_git_repo and not has_commits:
        return PackagingStrategy.ZIP, "Git 仓库尚无提交，无法创建 bundle，导出 zip"
    if is_git_repo and is_clean:
        return PackagingStrategy.BUNDLE, "Git 仓库干净，导出 git bundle"
    if is_git_repo and is_clean is False:
        return PackagingStrategy.BUNDLE_WITH_OVERLAY, "Git 仓库有未提交改动，导出 bundle 并附带工作区 zip"
    if not is_git_repo:
        return PackagingStrategy.ZIP, "非 Git 项目，导出 zip"
    return PackagingStrategy.SKIP, "无法确认仓库状态，先跳过人工确认"


def assess_export_value(strategy: PackagingStrategy, size_bytes: int) -> tuple[bool, str]:
    if strategy == PackagingStrategy.SKIP:
        return False, "仓库状态无法自动确认，需要人工检查"
    if size_bytes <= 0:
        return False, "目录为空，不建议导出"
    return True, "满足导出条件"


def run_git(path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )