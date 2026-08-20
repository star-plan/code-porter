"""Shared regenerable-directory matching for scan, clean, and zip export."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

# Project/solution files that identify a .NET tree.
DOTNET_PROJECT_SUFFIXES = (".csproj", ".fsproj", ".vbproj", ".sln", ".slnx")
DOTNET_SOLUTION_SUFFIXES = (".sln", ".slnx")

# Ambiguous build outputs: only junk when a .NET project file sits beside them.
DOTNET_BUILD_DIR_NAMES = frozenset({"bin", "obj"})

# VSTest output directory. Distinctive name, but still gated (see contextual rule).
VSTEST_RESULTS_DIR_NAME = "TestResults"

# Classic NuGet packages folder; must contain repositories.config to qualify.
NUGET_PACKAGES_DIR_NAME = "packages"
NUGET_PACKAGES_MARKER = "repositories.config"


def is_dotnet_project_filename(name: str) -> bool:
    """Return True if the filename is a .NET project or solution file."""
    return name.lower().endswith(DOTNET_PROJECT_SUFFIXES)


def is_dotnet_solution_filename(name: str) -> bool:
    """Return True if the filename is a Visual Studio / .NET solution file."""
    return name.lower().endswith(DOTNET_SOLUTION_SUFFIXES)


def has_dotnet_project_file(file_names: Iterable[str]) -> bool:
    """Return True if any name in the listing is a .NET project or solution file."""
    return any(is_dotnet_project_filename(name) for name in file_names)


def directory_has_solution_file(path: Path) -> bool:
    """Return True if the directory contains a .sln file."""
    try:
        return any(entry.is_file() and is_dotnet_solution_filename(entry.name) for entry in path.iterdir())
    except OSError:
        return False


def _dir_contains_suffix(path: Path, suffix: str, *, extra_depth: int = 1) -> bool:
    """Return True if a file with the given suffix exists at path or one child down."""
    lowered = suffix.lower()
    try:
        entries = list(path.iterdir())
    except OSError:
        return False
    for entry in entries:
        if entry.is_file() and entry.name.lower().endswith(lowered):
            return True
    if extra_depth <= 0:
        return False
    for entry in entries:
        if not entry.is_dir():
            continue
        try:
            for child in entry.iterdir():
                if child.is_file() and child.name.lower().endswith(lowered):
                    return True
        except OSError:
            continue
    return False


def _file_exists_ci(directory: Path, name: str) -> bool:
    """Return True if a file with this name exists, ignoring case."""
    target = name.lower()
    try:
        return any(entry.is_file() and entry.name.lower() == target for entry in directory.iterdir())
    except OSError:
        return False


def contextual_junk_profile(
    name: str,
    parent_files: Iterable[str],
    directory: Path | None = None,
) -> str | None:
    """Return a clean profile for an ambiguous directory, or None.

    Rules:

    - ``bin`` / ``obj``: parent has a .NET project or solution file → build
    - ``TestResults``: parent has a .NET project file, or the directory (or one
      child folder) contains a ``.trx`` file → build
    - ``packages``: directory contains ``repositories.config`` → deps
    """
    if name in DOTNET_BUILD_DIR_NAMES and has_dotnet_project_file(parent_files):
        return "build"
    if name == VSTEST_RESULTS_DIR_NAME:
        if has_dotnet_project_file(parent_files):
            return "build"
        if directory is not None and _dir_contains_suffix(directory, ".trx"):
            return "build"
        return None
    if name == NUGET_PACKAGES_DIR_NAME and directory is not None:
        if _file_exists_ci(directory, NUGET_PACKAGES_MARKER):
            return "deps"
    return None


def should_skip_directory(
    name: str,
    parent_files: Iterable[str],
    directory: Path,
    basename_excludes: set[str],
) -> bool:
    """Return True if a walk should not descend into this directory.

    Basename excludes (node_modules, dist, .vs, ...) match unconditionally.
    Ambiguous names use ``contextual_junk_profile``.
    """
    if name in basename_excludes:
        return True
    return contextual_junk_profile(name, parent_files, directory) is not None
