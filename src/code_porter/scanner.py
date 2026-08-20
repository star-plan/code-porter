from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .junk import directory_has_solution_file, has_dotnet_project_file, should_skip_directory
from .models import PackagingStrategy, ProjectReport, ProjectType, SafetyReport

MARKERS: dict[str, ProjectType] = {
    "package.json": ProjectType.NODE,
    "pyproject.toml": ProjectType.PYTHON,
    "go.mod": ProjectType.GO,
    "Cargo.toml": ProjectType.RUST,
}
# Regenerable dirs skipped during walk/size and excluded from zip export.
# Basename match only — nested names like .tmp/uv-cache are still excluded.
DEFAULT_EXCLUDES = {
    ".cache",
    ".git",
    ".gocache",
    ".gomodcache",
    ".next",
    ".venv",
    ".uv-cache",
    ".vs",  # Visual Studio user/cache directory
    "build",
    "dist",
    "gocache",
    "gomodcache",
    "node_modules",
    "target",
    "uv-cache",
}


@dataclass(slots=True)
class ScanOptions:
    excludes: set[str]
    large_dir_threshold_mb: int = 500
    depth: int | None = None


def default_scan_options(
    extra_excludes: list[str] | None = None,
    large_dir_threshold_mb: int = 500,
    depth: int | None = None,
) -> ScanOptions:
    excludes = set(DEFAULT_EXCLUDES)
    if extra_excludes:
        excludes.update(item for item in extra_excludes if item)
    return ScanOptions(excludes=excludes, large_dir_threshold_mb=large_dir_threshold_mb, depth=depth)


def scan_local_roots(paths: list[Path], options: ScanOptions) -> list[ProjectReport]:
    project_roots: dict[Path, ProjectType] = {}
    for root in paths:
        for candidate, project_type in discover_projects(root, options.excludes, options.depth).items():
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
        for candidate, project_type in discover_projects(root, options.excludes, options.depth).items():
            current_type = project_roots.get(candidate)
            if current_type is None or current_type == ProjectType.UNKNOWN:
                project_roots[candidate] = project_type
        if on_root_scanned is not None:
            on_root_scanned(root, index, total)
    return [inspect_local_project(path, project_roots[path], options) for path in sorted(project_roots)]


def discover_projects(root: Path, excludes: set[str], depth: int | None = None) -> dict[Path, ProjectType]:
    """Walk ``root`` and return project directories mapped to inferred types.

    Skips basename excludes and contextual junk (e.g. .NET bin/obj) so those
    trees are not treated as nested projects.
    """
    if not root.exists():
        raise FileNotFoundError(f"Scan root does not exist: {root}")

    project_roots: dict[Path, ProjectType] = {}
    for current_path, dir_names, file_names in root.walk(top_down=True):
        if depth is not None:
            relative = current_path.relative_to(root)
            current_depth = 0 if str(relative) == "." else len(relative.parts)
            if current_depth > depth:
                dir_names.clear()
                continue

        has_git_dir = ".git" in dir_names
        dir_names[:] = [
            name
            for name in dir_names
            if not should_skip_directory(name, file_names, current_path / name, excludes)
        ]
        file_set = set(file_names)

        if has_git_dir or (current_path / ".git").exists():
            project_roots.setdefault(current_path, infer_project_type(file_set))

        detected_type = infer_project_type(file_set)
        if detected_type != ProjectType.UNKNOWN:
            project_root = resolve_project_root(
                current_path,
                root,
                prefer_sln=(detected_type == ProjectType.DOTNET),
            )
            existing = project_roots.get(project_root)
            # Nested csproj should win over a nested package.json (walk order
            # is not a type signal). A later DOTNET marker also upgrades an
            # earlier non-dotnet guess on the same git/sln root.
            if existing is None or existing == ProjectType.UNKNOWN:
                project_roots[project_root] = detected_type
            elif detected_type == ProjectType.DOTNET:
                project_roots[project_root] = ProjectType.DOTNET

    return project_roots


def resolve_project_root(path: Path, scan_root: Path, *, prefer_sln: bool = False) -> Path:
    """Walk up from a marker directory to the owning project root.

    Git always wins. For .NET markers without git, collapse to the nearest
    ancestor (including self) that contains a .sln so a solution is one project
    instead of one project per csproj.
    """
    current = path
    nearest_sln: Path | None = None
    while True:
        if (current / ".git").exists():
            return current
        if prefer_sln and nearest_sln is None and directory_has_solution_file(current):
            nearest_sln = current
        if current == scan_root:
            if prefer_sln and nearest_sln is not None:
                return nearest_sln
            return path
        current = current.parent


def infer_project_type(file_set: set[str]) -> ProjectType:
    """Classify a directory from the filenames it contains.

    .NET project/solution files take priority over package.json and friends so
    an ASP.NET app with a client package.json still types as dotnet.
    """
    if has_dotnet_project_file(file_set):
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
    is_shallow = git_is_shallow(path, is_git_repo)

    size_bytes, large_directories, ignored_present = summarize_directory(path, options)
    packaging_strategy, packaging_reason = choose_packaging_strategy(is_git_repo, is_clean, has_commits, is_shallow)
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


