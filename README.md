# code-porter

[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/code-porter)](https://pypi.org/project/code-porter/)
[![PyPI Version](https://img.shields.io/pypi/v/code-porter)](https://pypi.org/project/code-porter/)
[![CI](https://github.com/star-plan/code-porter/actions/workflows/ci.yml/badge.svg)](https://github.com/star-plan/code-porter/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/star-plan/code-porter)](https://github.com/star-plan/code-porter/blob/main/LICENSE)

一个纯本地运行的代码库导入导出工具。它会在当前机器上扫描项目，优先将 Git 仓库导出为 git bundle，将非 Git 项目导出为 zip；导出完成后，可在另一台机器上基于 manifest 批量导入。

设计文档见 [docs/](./docs/README.md)（[产品设计](./docs/product-design.md) · [技术架构](./docs/tech-design.md)）。局部变更计划放在 [docs/changes/](./docs/changes/README.md)。

## 快速开始

### 普通用户：包管理器安装（推荐）

**前置条件：** 本机需已安装 [Git](https://git-scm.com/) 并加入 `PATH`（导出 / 导入 bundle 会调用系统 `git`）。

#### Scoop（Windows）

```powershell
scoop bucket add star-plan https://github.com/star-plan/scoop
scoop install code-porter
code-porter --help
```

#### Homebrew（macOS / Linux）

```bash
brew tap star-plan/tap
brew install code-porter
code-porter --help
```

### 普通用户：下载预编译二进制

不需要安装 Python 或 uv。打开 [GitHub Releases](https://github.com/star-plan/code-porter/releases)，按系统下载对应文件：

| 平台 | 文件 |
|------|------|
| Windows (x64) | `code-porter-windows-amd64.exe` |
| Linux (x64) | `code-porter-linux-amd64` |
| macOS (Intel) | `code-porter-macos-amd64` |
| macOS (Apple Silicon) | `code-porter-macos-arm64` |

```bash
# Linux / macOS
chmod +x code-porter-linux-amd64   # 或对应的 macOS 文件名
./code-porter-linux-amd64 --help

# Windows（PowerShell / cmd）
.\code-porter-windows-amd64.exe --help
```

可用 Release 中的 `SHA256SUMS` 校验下载文件。Windows SmartScreen 或 macOS Gatekeeper 可能拦截未签名程序：Windows 选「更多信息 → 仍要运行」；macOS 可右键 → 打开。

### 开发者：uvx 一行运行

无需手动装依赖，一行命令即可运行：

```bash
# 查看帮助
uvx code-porter --help
```

> `uvx` 是 [uv](https://docs.astral.sh/uv/) 自带的命令，如果尚未安装 uv，可参考 [uv 官方安装指南](https://docs.astral.sh/uv/getting-started/installation/)。

## 用法

### 扫描本地目录

```bash
# 默认：紧凑表格 + 汇总（适合人读）
uvx code-porter scan ~/code ~/lab

# 需要落盘时再写 JSON 文件
uvx code-porter scan ~/code ~/lab --json-output reports/local-scan.json

# 脚本/管道：只输出 JSON
uvx code-porter scan ~/code ~/lab --json

# 查看完整列（remote / clean / 大目录 / 完整原因）
uvx code-porter scan ~/code ~/lab --verbose

# 只看脏工作区项目（可重复 --status / -s，OR 关系）
uvx code-porter scan ~/code --status dirty
uvx code-porter scan ~/code -s dirty -s no-remote
```

默认终端输出包括：

- 项目名称、类型、Git 状态（是否仓库 / remote / clean）
- 目录大小、推荐打包策略、是否值得导出
- 简短 Note，以及一行汇总（各策略数量、值得导出数量）

使用 `--verbose` 可额外看到 remote、clean、大目录、忽略目录与完整原因；使用 `--json` / `--json-output` 获取完整机器可读结果（含路径、remote URL 等）。

`--status` / `-s` 可选值：`dirty`、`clean`、`git`、`not-git`、`remote`、`no-remote`、`exportable`、`skip`、`bundle`、`overlay`、`zip`。

### 导出 bundle/zip 归档

```bash
uvx code-porter export ~/code ~/lab ./exports/local-backup
```

### 导入归档

```bash
uvx code-porter import ./exports/local-backup/manifest.json ~/code/imported
```

### 清理可重建垃圾目录

```bash
# 默认 dry-run：列出 deps / cache / build 候选及体积
uvx code-porter clean ~/code

# 交互终端会弹出 checkbox 勾选 profile，并询问是否立刻删除
# 非交互 / 脚本用法：
uvx code-porter clean ~/code -p deps --apply --yes
uvx code-porter clean ~/code -p deps -p cache --apply --yes
uvx code-porter clean ~/code -p all --apply --yes
```

Profile 说明：

- `deps`：`node_modules`、`.venv`、`venv`、`.uv-cache` 等可重装依赖（推荐）
- `cache`：`.next`、`.cache`、`__pycache__`、各类工具缓存
- `build`：`dist`、`build`、`target` 等构建产物（风险更高，需有意选择）
- `all`：以上全部（仍永远不会删除 `.git`）

默认只预览不删除；必须显式 `--apply` 才会动手。非交互模式下 `--apply` 还需要 `-p/--profile` 与 `-y/--yes`。

## 命令

| 命令 | 作用 |
|------|------|
| scan | 扫描本地目录，分析项目结构 |
| clean | 预览/删除可重建垃圾目录（node_modules、.venv 等） |
| export | 扫描并输出 bundle/zip 归档，以及 manifest.json |
| import | 根据 manifest.json 将归档导入到目标目录 |

## 当前打包策略

- 干净 Git 仓库：导出 git bundle
- 脏 Git 仓库：导出 git bundle，并额外导出工作区 overlay zip
- 非 Git 项目：导出 zip

## 说明

- 默认会排除 node_modules、.venv、dist、build、target、.next、.cache、.git
- scan 与 export 支持 `--large-dir-threshold-mb` 调整大目录阈值
- scan 默认只输出紧凑表格与汇总；`--json` 纯 JSON，`--json-output` 写文件，`-v/--verbose` 显示完整列，`-s/--status` 按状态筛选
- clean 默认 dry-run；交互终端可用 checkbox 勾选 profile；`--apply` 真删，非交互需 `-p` + `-y`
- scan、export、import 会在终端显示进度条，减少长任务等待焦虑
- 导出 zip 时会读取项目根目录的 .gitignore，并叠加默认排除目录
- bundle 导入后如果存在 overlay zip，会在 clone 后覆盖工作区文件，以保留未提交改动
- import 遇到已存在目录时默认跳过，可用 `--on-existing replace` 覆盖

## 开发者

```bash
# 克隆仓库后，使用 uv 同步依赖
git clone https://github.com/deali/code-porter
cd code-porter
uv sync

# 运行
uv run code-porter --help
```

## 发布

```bash
# 本地构建并上传到 PyPI
uv build
uv publish
```

打 `v*.*.*` 标签推送后，GitHub Actions 会：

1. 运行测试并发布 wheel/sdist 到 PyPI  
2. 在 Windows / Linux / macOS 上用 PyInstaller 构建 standalone 二进制  
3. 创建 GitHub Release，附带各平台二进制与 `SHA256SUMS`  
4. （可选）若配置了 `PACKAGING_TOKEN`，通知 [star-plan/scoop](https://github.com/star-plan/scoop) 与 [star-plan/homebrew-tap](https://github.com/star-plan/homebrew-tap) 立即同步；否则对方按 cron 自动拉取 Release
本地预览二进制（需 dev 依赖）：

```bash
uv sync --group dev
uv run pyinstaller --noconfirm --clean packaging/code-porter.spec
# 产物：dist/code-porter 或 dist/code-porter.exe
```
