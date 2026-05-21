import json
import subprocess
import zipfile
from pathlib import Path

from code_porter.archive import export_projects, import_packages, load_manifest
from code_porter.models import ArchiveKind, PackagingStrategy, ProjectType
from code_porter.scanner import default_scan_options, scan_local_roots


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test User"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True, capture_output=True, text=True)


def test_scan_local_coalesces_git_root_and_ignores_nested_markers(tmp_path: Path) -> None:
    project_dir = tmp_path / "mono-app"
    project_dir.mkdir()
    init_git_repo(project_dir)
    (project_dir / "pyproject.toml").write_text("[project]\nname='mono-app'\n", encoding="utf-8")
    nested = project_dir / "src" / "nested"
    nested.mkdir(parents=True)
    (nested / "pyproject.toml").write_text("[project]\nname='nested'\n", encoding="utf-8")
    venv_pkg = project_dir / ".venv" / "lib"
    venv_pkg.mkdir(parents=True)
    (venv_pkg / "package.json").write_text("{}\n", encoding="utf-8")

    reports = scan_local_roots([tmp_path], default_scan_options())

    assert len(reports) == 1
    assert reports[0].name == "mono-app"
    assert reports[0].project_type == ProjectType.PYTHON


def test_export_creates_bundle_for_clean_git_repo(tmp_path: Path) -> None:
    project_dir = tmp_path / "git-app"
    project_dir.mkdir()
    init_git_repo(project_dir)
    (project_dir / "pyproject.toml").write_text("[project]\nname='git-app'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project_dir), "add", "pyproject.toml"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(project_dir), "commit", "-m", "init"], check=True, capture_output=True, text=True)

    reports = scan_local_roots([tmp_path], default_scan_options())
    manifest = export_projects(reports, tmp_path / "exported", [tmp_path])

    assert len(manifest.packages) == 1
    package = manifest.packages[0]
    assert package.package_kind == ArchiveKind.BUNDLE
    assert package.packaging_strategy == PackagingStrategy.BUNDLE
    assert (tmp_path / "exported" / package.package_path).exists()


def test_export_creates_overlay_for_dirty_git_repo(tmp_path: Path) -> None:
    project_dir = tmp_path / "dirty-app"
    project_dir.mkdir()
    init_git_repo(project_dir)
    source_file = project_dir / "app.py"
    source_file.write_text("print('v1')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project_dir), "add", "app.py"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(project_dir), "commit", "-m", "init"], check=True, capture_output=True, text=True)
    source_file.write_text("print('v2')\n", encoding="utf-8")

    reports = scan_local_roots([tmp_path], default_scan_options())
    manifest = export_projects(reports, tmp_path / "exported", [tmp_path])

    package = manifest.packages[0]
    assert package.packaging_strategy == PackagingStrategy.BUNDLE_WITH_OVERLAY
    assert package.overlay_path is not None
    assert (tmp_path / "exported" / package.overlay_path).exists()


def test_import_restores_dirty_worktree_overlay(tmp_path: Path) -> None:
    project_dir = tmp_path / "dirty-app"
    project_dir.mkdir()
    init_git_repo(project_dir)
    source_file = project_dir / "app.py"
    source_file.write_text("print('v1')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project_dir), "add", "app.py"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(project_dir), "commit", "-m", "init"], check=True, capture_output=True, text=True)
    source_file.write_text("print('v2')\n", encoding="utf-8")
    new_file = project_dir / "notes.txt"
    new_file.write_text("dirty worktree\n", encoding="utf-8")

    reports = scan_local_roots([tmp_path], default_scan_options())
    export_dir = tmp_path / "exported"
    manifest = export_projects(reports, export_dir, [tmp_path])

    package = manifest.packages[0]
    assert package.overlay_path is not None

    results = import_packages(export_dir / "manifest.json", tmp_path / "imported")
    assert results[0].status == "imported"

    imported_project = tmp_path / "imported" / "dirty-app"
    assert (imported_project / "app.py").read_text(encoding="utf-8") == "print('v2')\n"
    assert (imported_project / "notes.txt").read_text(encoding="utf-8") == "dirty worktree\n"


def test_export_falls_back_to_zip_for_git_repo_without_commits(tmp_path: Path) -> None:
    project_dir = tmp_path / "scratch-repo"
    project_dir.mkdir()
    init_git_repo(project_dir)
    (project_dir / "app.py").write_text("print('hello')\n", encoding="utf-8")

    reports = scan_local_roots([tmp_path], default_scan_options())

    assert len(reports) == 1
    assert reports[0].packaging_strategy == PackagingStrategy.ZIP

    manifest = export_projects(reports, tmp_path / "exported", [tmp_path])
    package = manifest.packages[0]

    assert package.package_kind == ArchiveKind.ZIP
    assert package.packaging_strategy == PackagingStrategy.ZIP
    assert (tmp_path / "exported" / package.package_path).exists()


def test_export_zip_honors_gitignore_and_default_excludes(tmp_path: Path) -> None:
    project_dir = tmp_path / "zip-app"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text("[project]\nname='zip-app'\n", encoding="utf-8")
    (project_dir / ".gitignore").write_text("secret.txt\ncache/\n", encoding="utf-8")
    (project_dir / "keep.txt").write_text("keep\n", encoding="utf-8")
    (project_dir / "secret.txt").write_text("secret\n", encoding="utf-8")
    cache_dir = project_dir / "cache"
    cache_dir.mkdir()
    (cache_dir / "temp.txt").write_text("temp\n", encoding="utf-8")
    node_modules = project_dir / "node_modules"
    node_modules.mkdir()
    (node_modules / "left-pad.js").write_text("module.exports = 1\n", encoding="utf-8")

    reports = scan_local_roots([tmp_path], default_scan_options())
    manifest = export_projects(reports, tmp_path / "exported", [tmp_path])
    package = manifest.packages[0]

    with zipfile.ZipFile(tmp_path / "exported" / package.package_path, "r") as zip_file:
        names = set(zip_file.namelist())

    assert "keep.txt" in names
    assert "secret.txt" not in names
    assert "cache/temp.txt" not in names
    assert "node_modules/left-pad.js" not in names


def test_import_restores_bundle_and_zip_packages(tmp_path: Path) -> None:
    git_project = tmp_path / "git-app"
    git_project.mkdir()
    init_git_repo(git_project)
    (git_project / "pyproject.toml").write_text("[project]\nname='git-app'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_project), "add", "pyproject.toml"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(git_project), "commit", "-m", "init"], check=True, capture_output=True, text=True)

    zip_project = tmp_path / "zip-app"
    zip_project.mkdir()
    (zip_project / "package.json").write_text('{"name":"zip-app"}\n', encoding="utf-8")
    (zip_project / "index.js").write_text("console.log('hi')\n", encoding="utf-8")

    reports = scan_local_roots([tmp_path], default_scan_options())
    export_dir = tmp_path / "exported"
    export_projects(reports, export_dir, [tmp_path])

    results = import_packages(export_dir / "manifest.json", tmp_path / "imported")
    statuses = {item.project_name: item.status for item in results}

    assert statuses["git-app"] == "imported"
    assert statuses["zip-app"] == "imported"
    assert (tmp_path / "imported" / "git-app" / ".git").exists()
    assert (tmp_path / "imported" / "zip-app" / "index.js").exists()


def test_import_accepts_windows_style_manifest_paths(tmp_path: Path) -> None:
    project_dir = tmp_path / "dirty-app"
    project_dir.mkdir()
    init_git_repo(project_dir)
    source_file = project_dir / "app.py"
    source_file.write_text("print('v1')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project_dir), "add", "app.py"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(project_dir), "commit", "-m", "init"], check=True, capture_output=True, text=True)
    source_file.write_text("print('v2')\n", encoding="utf-8")

    reports = scan_local_roots([tmp_path], default_scan_options())
    export_dir = tmp_path / "exported"
    export_projects(reports, export_dir, [tmp_path])

    manifest_path = export_dir / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_data["packages"][0]["package_path"] = manifest_data["packages"][0]["package_path"].replace("/", "\\")
    manifest_data["packages"][0]["overlay_path"] = manifest_data["packages"][0]["overlay_path"].replace("/", "\\")
    manifest_path.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    results = import_packages(manifest_path, tmp_path / "imported")

    assert results[0].status == "imported"
    assert (tmp_path / "imported" / "dirty-app" / "app.py").read_text(encoding="utf-8") == "print('v2')\n"


def test_manifest_is_loadable(tmp_path: Path) -> None:
    project_dir = tmp_path / "zip-app"
    project_dir.mkdir()
    (project_dir / "package.json").write_text('{"name":"zip-app"}\n', encoding="utf-8")

    reports = scan_local_roots([tmp_path], default_scan_options())
    export_dir = tmp_path / "exported"
    export_projects(reports, export_dir, [tmp_path])

    manifest = load_manifest(export_dir / "manifest.json")
    assert json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))["version"] == 1
    assert manifest.packages[0].name == "zip-app"