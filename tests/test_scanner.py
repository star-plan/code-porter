import subprocess
from pathlib import Path

from code_porter.models import MigrationStrategy, PlanAction, ProjectReport, ProjectType
from code_porter.planner import build_plans
from code_porter.scanner import choose_strategy, default_scan_options, scan_local_roots


def test_choose_strategy_prefers_clone_for_clean_remote_repo() -> None:
    strategy, reason = choose_strategy(True, True, True)
    assert strategy == MigrationStrategy.CLONE
    assert "clone" in reason


def test_choose_strategy_uses_rsync_for_dirty_git_repo() -> None:
    strategy, _ = choose_strategy(True, True, False)
    assert strategy == MigrationStrategy.RSYNC


def test_scan_local_roots_detects_python_and_ignored_dirs(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample-app"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text("[project]\nname='sample-app'\n", encoding="utf-8")
    ignored = project_dir / "node_modules"
    ignored.mkdir()
    (ignored / "bundle.js").write_text("x" * 2048, encoding="utf-8")
    src_dir = project_dir / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("print('hello')\n", encoding="utf-8")

    reports = scan_local_roots([tmp_path], default_scan_options())

    assert len(reports) == 1
    report = reports[0]
    assert report.name == "sample-app"
    assert report.project_type == ProjectType.PYTHON
    assert report.is_git_repo is False
    assert report.migration_strategy == MigrationStrategy.RSYNC
    assert report.ignored_directories_present == ["node_modules"]
    assert report.size_bytes < 2048
    assert report.worth_migrating is True


def test_build_plans_creates_bundle_workflow_for_remote_source(tmp_path: Path) -> None:
    project_dir = tmp_path / "solo-repo"
    report = scan_local_roots([tmp_path], default_scan_options()) if project_dir.exists() else []
    assert report == []

    item = ProjectReport(
        name="solo-repo",
        path="D:/Projects/solo-repo",
        project_type=ProjectType.PYTHON,
        is_git_repo=True,
        has_remote=False,
        is_clean=True,
        size_bytes=512,
        migration_strategy=MigrationStrategy.BUNDLE,
        migration_reason="本地 Git 仓库干净但没有 remote，适合 git bundle",
        worth_migrating=True,
        worth_reason="本地 Git 历史可通过 bundle 保留",
    )

    plans = build_plans([item], tmp_path / "migrated", source_host="kunkun")

    assert len(plans) == 1
    assert plans[0].strategy == MigrationStrategy.BUNDLE
    assert plans[0].action == PlanAction.BUNDLE
    assert "bundle create" in plans[0].command
    assert "scp" in plans[0].command
    assert "git clone" in plans[0].command


def test_scan_local_git_repo_captures_remote_url_and_clone_plan(tmp_path: Path) -> None:
    project_dir = tmp_path / "git-app"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text("[project]\nname='git-app'\n", encoding="utf-8")
    subprocess.run(["git", "init", str(project_dir)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(project_dir), "config", "user.name", "Test User"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(project_dir), "config", "user.email", "test@example.com"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(project_dir), "add", "pyproject.toml"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(project_dir), "commit", "-m", "init"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(project_dir), "remote", "add", "origin", "git@github.com:demo/git-app.git"], check=True, capture_output=True, text=True)

    reports = scan_local_roots([tmp_path], default_scan_options())

    assert len(reports) == 1
    report = reports[0]
    assert report.remote_url == "git@github.com:demo/git-app.git"
    assert report.migration_strategy == MigrationStrategy.CLONE

    plans = build_plans(reports, tmp_path / "dest")
    assert plans[0].action == PlanAction.CLONE
    assert "git@github.com:demo/git-app.git" in plans[0].command


def test_build_plans_uses_pull_for_existing_matching_repo(tmp_path: Path) -> None:
    destination = tmp_path / "dest" / "git-app"
    destination.mkdir(parents=True)
    subprocess.run(["git", "init", str(destination)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(destination), "remote", "add", "origin", "git@github.com:demo/git-app.git"], check=True, capture_output=True, text=True)

    report = ProjectReport(
        name="git-app",
        path="D:/Projects/git-app",
        project_type=ProjectType.PYTHON,
        is_git_repo=True,
        has_remote=True,
        remote_name="origin",
        remote_url="git@github.com:demo/git-app.git",
        is_clean=True,
        size_bytes=1024,
        migration_strategy=MigrationStrategy.CLONE,
        migration_reason="Git 仓库干净且存在 remote，优先 clone",
        worth_migrating=True,
        worth_reason="存在 Git remote，可低成本恢复",
    )

    plans = build_plans([report], tmp_path / "dest")

    assert plans[0].action == PlanAction.PULL
    assert "pull --ff-only" in plans[0].command