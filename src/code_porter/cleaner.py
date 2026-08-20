"""Discover and remove regenerable junk directories from local projects."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .scanner import ScanOptions, discover_projects

# Never delete these, even if a profile somehow listed them.
PROTECTED_DIR_NAMES = {".git"}

# Unambiguous junk dir names grouped by rebuild risk.
# dry-run previews all profiles; apply only deletes selected ones.
# Matching is basename-only: nested caches (e.g. .tmp/uv-cache) are cleaned
# without deleting a mixed parent like .tmp that may hold real project files.
CLEAN_PROFILES: dict[str, frozenset[str]] = {
    "deps": frozenset(
        {
            "node_modules",
            ".venv",
            "venv",
            ".uv-cache",
            "uv-cache",  # project-local UV_CACHE_DIR (often under .tmp/)
            ".pnpm-store",
            "gomodcache",  # project-local GOMODCACHE
            ".gomodcache",
        }
    ),
    "cache": frozenset(
        {
            ".cache",
            ".next",
            ".turbo",
            ".parcel-cache",
            ".nuxt",
            ".output",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".tox",
            ".nox",
            ".eggs",
            "htmlcov",
            ".ipynb_checkpoints",
            "gocache",  # project-local GOCACHE
            ".gocache",
        }
    ),
    "build": frozenset(
        {
            "dist",
            "build",
            "target",
            "out",
            "coverage",
        }
    ),
}

PROFILE_ORDER = ("deps", "cache", "build")
PROFILE_CHOICES = (*PROFILE_ORDER, "all")

# Ambiguous names: only junk when the parent directory looks like a .NET project.
# Do not put these in CLEAN_PROFILES — a scripts/bin folder is often source.
CONTEXTUAL_BUILD_DIR_NAMES = frozenset({"bin", "obj"})
DOTNET_PROJECT_SUFFIXES = (".csproj", ".fsproj", ".vbproj", ".sln")


@dataclass(slots=True)
class CleanTarget:
    """One junk directory discovered under a project root."""

    project_name: str
    project_path: str
    path: str
    name: str
    profile: str
    size_bytes: int
    status: str = "pending"  # pending | deleted | failed | skipped
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialize this target for JSON output."""
        data = asdict(self)
        data["size_human"] = format_size(self.size_bytes)
        return data

    @property
    def size_human(self) -> str:
        """Human-readable size for terminal tables."""
        return format_size(self.size_bytes)


