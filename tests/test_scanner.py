from pathlib import Path

from code_porter.models import MigrationStrategy, ProjectType
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


def test_build_plans_creates_bundle_workflow_for_remote_source(tmp_path: Path) -> None:
    project_dir = tmp_path / "solo-repo"
    report = scan_local_roots([tmp_path], default_scan_options()) if project_dir.exists() else []
    assert report == []

    from code_porter.models import ProjectReport

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
    )

    plans = build_plans([item], tmp_path / "migrated", source_host="kunkun")

    assert len(plans) == 1
    assert plans[0].strategy == MigrationStrategy.BUNDLE
    assert "bundle create" in plans[0].command
    assert "scp" in plans[0].command
    assert "git clone" in plans[0].command