"""Tests for project discovery, type inference, and scan excludes."""

from __future__ import annotations

import subprocess
from pathlib import Path

from code_porter.models import ProjectType
from code_porter.scanner import (
    default_scan_options,
    discover_projects,
    infer_project_type,
    scan_local_roots,
)


def init_git_repo(path: Path) -> None:
    """Initialize a local git repository on branch main with a test identity."""
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
        text=True,
    )


def write_file(path: Path, content: str = "x") -> None:
    """Create a parent directory and write a small text file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_infer_project_type_dotnet_from_csproj_and_sln() -> None:
    """csproj/fsproj/vbproj/sln should type as dotnet, including over package.json."""
    assert infer_project_type({"App.csproj", "Program.cs"}) == ProjectType.DOTNET
    assert infer_project_type({"Lib.fsproj"}) == ProjectType.DOTNET
    assert infer_project_type({"App.vbproj"}) == ProjectType.DOTNET
    assert infer_project_type({"App.sln"}) == ProjectType.DOTNET
    assert infer_project_type({"StarBlog.slnx"}) == ProjectType.DOTNET
    assert infer_project_type({"app.CSPROJ"}) == ProjectType.DOTNET
    assert infer_project_type({"App.csproj", "package.json"}) == ProjectType.DOTNET
    assert infer_project_type({"package.json"}) == ProjectType.NODE
    assert infer_project_type({"README.md"}) == ProjectType.UNKNOWN


def test_discover_standalone_csproj_without_git(tmp_path: Path) -> None:
    """A directory with only a csproj and no git should still be a project."""
    project = tmp_path / "OutboxSmokeTest"
    write_file(project / "OutboxSmokeTest.csproj", "<Project />\n")
    write_file(project / "Program.cs", "class P {}\n")

    found = discover_projects(tmp_path, default_scan_options().excludes)
    assert project in found
    assert found[project] == ProjectType.DOTNET


def test_discover_collapses_nested_csproj_to_sln_root(tmp_path: Path) -> None:
    """Without git, multiple csproj dirs under one sln should be one project."""
    solution = tmp_path / "solution"
    write_file(solution / "App.sln", "\n")
    write_file(solution / "src" / "A" / "A.csproj", "<Project />\n")
    write_file(solution / "src" / "B" / "B.csproj", "<Project />\n")
    write_file(solution / "src" / "A" / "bin" / "Debug" / "A.dll", "dll\n")

    found = discover_projects(tmp_path, default_scan_options().excludes)
    assert found == {solution: ProjectType.DOTNET}


def test_discover_git_nested_csproj_types_dotnet(tmp_path: Path) -> None:
    """A git root without sln should still type as dotnet from a nested csproj."""
    project = tmp_path / "starblog"
    project.mkdir()
    init_git_repo(project)
    write_file(project / "README.md", "hi\n")
    write_file(project / "demo" / "OutboxSmokeTest" / "OutboxSmokeTest.csproj", "<Project />\n")

    found = discover_projects(tmp_path, default_scan_options().excludes)
    assert found == {project: ProjectType.DOTNET}


def test_discover_slnx_collapses_nested_csproj(tmp_path: Path) -> None:
    """XML solution files (.slnx) should behave like .sln for root collapse."""
    solution = tmp_path / "starblog"
    write_file(solution / "StarBlog.slnx", "<Solution />\n")
    write_file(solution / "demo" / "Tool" / "Tool.csproj", "<Project />\n")
    write_file(solution / "apps" / "admin" / "package.json", "{}\n")

    found = discover_projects(tmp_path, default_scan_options().excludes)
    assert found[solution] == ProjectType.DOTNET
    assert solution in found


def test_discover_nested_csproj_upgrades_git_root_over_package_json(tmp_path: Path) -> None:
    """A nested csproj should type the git root as dotnet even if package.json is seen first."""
    project = tmp_path / "starblog"
    project.mkdir()
    init_git_repo(project)
    write_file(project / "apps" / "admin" / "package.json", "{}\n")
    write_file(project / "demo" / "Tool" / "Tool.csproj", "<Project />\n")

    found = discover_projects(tmp_path, default_scan_options().excludes)
    assert found == {project: ProjectType.DOTNET}


def test_scan_omits_dotnet_bin_from_size_keeps_script_bin(tmp_path: Path) -> None:
    """Scan should ignore csproj-adjacent bin/obj but still count a scripts/bin."""
    project = tmp_path / "app"
    write_file(project / "App.csproj", "<Project />\n")
    write_file(project / "Program.cs", "class P {}\n")
    (project / "bin" / "Debug").mkdir(parents=True)
    (project / "bin" / "Debug" / "App.dll").write_bytes(b"d" * 50_000)
    write_file(project / "scripts" / "bin" / "run.sh", "echo hi\n")
    write_file(project / "obj" / "project.assets.json", "{}\n")

    reports = scan_local_roots([tmp_path], default_scan_options())
    assert len(reports) == 1
    report = reports[0]
    assert report.project_type == ProjectType.DOTNET
    assert "bin" in report.ignored_directories_present
    assert "obj" in report.ignored_directories_present
    assert report.size_bytes < 10_000
    assert report.size_bytes > 0
