---
title: "技术架构"
type: design
status: active
date: 2026-08-12
tags:
  - tech
  - architecture
description: |
  code-porter 的技术选型、模块职责、用户代码打包策略、本工具分发渠道、
  数据模型与 CLI 设计要点。
---

# code-porter 技术架构

> 技术栈：Python 3.12+ · Typer · Rich · pathspec · 系统 Git CLI

## 一、技术选型

| 层次 | 技术 | 说明 |
|------|------|------|
| 语言 / 运行时 | Python ≥ 3.12 | 现代类型语法、`pathlib` 能力 |
| CLI 框架 | Typer | 子命令、参数解析、入口 `code-porter` |
| 终端 UI | Rich | 表格、进度条、着色 |
| 交互选择 | questionary | `clean` 等交互 checkbox |
| 忽略规则 | pathspec | 读项目 `.gitignore` 并叠加默认排除 |
| 版本控制 | 系统 `git` | bundle create / clone / status 等子进程调用 |
| 包管理 / 发布 | uv + hatchling / hatch-vcs | 开发同步、版本来自 VCS、PyPI 发布 |
| 预编译二进制 | PyInstaller（`--onefile`） | GHA 多平台构建，挂到 GitHub Release |
| Windows 包管理 | Scoop bucket [`star-plan/scoop`](https://github.com/star-plan/scoop) | 独立仓自同步，不回写本仓 |
| macOS/Linux 包管理 | Homebrew tap [`star-plan/homebrew-tap`](https://github.com/star-plan/homebrew-tap) | 独立仓自同步 Formula |
| 测试 | pytest | `tests/`，`pythonpath = src` |

**刻意不做的选择：**

- 无服务端、无数据库、无网络协议栈（迁移介质由用户自选）
- 不内嵌 libgit2；直接调用本机 `git`，行为与用户环境一致

## 二、总体架构

```text
Windows 工作机
    ↓ 本地扫描与导出
    Git 仓库 → git bundle
    非 Git 项目 → zip
    脏 Git 仓库 → bundle + overlay zip
    ↓ 拷贝导出目录
目标机（如 MacBook）
    ↓ 本地导入
    bundle → git clone
    zip → 解压恢复
    bundle + overlay → clone 后覆盖工作区
```

### 2.1 源码布局

```text
code-porter/
├── src/
│   ├── main.py                 # 可选入口
│   └── code_porter/
│       ├── __init__.py
│       ├── cli.py              # Typer 命令与终端渲染
│       ├── models.py           # 领域模型与枚举
│       ├── scanner.py          # 项目发现与扫描
│       ├── archive.py          # 导出 / 导入 / manifest
│       └── cleaner.py          # 垃圾目录发现与删除
├── tests/
├── docs/
├── pyproject.toml
└── README.md
```

### 2.2 模块职责

| 模块 | 职责 |
|------|------|
| `cli.py` | 命令入口、参数、进度条、表格输出、状态筛选 |
| `scanner.py` | 按 marker 发现项目、Git 状态、大小、打包策略建议 |
| `archive.py` | bundle/zip/overlay 导出、manifest 读写、导入流程 |
| `cleaner.py` | clean profile、目标发现、dry-run / apply |
| `models.py` | `ProjectReport`、`PackageEntry`、`ExportManifest` 等 |

依赖方向（自上而下）：

```text
cli → scanner / archive / cleaner
archive → models, scanner（DEFAULT_EXCLUDES）
cleaner → scanner（discover_projects）
scanner → models
```

## 三、打包策略

| 条件 | `PackagingStrategy` | 产物 |
|------|---------------------|------|
| 干净 Git 仓库 | `git_bundle` | `{name}.bundle` |
| 脏 Git 仓库 | `git_bundle_with_overlay` | `{name}.bundle` + `{name}.worktree.zip` |
| 非 Git 项目 | `zip_archive` | `{name}.zip` |
| 不值得导出 / 失败降级 | `skip` 或降级 zip | 见扫描原因字段 |

### 3.1 干净 Git

```bash
git bundle create project.bundle --all
```

### 3.2 脏 Git

在 bundle 之外，再导出当前工作区 overlay zip，既保留历史也保留未提交文件。

### 3.3 非 Git / 降级

- 优先读取项目根 `.gitignore`
- 叠加默认排除目录
- 浅克隆等无法可靠 bundle 的情况可降级为 zip（见 `bundle_export_unsupported_reason`）

### 3.4 默认排除目录

```text
node_modules
.venv
.uv-cache
uv-cache
gomodcache
.gomodcache
gocache
.gocache
dist
build
target
.next
.cache
.git
```

（实现以 `scanner.DEFAULT_EXCLUDES` 为准；`clean` 的 profile 集合更细，见 `cleaner.CLEAN_PROFILES`。按 basename 匹配，嵌套路径如 `.tmp/uv-cache` 也会生效，但不会整删混合用途的 `.tmp`。）

## 四、导出目录与 Manifest

### 4.1 目录结构

```text
exports/windows-backup/
    manifest.json
    artifacts/
        project-a.bundle
        project-b.worktree.zip
        project-c.zip
```

### 4.2 Manifest 形态（概念）

```json
{
  "version": 1,
  "created_at": "2026-08-12T12:00:00+08:00",
  "source_roots": ["C:/code/1"],
  "packages": [
    {
      "name": "land-go",
      "package_kind": "bundle",
      "package_path": "artifacts/land-go-xxxx.bundle",
      "packaging_strategy": "git_bundle",
      "overlay_path": null
    }
  ]
}
```

权威字段以 `models.ExportManifest` / `PackageEntry` 的 `to_dict()` 为准。

### 4.3 导入行为摘要

| 包类型 | 行为 |
|--------|------|
| bundle | `git clone <bundle>` 到目标目录 |
| bundle + overlay | clone 后将 overlay zip 解压覆盖工作区 |
| zip | 解压到目标目录 |
| 目标已存在 | 默认跳过；可用 `--on-existing replace` 覆盖 |

## 五、扫描与项目发现

### 5.1 项目标记

| 标记文件 | `ProjectType` |
|----------|----------------|
| `package.json` | node |
| `pyproject.toml` | python |
| `go.mod` | go |
| `Cargo.toml` | rust |
| `*.sln` 等 | dotnet（及实现中的扩展规则） |

未识别标记时可为 `unknown`，仍可能作为目录项目处理（以实现为准）。

### 5.2 扫描结论字段（`ProjectReport`）

- 路径、类型、是否 Git / remote / clean
- 体积、大目录列表、已存在的忽略目录
- `packaging_strategy` + `packaging_reason`
- `worth_exporting` + `worth_reason`

### 5.3 `scan --status` 筛选（OR）

`dirty` · `clean` · `git` · `not-git` · `remote` · `no-remote` · `exportable` · `skip` · `bundle` · `overlay` · `zip`

### 5.4 `scan` / `clean` 结果排序

- 选项：`--sort` / `-S KEY`，`--reverse` / `-r`
- 流水线：`filter → sort → 表格 / JSON`（人读与机器读顺序一致）
- `scan` KEY：`path` · `name` · `size`（默认大→小）· `type` · `package` · `export`（值得导出优先）
- `clean` KEY：`profile`（deps→cache→build，同组内体积降序）· `size` · `project` · `name` · `path`
- 未指定 `--sort` 时：`scan` 保持 path 序；`clean` 默认 `PROFILE_ORDER` + 体积降序
- 调研背景：[research/scan-clean-result-sort.md](./research/scan-clean-result-sort.md)

## 六、Clean 设计要点

| Profile | 典型目录 | 风险 |
|---------|----------|------|
| `deps` | `node_modules`、`.venv`、`venv`、`.uv-cache` / `uv-cache`、`gomodcache` | 可重装，推荐 |
| `cache` | `.next`、`.cache`、`__pycache__`、`gocache` 等 | 可重建 |
| `build` | `dist`、`build`、`target` 等 | 较高，需有意选择 |
| `all` | 以上全部 | 仍永不删 `.git` |

安全约束：

1. 默认 dry-run，仅列表
2. `--apply` 才删除；非交互还需 `-p/--profile` 与 `-y/--yes`
3. `PROTECTED_DIR_NAMES` 含 `.git`

## 七、CLI 命令面

| 命令 | 作用 | 关键实现 |
|------|------|----------|
| `scan` | 扫描与表格/JSON | `scanner` + `cli` 渲染 |
| `check` | 迁移前安全检查 | `SafetyReport` |
| `clean` | 垃圾目录预览/删除 | `cleaner` |
| `export` | 扫描并导出归档 | `export_projects` |
| `import` | 按 manifest 导入 | `import_packages` |

人读示例见根目录 [README.md](../README.md)；产品语义见 [product-design.md](./product-design.md)。

## 八、跨机复制

工具**不绑定**复制方式。导出目录可通过 U 盘、SMB、iCloud Drive、移动硬盘等任意介质到达目标机。

## 九、本工具如何分发（安装渠道）

本节指 **code-porter 自身**如何交付给终端用户，与「三、打包策略」（用户代码库 → bundle/zip）无关。

详细对比、备选方案与路线图见调研：[research/distribution-packaging.md](./research/distribution-packaging.md)。

### 9.1 渠道矩阵

| 受众 | 渠道 | 说明 |
|------|------|------|
| 开发者 | PyPI / `uvx code-porter` | 现有主路径 |
| 要免 Python 环境 | GitHub Release 预编译 | PyInstaller one-file；附 `SHA256SUMS` |
| Windows 包管理 | Scoop [`star-plan/scoop`](https://github.com/star-plan/scoop) | 独立 bucket，不回写本仓 |
| macOS / Linux 包管理 | Homebrew [`star-plan/tap`](https://github.com/star-plan/homebrew-tap) | Linux 亦可 brew，与 mac 共用 Formula |
| 前置条件 | 系统 `git` 在 `PATH` | 导出/导入 bundle 调用本机 Git |

### 9.2 发布流水线（摘要）

```text
tag v*.*.*
  → 测试 + PyPI
  → matrix 构建 Win/Linux/macOS 二进制 → GitHub Release
  →（可选 PACKAGING_TOKEN）notify star-plan/scoop & homebrew-tap
  → packaging 仓 cron/dispatch 从 Release 同步 manifest / Formula
```

应用仓**不**在发版后 commit Scoop/Homebrew 的 version/hash，避免 bot 污染 git 历史。

### 9.3 后续（未完成）

Linux deb/rpm、代码签名、Aqua/AUR 等见 [changes/active/distribution-followups.md](./changes/active/distribution-followups.md) 与上述 research 文档。

## 十、演进约定

- 改变用户代码打包语义、manifest 版本或默认排除规则：先更新本文，必要时写 `docs/changes/active/` 计划
- 改变本工具分发渠道的**默认策略**：更新本节摘要，细节写入 `docs/research/distribution-packaging.md`
- 仅改文案或小 bugfix：可直接改代码，不必强制 change plan
- 产品范围变化：同步 [product-design.md](./product-design.md)
