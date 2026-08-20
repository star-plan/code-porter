"""Tests for clean discovery and the clean CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from rich.console import Console
from typer.main import get_command
from typer.testing import CliRunner

import code_porter.cli as cli_module
from code_porter.cleaner import (
    CleanTarget,
    apply_clean_targets,
    discover_clean_targets,
    normalize_profiles,
    profile_for_directory,
    profile_for_dirname,
    profile_label_names,
)
from code_porter.cli import app
from code_porter.scanner import default_scan_options

runner = CliRunner()


@pytest.fixture(autouse=True)
def plain_wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a wide, plain console so tables are stable in assertions."""
    monkeypatch.setattr(
        cli_module,
        "console",
        Console(force_terminal=False, no_color=True, width=200, highlight=False),
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
    """Create a parent directory and write a small file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_profile_for_dirname_maps_known_and_protected() -> None:
    """Directory basenames should map to the right profile; .git is protected."""
    assert profile_for_dirname("node_modules") == "deps"
    assert profile_for_dirname(".venv") == "deps"
    assert profile_for_dirname(".uv-cache") == "deps"
    assert profile_for_dirname("uv-cache") == "deps"
    assert profile_for_dirname("gomodcache") == "deps"
    assert profile_for_dirname(".next") == "cache"
    assert profile_for_dirname("gocache") == "cache"
    assert profile_for_dirname("dist") == "build"
    assert profile_for_dirname(".vs") == "cache"
    assert profile_for_dirname(".git") is None
    assert profile_for_dirname(".tmp") is None
    assert profile_for_dirname("sdists-v9") is None
    assert profile_for_dirname("src") is None
    # Ambiguous names must not match by basename alone.
    assert profile_for_dirname("bin") is None
    assert profile_for_dirname("obj") is None


def test_profile_for_directory_dotnet_neighbors() -> None:
    """bin/obj are build targets only when a .NET project file sits beside them."""
    assert profile_for_directory("bin", parent_files=["OutboxSmokeTest.csproj", "Program.cs"]) == "build"
    assert profile_for_directory("obj", parent_files=["Lib.fsproj"]) == "build"
    assert profile_for_directory("bin", parent_files=["App.vbproj"]) == "build"
    assert profile_for_directory("obj", parent_files=["StarBlog.sln"]) == "build"
    assert profile_for_directory("bin", parent_files=["StarBlog.slnx"]) == "build"
    assert profile_for_directory("bin", parent_files=["APP.CSPROJ"]) == "build"
    assert profile_for_directory("bin", parent_files=["run.sh", "README.md"]) is None
    assert profile_for_directory("bin") is None
    assert profile_for_directory("obj", parent_files=[]) is None
    assert profile_for_directory("src", parent_files=["App.csproj"]) is None
    assert profile_for_directory(".git", parent_files=["App.csproj"]) is None
    assert profile_for_directory("TestResults", parent_files=["App.csproj"]) == "build"
    assert profile_for_directory("TestResults", parent_files=["README.md"]) is None
    assert profile_for_directory("packages", parent_files=["App.sln"]) is None


def test_profile_label_names_mentions_dotnet_bin_obj() -> None:
    """Build profile UI labels should distinguish contextual .NET bin/obj."""
    labels = profile_label_names("build")
    assert "dist" in labels
    assert "bin (.NET)" in labels
    assert "obj (.NET)" in labels
    assert "TestResults (.NET)" in labels
    assert "bin (.NET)" not in profile_label_names("deps")
    assert "packages (NuGet)" in profile_label_names("deps")


def test_normalize_profiles_expands_all() -> None:
    """The all profile expands to deps/cache/build."""
    assert normalize_profiles(["all"]) == {"deps", "cache", "build"}
    assert normalize_profiles(["deps", "cache"]) == {"deps", "cache"}
    with pytest.raises(ValueError):
        normalize_profiles(["weird"])


def test_discover_clean_targets_finds_profiles_and_sizes(tmp_path: Path) -> None:
    """Discovery should classify junk dirs and estimate reclaimable size."""
    project = tmp_path / "app"
    project.mkdir()
    write_file(project / "package.json", "{}\n")
    write_file(project / "node_modules" / "pkg" / "index.js", "module.exports=1\n")
    write_file(project / ".venv" / "lib" / "x.py", "print(1)\n")
    write_file(project / ".next" / "cache" / "a", "cache\n")
    write_file(project / "dist" / "bundle.js", "console.log(1)\n")
    write_file(project / "src" / "main.js", "ok\n")

    plan = discover_clean_targets([tmp_path], default_scan_options())
    by_name = {item.name: item for item in plan.targets}

    assert set(by_name) >= {"node_modules", ".venv", ".next", "dist"}
    assert by_name["node_modules"].profile == "deps"
    assert by_name[".venv"].profile == "deps"
    assert by_name[".next"].profile == "cache"
    assert by_name["dist"].profile == "build"
    assert by_name["node_modules"].size_bytes > 0
    assert all(item.name != ".git" for item in plan.targets)


def test_discover_nested_project_local_tool_caches(tmp_path: Path) -> None:
    """Project-local Go/uv caches under a mixed .tmp dir should be clean targets.

    The parent .tmp directory itself must not be selected: it may hold real
    project files (spreadsheets, scripts) alongside regenerable caches.
    """
    project = tmp_path / "xingwei-app"
    project.mkdir()
    write_file(project / "go.mod", "module example.com/app\n")
    write_file(project / ".tmp" / "notes.xlsx", "sheet\n")
    write_file(project / ".tmp" / "update_quote_workbook.py", "print(1)\n")
    write_file(project / ".tmp" / "uv-cache" / "sdists-v9" / "pkg" / "a.tar.gz", "sdist\n")
    write_file(project / ".tmp" / "gocache" / "trim.txt", "go-build\n")
    write_file(project / ".tmp" / "gomodcache" / "mod" / "go.mod", "module x\n")

    plan = discover_clean_targets([tmp_path], default_scan_options())
    by_path = {item.path: item for item in plan.targets}

    uv_cache = project / ".tmp" / "uv-cache"
    gocache = project / ".tmp" / "gocache"
    gomodcache = project / ".tmp" / "gomodcache"
    assert str(uv_cache) in by_path
    assert str(gocache) in by_path
    assert str(gomodcache) in by_path
    assert by_path[str(uv_cache)].profile == "deps"
    assert by_path[str(gomodcache)].profile == "deps"
    assert by_path[str(gocache)].profile == "cache"
    assert all(item.name != ".tmp" for item in plan.targets)
    assert all(item.name != "sdists-v9" for item in plan.targets)


def test_apply_nested_tool_caches_keeps_mixed_tmp_files(tmp_path: Path) -> None:
    """Deleting nested tool caches must leave sibling files under .tmp intact."""
    project = tmp_path / "app"
    project.mkdir()
    write_file(project / "pyproject.toml", "[project]\nname='app'\n")
    xlsx = project / ".tmp" / "需求.xlsx"
    script = project / ".tmp" / "update_quote_workbook.py"
    uv_cache = project / ".tmp" / "uv-cache"
    write_file(xlsx, "sheet\n")
    write_file(script, "print(1)\n")
    write_file(uv_cache / "sdists-v9" / "pkg" / "a.tar.gz", "sdist\n")

    plan = discover_clean_targets([tmp_path], default_scan_options())
    deps_only = plan.filter_profiles(["deps"]).targets
    apply_clean_targets(deps_only)

    assert not uv_cache.exists()
    assert xlsx.exists()
    assert script.exists()
    assert (project / ".tmp").is_dir()
    assert all(item.status == "deleted" for item in deps_only)


def test_discover_skips_nested_project_roots(tmp_path: Path) -> None:
    """Parent projects should not claim junk belonging to nested project roots."""
    parent = tmp_path / "parent"
    child = parent / "child"
    parent.mkdir()
    child.mkdir()
    write_file(parent / "package.json", "{}\n")
    write_file(child / "package.json", "{}\n")
    init_git_repo(child)
    write_file(child / "node_modules" / "x" / "a.js", "1\n")
    write_file(parent / "node_modules" / "y" / "a.js", "1\n")

    plan = discover_clean_targets([tmp_path], default_scan_options())
    paths = {item.path for item in plan.targets}
    assert str(parent / "node_modules") in paths
    assert str(child / "node_modules") in paths
    # Nested target should be attributed to the child project, not the parent.
    child_target = next(item for item in plan.targets if item.path == str(child / "node_modules"))
    assert child_target.project_name == "child"


def test_discover_dotnet_bin_obj_next_to_csproj(tmp_path: Path) -> None:
    """bin/obj beside a .csproj are build targets; a scripts/bin folder is not.

    The git root has no .sln, so project type is unknown — neighbor files still count.
    """
    project = tmp_path / "starblog"
    project.mkdir()
    init_git_repo(project)
    csproj_dir = project / "demo" / "OutboxSmokeTest"
    write_file(csproj_dir / "OutboxSmokeTest.csproj", "<Project Sdk='Microsoft.NET.Sdk' />\n")
    write_file(csproj_dir / "Program.cs", "class P {}\n")
    write_file(csproj_dir / "bin" / "Debug" / "app.dll", "dll\n")
    write_file(csproj_dir / "obj" / "project.assets.json", "{}\n")
    write_file(project / "scripts" / "bin" / "run.sh", "echo hi\n")
    write_file(project / "src" / "obj" / "mesh.obj", "v 0 0 0\n")
    write_file(project / "README.md", "hi\n")

    plan = discover_clean_targets([tmp_path], default_scan_options())
    paths = {item.path for item in plan.targets}

    assert str(csproj_dir / "bin") in paths
    assert str(csproj_dir / "obj") in paths
    assert str(project / "scripts" / "bin") not in paths
    assert str(project / "src" / "obj") not in paths
    bin_target = next(item for item in plan.targets if item.path == str(csproj_dir / "bin"))
    obj_target = next(item for item in plan.targets if item.path == str(csproj_dir / "obj"))
    assert bin_target.profile == "build"
    assert obj_target.profile == "build"
    assert bin_target.project_name == "starblog"
    assert bin_target.size_bytes > 0


def test_discover_dotnet_bin_inside_python_repo(tmp_path: Path) -> None:
    """A nested .csproj bin/obj is cleaned even if the repo is typed python."""
    project = tmp_path / "mixed"
    project.mkdir()
    write_file(project / "pyproject.toml", "[project]\nname='mixed'\n")
    write_file(project / "src" / "main.py", "print(1)\n")
    nested = project / "demo" / "Tool"
    write_file(nested / "Tool.csproj", "<Project />\n")
    write_file(nested / "bin" / "Release" / "Tool.dll", "dll\n")
    write_file(nested / "obj" / "Tool.csproj.nuget.g.props", "<Project />\n")

    plan = discover_clean_targets([tmp_path], default_scan_options())
    paths = {item.path for item in plan.targets}
    assert str(nested / "bin") in paths
    assert str(nested / "obj") in paths


@pytest.mark.parametrize(
    "project_file",
    ["App.fsproj", "App.vbproj", "App.sln", "app.CSPROJ"],
)
def test_discover_dotnet_bin_next_to_other_project_files(tmp_path: Path, project_file: str) -> None:
    """fsproj, vbproj, sln, and case-variant csproj all count as neighbors."""
    project = tmp_path / "app"
    project.mkdir()
    write_file(project / "package.json", "{}\n")
    write_file(project / project_file, "placeholder\n")
    write_file(project / "bin" / "Debug" / "a.dll", "dll\n")

    plan = discover_clean_targets([tmp_path], default_scan_options())
    paths = {item.path for item in plan.targets}
    assert str(project / "bin") in paths


def test_apply_dotnet_bin_obj_keeps_sources(tmp_path: Path) -> None:
    """Applying the build profile should delete .NET bin/obj and keep sources."""
    project = tmp_path / "app"
    project.mkdir()
    # package.json makes this a discovered project without relying on .csproj markers.
    write_file(project / "package.json", "{}\n")
    write_file(project / "App.csproj", "<Project />\n")
    write_file(project / "Program.cs", "class P {}\n")
    bin_dir = project / "bin"
    obj_dir = project / "obj"
    write_file(bin_dir / "Debug" / "App.dll", "dll\n")
    write_file(obj_dir / "project.assets.json", "{}\n")

    plan = discover_clean_targets([tmp_path], default_scan_options())
    build_only = plan.filter_profiles(["build"]).targets
    apply_clean_targets(build_only)

    assert not bin_dir.exists()
    assert not obj_dir.exists()
    assert (project / "App.csproj").exists()
    assert (project / "Program.cs").exists()
    assert all(item.status == "deleted" for item in build_only)


def test_apply_skips_generic_bin_without_dotnet_neighbor(tmp_path: Path) -> None:
    """Apply must re-check the neighbor rule, not trust a forged basename-only target."""
    project = tmp_path / "scripts"
    project.mkdir()
    write_file(project / "package.json", "{}\n")
    bin_dir = project / "bin"
    write_file(bin_dir / "run.sh", "echo hi\n")

    forged = CleanTarget(
        project_name=project.name,
        project_path=str(project),
        path=str(bin_dir),
        name="bin",
        profile="build",
        size_bytes=1,
    )
    apply_clean_targets([forged])

    assert bin_dir.exists()
    assert (bin_dir / "run.sh").exists()
    assert forged.status == "skipped"
    assert "not in any clean profile" in forged.detail


def test_discover_standalone_csproj_without_git(tmp_path: Path) -> None:
    """A folder with only a .csproj (no git) should still be cleaned as a project."""
    project = tmp_path / "OutboxSmokeTest"
    project.mkdir()
    write_file(project / "OutboxSmokeTest.csproj", "<Project />\n")
    write_file(project / "Program.cs", "class P {}\n")
    write_file(project / "bin" / "Debug" / "app.dll", "dll\n")
    write_file(project / "obj" / "project.assets.json", "{}\n")

    plan = discover_clean_targets([tmp_path], default_scan_options())
    paths = {item.path for item in plan.targets}
    assert str(project / "bin") in paths
    assert str(project / "obj") in paths
    assert all(item.project_name == "OutboxSmokeTest" for item in plan.targets)


def test_discover_vs_testresults_and_nuget_packages(tmp_path: Path) -> None:
    """Visual Studio cache, VSTest results, and classic NuGet packages are junk."""
    project = tmp_path / "app"
    project.mkdir()
    write_file(project / "App.csproj", "<Project />\n")
    write_file(project / ".vs" / "App" / "v17" / ".suo", "vs\n")
    write_file(project / "TestResults" / "run.trx", "<TestRun />\n")
    write_file(project / "packages" / "repositories.config", "<repositories />\n")
    write_file(project / "packages" / "Newtonsoft.Json.12.0.3" / "lib" / "net45" / "n.dll", "dll\n")
    docs_packages = project / "docs" / "packages"
    write_file(docs_packages / "notes.txt", "not nuget\n")
    fake_results = project / "docs" / "TestResults"
    write_file(fake_results / "readme.md", "not trx\n")

    plan = discover_clean_targets([tmp_path], default_scan_options())
    by_path = {item.path: item for item in plan.targets}

    assert str(project / ".vs") in by_path
    assert by_path[str(project / ".vs")].profile == "cache"
    assert str(project / "TestResults") in by_path
    assert by_path[str(project / "TestResults")].profile == "build"
    assert str(project / "packages") in by_path
    assert by_path[str(project / "packages")].profile == "deps"
    assert str(docs_packages) not in by_path
    assert str(fake_results) not in by_path


def test_discover_testresults_by_nested_trx(tmp_path: Path) -> None:
    """TestResults at a git root without sln still matches when a nested .trx exists."""
    project = tmp_path / "starblog"
    project.mkdir()
    init_git_repo(project)
    write_file(project / "README.md", "hi\n")
    write_file(project / "TestResults" / "guid-folder" / "run.trx", "<TestRun />\n")

    plan = discover_clean_targets([tmp_path], default_scan_options())
    paths = {item.path for item in plan.targets}
    assert str(project / "TestResults") in paths


def test_apply_clean_targets_deletes_only_selected(tmp_path: Path) -> None:
    """apply_clean_targets should remove listed directories and keep others."""
    project = tmp_path / "app"
    project.mkdir()
    write_file(project / "pyproject.toml", "[project]\nname='app'\n")
    nm = project / "node_modules"
    dist = project / "dist"
    write_file(nm / "a.js", "1\n")
    write_file(dist / "out.js", "1\n")
    src = project / "src" / "main.py"
    write_file(src, "print(1)\n")

    plan = discover_clean_targets([tmp_path], default_scan_options())
    deps_only = plan.filter_profiles(["deps"]).targets
    apply_clean_targets(deps_only)

    assert not nm.exists()
    assert dist.exists()
    assert src.exists()
    assert all(item.status == "deleted" for item in deps_only)


def test_clean_cli_dry_run_does_not_delete(tmp_path: Path) -> None:
    """Default clean CLI is dry-run and must not delete anything."""
    project = tmp_path / "app"
    project.mkdir()
    write_file(project / "package.json", "{}\n")
    nm = project / "node_modules" / "x"
    write_file(nm / "a.js", "1\n")

    result = runner.invoke(
        app,
        ["clean", str(tmp_path), "--no-interactive", "--no-progress"],
    )

    assert result.exit_code == 0, result.output
    assert "Clean Candidates" in result.output
    assert "Dry-run only" in result.output
    assert (project / "node_modules").exists()


def test_clean_cli_apply_with_profile_and_yes(tmp_path: Path) -> None:
    """--apply -p deps -y should delete dependency directories only."""
    project = tmp_path / "app"
    project.mkdir()
    write_file(project / "package.json", "{}\n")
    write_file(project / "node_modules" / "x" / "a.js", "1\n")
    write_file(project / "dist" / "out.js", "1\n")

    result = runner.invoke(
        app,
        [
            "clean",
            str(tmp_path),
            "--profile",
            "deps",
            "--apply",
            "--yes",
            "--no-interactive",
            "--no-progress",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "deleted=" in result.output
    assert not (project / "node_modules").exists()
    assert (project / "dist").exists()


def test_clean_cli_apply_requires_profile_non_interactive(tmp_path: Path) -> None:
    """Non-interactive --apply without --profile must fail closed."""
    project = tmp_path / "app"
    project.mkdir()
    write_file(project / "package.json", "{}\n")
    write_file(project / "node_modules" / "x" / "a.js", "1\n")

    result = runner.invoke(
        app,
        ["clean", str(tmp_path), "--apply", "--yes", "--no-interactive", "--no-progress"],
    )

    assert result.exit_code == 2
    assert "without --profile" in result.output
    assert (project / "node_modules").exists()


def test_clean_cli_json_output(tmp_path: Path) -> None:
    """--json should emit a machine-readable clean plan."""
    project = tmp_path / "app"
    project.mkdir()
    write_file(project / "package.json", "{}\n")
    write_file(project / "node_modules" / "x" / "a.js", "1\n")

    result = runner.invoke(
        app,
        ["clean", str(tmp_path), "--json", "--profile", "deps", "--no-progress"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["target_count"] == 1
    assert payload["targets"][0]["name"] == "node_modules"
    assert payload["targets"][0]["profile"] == "deps"


def test_clean_cli_sort_size_largest_first(tmp_path: Path) -> None:
    """--sort size should list largest clean targets first."""
    project = tmp_path / "app"
    project.mkdir()
    write_file(project / "package.json", "{}\n")
    write_file(project / "node_modules" / "x" / "a.js", "big" * 5000 + "\n")
    write_file(project / ".next" / "cache" / "a", "tiny\n")

    result = runner.invoke(
        app,
        [
            "clean",
            str(tmp_path),
            "--json",
            "--profile",
            "all",
            "--sort",
            "size",
            "--no-progress",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    names = [item["name"] for item in payload["targets"]]
    assert names[0] == "node_modules"
    assert ".next" in names


def test_clean_cli_sort_profile_order(tmp_path: Path) -> None:
    """--sort profile should follow deps → cache → build."""
    project = tmp_path / "app"
    project.mkdir()
    write_file(project / "package.json", "{}\n")
    write_file(project / "dist" / "out.js", "1\n")
    write_file(project / ".next" / "a", "1\n")
    write_file(project / "node_modules" / "x" / "a.js", "1\n")

    result = runner.invoke(
        app,
        [
            "clean",
            str(tmp_path),
            "--json",
            "--profile",
            "all",
            "--sort",
            "profile",
            "--no-progress",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    profiles = [item["profile"] for item in payload["targets"]]
    assert profiles == sorted(profiles, key=lambda p: {"deps": 0, "cache": 1, "build": 2}[p])


def test_clean_default_order_is_profile_then_size(tmp_path: Path) -> None:
    """Discovery default order should be PROFILE_ORDER then size descending."""
    project = tmp_path / "app"
    project.mkdir()
    write_file(project / "package.json", "{}\n")
    write_file(project / "dist" / "out.js", "build-small\n")
    write_file(project / "node_modules" / "x" / "a.js", "deps-large" * 200 + "\n")
    write_file(project / ".next" / "a", "cache\n")

    plan = discover_clean_targets([tmp_path], default_scan_options())
    profiles = [item.profile for item in plan.targets]
    assert profiles == sorted(profiles, key=lambda p: {"deps": 0, "cache": 1, "build": 2}[p])
    deps = [item for item in plan.targets if item.profile == "deps"]
    assert deps[0].name == "node_modules"


def test_clean_help_encodes_on_legacy_windows() -> None:
    """clean option help must encode as cp1252 (Windows Rich legacy_windows_render)."""
    clean = get_command(app).commands["clean"]
    texts = [clean.help or ""]
    texts.extend(param.help or "" for param in clean.params)
    combined = "\n".join(texts)
    assert "deps -> cache -> build" in combined
    combined.encode("cp1252")


def test_clean_cli_sort_unknown_exits_with_error(tmp_path: Path) -> None:
    """Unknown --sort keys should fail fast for clean."""
    project = tmp_path / "app"
    project.mkdir()
    write_file(project / "package.json", "{}\n")
    write_file(project / "node_modules" / "x" / "a.js", "1\n")

    result = runner.invoke(
        app,
        ["clean", str(tmp_path), "--sort", "weird", "--no-interactive", "--no-progress"],
    )

    assert result.exit_code == 2
    assert "Unknown sort key" in result.output


def test_enable_escape_cancel_binds_escape_and_aborts() -> None:
    """_enable_escape_cancel should make ESC cancel confirm and checkbox prompts."""
    import questionary
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.output import DummyOutput

    from code_porter.cli import _enable_escape_cancel

    factories = (
        lambda pipe, out: questionary.confirm(
            "Proceed?", default=False, input=pipe, output=out
        ),
        lambda pipe, out: questionary.checkbox(
            "Pick", choices=["deps", "cache"], input=pipe, output=out
        ),
    )

    for factory in factories:
        with create_pipe_input() as pipe_input:
            question = _enable_escape_cancel(factory(pipe_input, DummyOutput()))
            esc_bindings = [
                binding
                for binding in question.application.key_bindings.get_bindings_for_keys(
                    (Keys.Escape,)
                )
                if binding.keys == (Keys.Escape,)
            ]
            assert esc_bindings, "ESC should be registered as a cancel binding"

            pipe_input.send_text("\x1b")  # Escape
            assert question.ask() is None, "ESC should cancel the prompt (return None)"
