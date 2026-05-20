from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum


class ProjectType(StrEnum):
    PYTHON = "python"
    NODE = "node"
    GO = "go"
    RUST = "rust"
    DOTNET = "dotnet"
    UNKNOWN = "unknown"


class MigrationStrategy(StrEnum):
    CLONE = "git_clone"
    BUNDLE = "git_bundle"
    RSYNC = "rsync_copy"
    SKIP = "skip"


class PlanAction(StrEnum):
    CLONE = "clone"
    PULL = "pull"
    BUNDLE = "bundle_clone"
    SYNC = "sync_copy"
    SKIP = "skip"


@dataclass(slots=True)
class ProjectReport:
    name: str
    path: str
    project_type: ProjectType
    is_git_repo: bool
    has_remote: bool
    is_clean: bool | None
    size_bytes: int
    remote_name: str | None = None
    remote_url: str | None = None
    large_directories: list[str] = field(default_factory=list)
    ignored_directories_present: list[str] = field(default_factory=list)
    migration_strategy: MigrationStrategy = MigrationStrategy.SKIP
    migration_reason: str = ""
    worth_migrating: bool = True
    worth_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["project_type"] = self.project_type.value
        data["migration_strategy"] = self.migration_strategy.value
        data["size_human"] = self.size_human
        return data

    @property
    def size_human(self) -> str:
        size = float(self.size_bytes)
        units = ["B", "KB", "MB", "GB", "TB"]
        for unit in units:
            if size < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(size)}{unit}"
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"