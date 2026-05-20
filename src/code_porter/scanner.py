from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import MigrationStrategy, ProjectReport, ProjectType

MARKERS: dict[str, ProjectType] = {
    "package.json": ProjectType.NODE,
    "pyproject.toml": ProjectType.PYTHON,
    "go.mod": ProjectType.GO,
    "Cargo.toml": ProjectType.RUST,
}
DEFAULT_EXCLUDES = {
    ".git",
    ".cache",
    ".next",
    ".venv",
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
            project_roots.setdefault(candidate, project_type)
    return [inspect_local_project(path, project_roots[path], options) for path in sorted(project_roots)]


def discover_projects(root: Path, excludes: set[str]) -> dict[Path, ProjectType]:
    project_roots: dict[Path, ProjectType] = {}
    if not root.exists():
        raise FileNotFoundError(f"Scan root does not exist: {root}")

    for current_path, dir_names, file_names in root.walk(top_down=True):
        dir_names[:] = [name for name in dir_names if name not in excludes]
        file_set = set(file_names)

        if any(name.endswith(".sln") for name in file_set):
            project_roots[current_path] = ProjectType.DOTNET

        for marker, project_type in MARKERS.items():
            if marker in file_set:
                project_roots.setdefault(current_path, project_type)

    return project_roots


def inspect_local_project(path: Path, project_type: ProjectType, options: ScanOptions) -> ProjectReport:
    git_dir = path / ".git"
    is_git_repo = git_dir.exists()
    remote_name, remote_url, has_remote, is_clean = inspect_local_git_state(path, is_git_repo)

    size_bytes, large_directories, ignored_present = summarize_directory(path, options)
    strategy, reason = choose_strategy(is_git_repo, has_remote, is_clean)
    worth_migrating, worth_reason = assess_migration_value(strategy, is_git_repo, has_remote, size_bytes)

    return ProjectReport(
        name=path.name,
        path=str(path),
        project_type=project_type,
        is_git_repo=is_git_repo,
        has_remote=has_remote,
        remote_name=remote_name,
        remote_url=remote_url,
        is_clean=is_clean,
        size_bytes=size_bytes,
        large_directories=large_directories,
        ignored_directories_present=ignored_present,
        migration_strategy=strategy,
        migration_reason=reason,
        worth_migrating=worth_migrating,
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


def choose_strategy(is_git_repo: bool, has_remote: bool, is_clean: bool | None) -> tuple[MigrationStrategy, str]:
    if is_git_repo and has_remote and is_clean:
        return MigrationStrategy.CLONE, "Git 仓库干净且存在 remote，优先 clone"
    if is_git_repo and not has_remote and is_clean:
        return MigrationStrategy.BUNDLE, "本地 Git 仓库干净但没有 remote，适合 git bundle"
    if is_git_repo and is_clean is False:
        return MigrationStrategy.RSYNC, "Git 仓库存在未提交变更，按非 Git 项目复制源码"
    if not is_git_repo:
        return MigrationStrategy.RSYNC, "非 Git 项目，使用 rsync/scp 复制源码"
    return MigrationStrategy.SKIP, "无法确认仓库状态，先跳过人工确认"


def assess_migration_value(
    strategy: MigrationStrategy,
    is_git_repo: bool,
    has_remote: bool,
    size_bytes: int,
) -> tuple[bool, str]:
    if strategy == MigrationStrategy.SKIP:
        return False, "仓库状态无法自动确认，需要人工检查"
    if size_bytes <= 0:
        return False, "目录为空，不建议迁移"
    if has_remote:
        return True, "存在 Git remote，可低成本恢复"
    if is_git_repo:
        return True, "本地 Git 历史可通过 bundle 保留"
    return True, "检测到可识别项目文件且存在源码内容"


def run_git(path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def build_remote_scan_command(host: str, roots: list[str], options: ScanOptions) -> list[str]:
    marker_names = [*MARKERS.keys(), "*.sln"]
    excludes = sorted(options.excludes)
    threshold_bytes = options.large_dir_threshold_mb * 1024 * 1024
    powershell_script = f"""
$ErrorActionPreference = 'Stop'
$roots = ConvertFrom-Json @'{json.dumps(roots)}'@
$excludes = ConvertFrom-Json @'{json.dumps(excludes)}'@
$markers = ConvertFrom-Json @'{json.dumps(marker_names)}'@
$thresholdBytes = {threshold_bytes}

function Get-ProjectType($fileName) {{
    switch ($fileName) {{
        'package.json' {{ return 'node' }}
        'pyproject.toml' {{ return 'python' }}
        'go.mod' {{ return 'go' }}
        'Cargo.toml' {{ return 'rust' }}
        default {{
            if ($fileName -like '*.sln') {{ return 'dotnet' }}
            return 'unknown'
        }}
    }}
}}

function Get-DirectorySize($path, $excludeSet) {{
    $sum = 0
    Get-ChildItem -LiteralPath $path -Force | ForEach-Object {{
        if ($_.PSIsContainer) {{
            if ($excludeSet -contains $_.Name) {{ return }}
            $sum += Get-DirectorySize $_.FullName $excludeSet
        }} else {{
            $sum += $_.Length
        }}
    }}
    return $sum
}}

function Get-LargeDirectories($path, $excludeSet, $threshold) {{
    $items = New-Object System.Collections.Generic.List[string]
    Get-ChildItem -LiteralPath $path -Force -Directory -ErrorAction SilentlyContinue | ForEach-Object {{
        if ($excludeSet -contains $_.Name) {{ return }}
        $dirSize = Get-DirectorySize $_.FullName $excludeSet
        if ($dirSize -ge $threshold) {{ $items.Add($_.Name) | Out-Null }}
    }}
    return @($items)
}}

function Get-IgnoredDirectoriesPresent($path, $excludeSet) {{
    $found = New-Object 'System.Collections.Generic.HashSet[string]'
    Get-ChildItem -LiteralPath $path -Recurse -Force -Directory -ErrorAction SilentlyContinue | ForEach-Object {{
        if ($excludeSet -contains $_.Name) {{ $found.Add($_.Name) | Out-Null }}
    }}
    return @($found.ToArray() | Sort-Object)
}}

$reports = New-Object System.Collections.Generic.List[Object]

foreach ($root in $roots) {{
    if (-not (Test-Path -LiteralPath $root)) {{ continue }}
    $files = Get-ChildItem -LiteralPath $root -Recurse -Force -File | Where-Object {{
        $markers -contains $_.Name -or $_.Name -like '*.sln'
    }}
    foreach ($file in $files) {{
        $segments = $file.DirectoryName -split '[\\/]'
        if (($segments | Where-Object {{ $excludes -contains $_ }}).Count -gt 0) {{ continue }}

        $projectPath = $file.DirectoryName
        $gitPath = Join-Path $projectPath '.git'
        $isGit = Test-Path -LiteralPath $gitPath
        $hasRemote = $false
        $remoteName = $null
        $remoteUrl = $null
        $isClean = $null

        if ($isGit) {{
            $remoteOutput = git -C $projectPath remote 2>$null
            if ($LASTEXITCODE -eq 0 -and $remoteOutput) {{
                $hasRemote = $true
                $remoteName = ($remoteOutput | Select-Object -First 1)
                $remoteUrl = git -C $projectPath remote get-url $remoteName 2>$null
                if ($LASTEXITCODE -ne 0) {{ $remoteUrl = $null }}
            }}
            $statusOutput = git -C $projectPath status --porcelain 2>$null
            if ($LASTEXITCODE -eq 0) {{ $isClean = [string]::IsNullOrWhiteSpace(($statusOutput | Out-String)) }}
        }}

        $sizeBytes = Get-DirectorySize $projectPath $excludes
        $largeDirectories = Get-LargeDirectories $projectPath $excludes $thresholdBytes
        $ignoredPresent = Get-IgnoredDirectoriesPresent $projectPath $excludes
        $reports.Add([pscustomobject]@{{
            name = Split-Path $projectPath -Leaf
            path = $projectPath
            project_type = Get-ProjectType $file.Name
            is_git_repo = $isGit
            has_remote = $hasRemote
            remote_name = $remoteName
            remote_url = $remoteUrl
            is_clean = $isClean
            size_bytes = $sizeBytes
            large_directories = $largeDirectories
            ignored_directories_present = $ignoredPresent
        }}) | Out-Null
    }}
}}

$reports | ConvertTo-Json -Depth 4
""".strip()
    return ["ssh", host, "powershell", "-NoProfile", "-Command", powershell_script]


def scan_remote_host(host: str, roots: list[str], options: ScanOptions) -> list[ProjectReport]:
    result = subprocess.run(
        build_remote_scan_command(host, roots, options),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Remote scan failed")

    payload = result.stdout.strip()
    if not payload:
        return []

    data = json.loads(payload)
    if isinstance(data, dict):
        data = [data]

    reports: list[ProjectReport] = []
    for item in data:
        strategy, reason = choose_strategy(item["is_git_repo"], item["has_remote"], item.get("is_clean"))
        worth_migrating, worth_reason = assess_migration_value(
            strategy,
            item["is_git_repo"],
            item["has_remote"],
            item.get("size_bytes", 0),
        )
        reports.append(
            ProjectReport(
                name=item["name"],
                path=item["path"],
                project_type=ProjectType(item["project_type"]),
                is_git_repo=item["is_git_repo"],
                has_remote=item["has_remote"],
                remote_name=item.get("remote_name"),
                remote_url=item.get("remote_url"),
                is_clean=item.get("is_clean"),
                size_bytes=item.get("size_bytes", 0),
                large_directories=item.get("large_directories", []),
                ignored_directories_present=item.get("ignored_directories_present", []),
                migration_strategy=strategy,
                migration_reason=reason,
                worth_migrating=worth_migrating,
                worth_reason=worth_reason,
            )
        )

    unique_reports: dict[str, ProjectReport] = {}
    for report in reports:
        unique_reports.setdefault(report.path, report)
    return sorted(unique_reports.values(), key=lambda report: report.path.lower())