def git_is_shallow(path: Path, is_git_repo: bool) -> bool:
    if not is_git_repo:
        return False
    result = run_git(path, ["rev-parse", "--is-shallow-repository"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def summarize_directory(path: Path, options: ScanOptions) -> tuple[int, list[str], list[str]]:
    """Return total size, large first-level dirs, and ignored junk dir names.

    Contextual .NET bin/obj (and similar) are omitted from the size total and
    listed in ignored names, without treating a script ``bin/`` as junk.
    """
    total_size = 0
    directory_sizes: dict[str, int] = {}
    ignored_present: set[str] = set()

    for current_path, dir_names, file_names in path.walk(top_down=True):
        ignored_here = [
            name
            for name in dir_names
            if should_skip_directory(name, file_names, current_path / name, options.excludes)
        ]
        ignored_present.update(ignored_here)
        dir_names[:] = [name for name in dir_names if name not in ignored_here]

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
    is_shallow: bool,
) -> tuple[PackagingStrategy, str]:
    if is_git_repo and not has_commits:
        return PackagingStrategy.ZIP, "Git 仓库尚无提交，无法创建 bundle，导出 zip"
    if is_git_repo and is_shallow:
        return PackagingStrategy.ZIP, "Git 仓库为浅克隆，bundle 无法保证完整，导出 zip"
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


def check_local_roots(paths: list[Path], options: ScanOptions) -> list[SafetyReport]:
    project_roots: dict[Path, ProjectType] = {}
    for root in paths:
        for candidate, project_type in discover_projects(root, options.excludes, options.depth).items():
            current_type = project_roots.get(candidate)
            if current_type is None or current_type == ProjectType.UNKNOWN:
                project_roots[candidate] = project_type
    return [check_safety(path, project_roots[path]) for path in sorted(project_roots)]


def check_local_roots_with_progress(
    paths: list[Path],
    options: ScanOptions,
    on_root_scanned: Callable[[Path, int, int], None] | None = None,
) -> list[SafetyReport]:
    project_roots: dict[Path, ProjectType] = {}
    total = len(paths)
    for index, root in enumerate(paths, start=1):
        for candidate, project_type in discover_projects(root, options.excludes, options.depth).items():
            current_type = project_roots.get(candidate)
            if current_type is None or current_type == ProjectType.UNKNOWN:
                project_roots[candidate] = project_type
        if on_root_scanned is not None:
            on_root_scanned(root, index, total)
    return [check_safety(path, project_roots[path]) for path in sorted(project_roots)]


def check_safety(path: Path, project_type: ProjectType) -> SafetyReport:
    git_dir = path / ".git"
    is_git_repo = git_dir.exists()

    remote_name, remote_url, has_remote, is_clean = inspect_local_git_state(path, is_git_repo)

    current_branch = git_current_branch(path, is_git_repo)
    upstream_branch = git_upstream_branch(path, is_git_repo)
    unpushed_count, unpushed_commits = git_unpushed_commits(path, is_git_repo, upstream_branch)

    issues: list[str] = []
    if not is_git_repo:
        issues.append("不是 Git 仓库，无法 push 到远程")
    if is_git_repo and not has_remote:
        issues.append("没有远程仓库，无法 push")
    if is_git_repo and has_remote and upstream_branch is None and git_has_commits(path, is_git_repo):
        issues.append("当前分支没有追踪远程分支")
    if is_clean is False:
        issues.append("工作区有未提交的更改")
    if unpushed_count > 0:
        issues.append(f"有 {unpushed_count} 个 commit 未 push 到远程")

    if not is_git_repo:
        status = "danger"
    elif unpushed_count > 0:
        status = "danger"
    elif not has_remote:
        status = "danger"
    elif upstream_branch is None and git_has_commits(path, is_git_repo):
        status = "warning"
    elif is_clean is False:
        status = "warning"
    else:
        status = "ok"

    return SafetyReport(
        name=path.name,
        path=str(path),
        project_type=project_type,
        is_git_repo=is_git_repo,
        has_remote=has_remote,
        remote_url=remote_url,
        is_clean=is_clean,
        current_branch=current_branch,
        upstream_branch=upstream_branch,
        unpushed_commit_count=unpushed_count,
        unpushed_commits=unpushed_commits,
        status=status,
        issues=issues,
    )


def git_current_branch(path: Path, is_git_repo: bool) -> str | None:
    if not is_git_repo:
        return None
    result = run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if result.returncode == 0:
        return result.stdout.strip() or None
    return None


def git_upstream_branch(path: Path, is_git_repo: bool) -> str | None:
    if not is_git_repo:
        return None
    result = run_git(path, ["rev-parse", "--abbrev-ref", "@{u}"])
    if result.returncode == 0:
        return result.stdout.strip() or None
    return None


def git_unpushed_commits(path: Path, is_git_repo: bool, upstream_branch: str | None) -> tuple[int, list[str]]:
    if not is_git_repo or upstream_branch is None:
        return 0, []
    result = run_git(path, ["rev-list", f"{upstream_branch}..HEAD", "--oneline"])
    if result.returncode != 0:
        return 0, []
    lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
    return len(lines), lines[:5]


def run_git(path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )