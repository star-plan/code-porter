# code-porter

[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/code-porter)](https://pypi.org/project/code-porter/)
[![PyPI Version](https://img.shields.io/pypi/v/code-porter)](https://pypi.org/project/code-porter/)
[![CI](https://github.com/star-plan/code-porter/actions/workflows/ci.yml/badge.svg)](https://github.com/star-plan/code-porter/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/star-plan/code-porter)](https://github.com/star-plan/code-porter/blob/main/LICENSE)

一个纯本地运行的代码库导入导出工具。它会在当前机器上扫描项目，优先将 Git 仓库导出为 git bundle，将非 Git 项目导出为 zip；导出完成后，可在另一台机器上基于 manifest 批量导入。

## 快速开始

无需安装任何 Python 环境或依赖，一行命令即可运行：

```bash
# 查看帮助
uvx code-porter --help
```

> `uvx` 是 [uv](https://docs.astral.sh/uv/) 自带的命令，如果尚未安装 uv，可参考 [uv 官方安装指南](https://docs.astral.sh/uv/getting-started/installation/)。

## 用法

### 扫描本地目录

```bash
uvx code-porter scan ~/code ~/lab --json-output reports/local-scan.json
```

输出内容包括：

- 项目名称与路径
- 项目类型（Python / Node / Go / Rust / .NET）
- 是否 Git 仓库
- 是否存在 Git remote
- 首个 Git remote 名称与 URL
- work tree 是否干净
- 目录大小
- 是否值得导出与原因
- 大目录命中情况
- 默认忽略目录命中情况
- 推荐打包策略与原因

### 导出 bundle/zip 归档

```bash
uvx code-porter export ~/code ~/lab ./exports/local-backup
```

### 导入归档

```bash
uvx code-porter import ./exports/local-backup/manifest.json ~/code/imported
```

## 命令

| 命令 | 作用 |
|------|------|
| scan | 扫描本地目录，分析项目结构 |
| export | 扫描并输出 bundle/zip 归档，以及 manifest.json |
| import | 根据 manifest.json 将归档导入到目标目录 |

## 当前打包策略

- 干净 Git 仓库：导出 git bundle
- 脏 Git 仓库：导出 git bundle，并额外导出工作区 overlay zip
- 非 Git 项目：导出 zip

## 说明

- 默认会排除 node_modules、.venv、dist、build、target、.next、.cache、.git
- scan 与 export 支持 `--large-dir-threshold-mb` 调整大目录阈值
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
uv build
uv publish
```