@dataclass(slots=True)
class CleanPlan:
    """Full clean discovery result across one or more roots."""

    projects: list[str] = field(default_factory=list)
    targets: list[CleanTarget] = field(default_factory=list)

    def filter_profiles(self, profiles: Iterable[str]) -> "CleanPlan":
        """Return a new plan containing only targets in the given profiles."""
        wanted = normalize_profiles(profiles)
        return CleanPlan(
            projects=list(self.projects),
            targets=[item for item in self.targets if item.profile in wanted],
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize plan for JSON output."""
        return {
            "projects": list(self.projects),
            "targets": [item.to_dict() for item in self.targets],
            "summary": summarize_targets(self.targets),
        }


def format_size(size_bytes: int) -> str:
    """Format a byte count as a short human-readable string."""
    size = float(size_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)}{unit}"
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def normalize_profiles(profiles: Iterable[str]) -> set[str]:
    """Normalize profile names; expand ``all`` into deps/cache/build."""
    result: set[str] = set()
    for raw in profiles:
        name = raw.strip().lower()
        if not name:
            continue
        if name == "all":
            result.update(PROFILE_ORDER)
            continue
        if name not in CLEAN_PROFILES:
            raise ValueError(f"Unknown clean profile: {raw}")
        result.add(name)
    return result


def is_dotnet_project_filename(name: str) -> bool:
    """Return True if the filename is a .NET project or solution file."""
    lowered = name.lower()
    return lowered.endswith(DOTNET_PROJECT_SUFFIXES)


def has_dotnet_project_file(file_names: Iterable[str]) -> bool:
    """Return True if any name in the listing is a .NET project or solution file."""
    return any(is_dotnet_project_filename(name) for name in file_names)


def list_parent_filenames(directory: Path) -> list[str]:
    """List file names in a directory; return empty on permission or IO errors."""
    try:
        return [entry.name for entry in directory.iterdir() if entry.is_file()]
    except OSError:
        return []


def profile_for_dirname(name: str) -> str | None:
    """Map an unambiguous directory basename to its clean profile, if any.

    Does not apply contextual rules: ``bin`` / ``obj`` always return None here.
    """
    if name in PROTECTED_DIR_NAMES:
        return None
    for profile in PROFILE_ORDER:
        if name in CLEAN_PROFILES[profile]:
            return profile
    return None


def profile_for_directory(name: str, parent_files: Iterable[str] | None = None) -> str | None:
    """Resolve a directory's clean profile using basename, then contextual rules.

    Unambiguous names (node_modules, dist, ...) match by basename only.
    ``bin`` / ``obj`` match the build profile only when the parent directory
    contains a .NET project file (.csproj / .fsproj / .vbproj / .sln).
    """
    if name in PROTECTED_DIR_NAMES:
        return None
    profile = profile_for_dirname(name)
    if profile is not None:
        return profile
    if name in CONTEXTUAL_BUILD_DIR_NAMES and parent_files is not None:
        if has_dotnet_project_file(parent_files):
            return "build"
    return None


def profile_for_path(path: Path) -> str | None:
    """Resolve a live directory path to a clean profile, or None if it is not junk.

    Used at apply time so deletion uses the same neighbor-file rule as discovery.
    """
    return profile_for_directory(path.name, parent_files=list_parent_filenames(path.parent))


def profile_label_names(profile: str) -> list[str]:
    """Directory names shown in UI for a profile, including contextual .NET dirs."""
    names = sorted(CLEAN_PROFILES[profile])
    if profile == "build":
        names.extend(["bin (.NET)", "obj (.NET)"])
    return names


def directory_size(path: Path) -> int:
    """Sum file sizes under a directory; ignore unreadable entries."""
    total = 0
    try:
        for current_path, _dir_names, file_names in path.walk(top_down=True):
            for file_name in file_names:
                file_path = current_path / file_name
                try:
                    total += file_path.stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def list_project_roots(roots: list[Path], options: ScanOptions) -> list[tuple[Path, str]]:
    """Discover project roots without running full export inspection."""
    project_roots: dict[Path, str] = {}
    for root in roots:
        for candidate, project_type in discover_projects(root, options.excludes, options.depth).items():
            current = project_roots.get(candidate)
            if current is None or current == "unknown":
                project_roots[candidate] = project_type.value
    return sorted(project_roots.items(), key=lambda item: str(item[0]).lower())


def discover_clean_targets(
    roots: list[Path],
    options: ScanOptions,
    *,
    on_project_scanned: Callable[[Path, int, int], None] | None = None,
) -> CleanPlan:
    """Walk project trees and collect regenerable junk directories for all profiles."""
    projects = list_project_roots(roots, options)
    all_roots = {path for path, _ in projects}
    targets: list[CleanTarget] = []
    total = len(projects)

    for index, (project_path, _project_type) in enumerate(projects, start=1):
        targets.extend(_discover_targets_in_project(project_path, all_roots))
        if on_project_scanned is not None:
            on_project_scanned(project_path, index, total)

    # PROFILE_ORDER first (deps → cache → build), then largest reclaimable, then path.
    profile_rank = {name: index for index, name in enumerate(PROFILE_ORDER)}
    targets.sort(
        key=lambda item: (
            profile_rank.get(item.profile, 99),
            -item.size_bytes,
            item.path.lower(),
        )
    )
    return CleanPlan(
        projects=[path.name for path, _ in projects],
        targets=targets,
    )


def _discover_targets_in_project(project_path: Path, all_project_roots: set[Path]) -> list[CleanTarget]:
    """Find clean targets under one project, skipping nested project roots and .git."""
    found: list[CleanTarget] = []
    try:
        walker = project_path.walk(top_down=True)
    except OSError:
        return found

    for current_path, dir_names, file_names in walker:
        # Nested projects are cleaned via their own root, not the parent walk.
        if current_path != project_path and current_path in all_project_roots:
            dir_names.clear()
            continue

        # Never enter .git.
        dir_names[:] = [name for name in dir_names if name not in PROTECTED_DIR_NAMES]

        matched_names: list[str] = []
        parent_files = file_names
        for name in list(dir_names):
            profile = profile_for_directory(name, parent_files=parent_files)
            if profile is None:
                continue
            matched_names.append(name)
            target_path = current_path / name
            size_bytes = directory_size(target_path)
            found.append(
                CleanTarget(
                    project_name=project_path.name,
                    project_path=str(project_path),
                    path=str(target_path),
                    name=name,
                    profile=profile,
                    size_bytes=size_bytes,
                )
            )

        # Do not descend into directories we plan to delete.
        if matched_names:
            remove = set(matched_names)
            dir_names[:] = [name for name in dir_names if name not in remove]

    return found


def summarize_targets(targets: list[CleanTarget]) -> dict[str, object]:
    """Build aggregate counts and sizes for a target list."""
    by_profile: dict[str, dict[str, int]] = {
        profile: {"count": 0, "bytes": 0} for profile in PROFILE_ORDER
    }
    status_counts: dict[str, int] = {}
    total_bytes = 0
    for item in targets:
        bucket = by_profile.setdefault(item.profile, {"count": 0, "bytes": 0})
        bucket["count"] += 1
        bucket["bytes"] += item.size_bytes
        total_bytes += item.size_bytes
        status_counts[item.status] = status_counts.get(item.status, 0) + 1
    return {
        "target_count": len(targets),
        "total_bytes": total_bytes,
        "total_human": format_size(total_bytes),
        "by_profile": {
            profile: {
                "count": data["count"],
                "bytes": data["bytes"],
                "size_human": format_size(data["bytes"]),
            }
            for profile, data in by_profile.items()
            if data["count"] > 0 or profile in PROFILE_ORDER
        },
        "by_status": status_counts,
    }


def apply_clean_targets(
    targets: list[CleanTarget],
    *,
    on_target_processed: Callable[[CleanTarget, int, int], None] | None = None,
) -> list[CleanTarget]:
    """Delete target directories in place and update each target's status."""
    total = len(targets)
    for index, target in enumerate(targets, start=1):
        path = Path(target.path)
        if not path.exists():
            target.status = "skipped"
            target.detail = "path does not exist"
        elif not path.is_dir():
            target.status = "skipped"
            target.detail = "not a directory"
        elif path.name in PROTECTED_DIR_NAMES:
            target.status = "skipped"
            target.detail = "protected directory"
        elif profile_for_path(path) is None:
            target.status = "skipped"
            target.detail = "directory is not in any clean profile"
        else:
            try:
                shutil.rmtree(path)
                target.status = "deleted"
                target.detail = f"removed {target.size_human}"
            except OSError as error:
                target.status = "failed"
                target.detail = str(error) or error.__class__.__name__

        if on_target_processed is not None:
            on_target_processed(target, index, total)
    return targets
