"""Tests for clean discovery and the clean CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

import code_porter.cli as cli_module
from code_porter.cleaner import (
    apply_clean_targets,
    discover_clean_targets,
    normalize_profiles,
    profile_for_dirname,
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
    assert profile_for_dirname(".git") is None
    assert profile_for_dirname(".tmp") is None
    assert profile_for_dirname("sdists-v9") is None
    assert profile_for_dirname("src") is None


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
