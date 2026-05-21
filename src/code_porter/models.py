from __future__ import annotations

from datetime import datetime
from dataclasses import asdict, dataclass, field
from enum import StrEnum


class ProjectType(StrEnum):
    PYTHON = "python"
    NODE = "node"
    GO = "go"
    RUST = "rust"
    DOTNET = "dotnet"
    UNKNOWN = "unknown"


class PackagingStrategy(StrEnum):
    BUNDLE = "git_bundle"
    BUNDLE_WITH_OVERLAY = "git_bundle_with_overlay"
    ZIP = "zip_archive"
    SKIP = "skip"


class ArchiveKind(StrEnum):
    BUNDLE = "bundle"
    ZIP = "zip"


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
    packaging_strategy: PackagingStrategy = PackagingStrategy.SKIP
    packaging_reason: str = ""
    worth_exporting: bool = True
    worth_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["project_type"] = self.project_type.value
        data["packaging_strategy"] = self.packaging_strategy.value
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


@dataclass(slots=True)
class PackageEntry:
    name: str
    project_type: ProjectType
    source_path: str
    package_kind: ArchiveKind
    package_path: str
    packaging_strategy: PackagingStrategy
    is_git_repo: bool
    is_clean: bool | None
    has_remote: bool
    size_bytes: int
    packaging_reason: str
    remote_url: str | None = None
    overlay_path: str | None = None
    ignored_patterns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["project_type"] = self.project_type.value
        data["package_kind"] = self.package_kind.value
        data["packaging_strategy"] = self.packaging_strategy.value
        return data


@dataclass(slots=True)
class ExportManifest:
    version: int
    created_at: str
    source_roots: list[str]
    packages: list[PackageEntry]

    @classmethod
    def create(cls, source_roots: list[str], packages: list[PackageEntry]) -> "ExportManifest":
        return cls(
            version=1,
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            source_roots=source_roots,
            packages=packages,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "source_roots": self.source_roots,
            "packages": [item.to_dict() for item in self.packages],
        }