import subprocess
from pathlib import Path

from code_porter.models import ProjectType
from code_porter.scanner import check_local_roots, default_scan_options


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test User"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True, capture_output=True, text=True)


def create_bare_remote(tmp_path: Path, name: str = "origin") -> Path:
    remote = tmp_path / f"{name}.git"
    remote.mkdir()
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    return remote


def add_remote(project_dir: Path, remote_path: Path, name: str = "origin") -> None:
    subprocess.run(["git", "-C", str(project_dir), "remote", "add", name, str(remote_path)], check=True, capture_output=True, text=True)


def commit_file(project_dir: Path, filename: str, content: str, message: str) -> None:
    file_path = project_dir / filename
    file_path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(project_dir), "add", filename], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(project_dir), "commit", "-m", message], check=True, capture_output=True, text=True)


def test_check_clean_project_pushed_to_remote(tmp_path: Path) -> None:
    remote = create_bare_remote(tmp_path)
    project_dir = tmp_path / "clean-app"
    project_dir.mkdir()
    init_git_repo(project_dir)
    add_remote(project_dir, remote)
    commit_file(project_dir, "app.py", "print('hello')\n", "init")
    subprocess.run(["git", "-C", str(project_dir), "push", "-u", "origin", "main"], check=True, capture_output=True, text=True)

    reports = check_local_roots([tmp_path], default_scan_options())

    assert len(reports) == 1
    report = reports[0]
    assert report.name == "clean-app"
    assert report.is_git_repo is True
    assert report.has_remote is True
    assert report.is_clean is True
    assert report.unpushed_commit_count == 0
    assert report.status == "ok"
    assert report.issues == []


def test_check_unpushed_commits(tmp_path: Path) -> None:
    remote = create_bare_remote(tmp_path)
    project_dir = tmp_path / "unpushed-app"
    project_dir.mkdir()
    init_git_repo(project_dir)
    add_remote(project_dir, remote)
    commit_file(project_dir, "app.py", "print('v1')\n", "init")
    subprocess.run(["git", "-C", str(project_dir), "push", "-u", "origin", "main"], check=True, capture_output=True, text=True)
    commit_file(project_dir, "app.py", "print('v2')\n", "update")

    reports = check_local_roots([tmp_path], default_scan_options())

    assert len(reports) == 1
    report = reports[0]
    assert report.unpushed_commit_count == 1
    assert report.status == "danger"
    assert any("未 push" in issue for issue in report.issues)


def test_check_dirty_working_tree(tmp_path: Path) -> None:
    remote = create_bare_remote(tmp_path)
    project_dir = tmp_path / "dirty-app"
    project_dir.mkdir()
    init_git_repo(project_dir)
    add_remote(project_dir, remote)
    commit_file(project_dir, "app.py", "print('v1')\n", "init")
    subprocess.run(["git", "-C", str(project_dir), "push", "-u", "origin", "main"], check=True, capture_output=True, text=True)
    (project_dir / "app.py").write_text("print('v2')\n", encoding="utf-8")

    reports = check_local_roots([tmp_path], default_scan_options())

    assert len(reports) == 1
    report = reports[0]
    assert report.is_clean is False
    assert report.unpushed_commit_count == 0
    assert report.status == "warning"
    assert any("未提交" in issue for issue in report.issues)


def test_check_no_remote(tmp_path: Path) -> None:
    project_dir = tmp_path / "local-only"
    project_dir.mkdir()
    init_git_repo(project_dir)
    commit_file(project_dir, "app.py", "print('hello')\n", "init")

    reports = check_local_roots([tmp_path], default_scan_options())

    assert len(reports) == 1
    report = reports[0]
    assert report.has_remote is False
    assert report.status == "danger"
    assert any("没有远程仓库" in issue for issue in report.issues)


def test_check_non_git_project(tmp_path: Path) -> None:
    project_dir = tmp_path / "plain-dir"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text("[project]\nname='plain'\n", encoding="utf-8")

    reports = check_local_roots([tmp_path], default_scan_options())

    assert len(reports) == 1
    report = reports[0]
    assert report.is_git_repo is False
    assert report.status == "danger"
    assert any("不是 Git 仓库" in issue for issue in report.issues)


def test_check_no_upstream_tracking(tmp_path: Path) -> None:
    remote = create_bare_remote(tmp_path)
    project_dir = tmp_path / "no-upstream"
    project_dir.mkdir()
    init_git_repo(project_dir)
    add_remote(project_dir, remote)
    commit_file(project_dir, "app.py", "print('hello')\n", "init")
    subprocess.run(["git", "-C", str(project_dir), "push", "origin", "main"], check=True, capture_output=True, text=True)
    # Push without -u, so no upstream tracking

    reports = check_local_roots([tmp_path], default_scan_options())

    assert len(reports) == 1
    report = reports[0]
    assert report.has_remote is True
    assert report.upstream_branch is None
    assert report.status == "warning"
    assert any("追踪远程分支" in issue for issue in report.issues)


def test_check_mixed_projects(tmp_path: Path) -> None:
    # ok project
    remote_ok = create_bare_remote(tmp_path, "ok-origin")
    ok_dir = tmp_path / "ok-app"
    ok_dir.mkdir()
    init_git_repo(ok_dir)
    add_remote(ok_dir, remote_ok)
    commit_file(ok_dir, "app.py", "print('ok')\n", "init")
    subprocess.run(["git", "-C", str(ok_dir), "push", "-u", "origin", "main"], check=True, capture_output=True, text=True)

    # danger project: unpushed
    remote_danger = create_bare_remote(tmp_path, "danger-origin")
    danger_dir = tmp_path / "danger-app"
    danger_dir.mkdir()
    init_git_repo(danger_dir)
    add_remote(danger_dir, remote_danger)
    commit_file(danger_dir, "app.py", "print('v1')\n", "init")
    subprocess.run(["git", "-C", str(danger_dir), "push", "-u", "origin", "main"], check=True, capture_output=True, text=True)
    commit_file(danger_dir, "app.py", "print('v2')\n", "update")

    reports = check_local_roots([tmp_path], default_scan_options())

    assert len(reports) == 2
    statuses = {r.name: r.status for r in reports}
    assert statuses["ok-app"] == "ok"
    assert statuses["danger-app"] == "danger"


def test_check_monorepo(tmp_path: Path) -> None:
    remote = create_bare_remote(tmp_path)
    mono_dir = tmp_path / "monorepo"
    mono_dir.mkdir()
    init_git_repo(mono_dir)
    add_remote(mono_dir, remote)
    (mono_dir / "pyproject.toml").write_text("[project]\nname='mono'\n", encoding="utf-8")
    nested = mono_dir / "src" / "nested"
    nested.mkdir(parents=True)
    (nested / "package.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(mono_dir), "add", "."], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(mono_dir), "commit", "-m", "init"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(mono_dir), "push", "-u", "origin", "main"], check=True, capture_output=True, text=True)

    reports = check_local_roots([tmp_path], default_scan_options())

    # Should only report the git root, not nested markers
    assert len(reports) == 1
    assert reports[0].name == "monorepo"
    assert reports[0].status == "ok"
