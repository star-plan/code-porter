# code-porter

一个纯本地运行的代码库导入导出工具。它会在当前机器上扫描项目，优先将 Git 仓库导出为 git bundle，将非 Git 项目导出为 zip；导出完成后，可在另一台机器上基于 manifest 批量导入。

## 技术选型

- 使用 uv 管理 Python 环境与依赖
- 使用 typer 构建 CLI
- 使用 rich 输出表格与 JSON
- 使用 pathspec 解析 .gitignore 规则

## 安装

```bash
uv sync
```

## 用法

扫描本地目录：

```bash
uv run code-porter scan ~/code ~/lab --json-output reports/local-scan.json
```

导出 bundle/zip 归档：

```bash
uv run code-porter export ~/code ~/lab ./exports/local-backup
```

导入归档：

```bash
uv run code-porter import ./exports/local-backup/manifest.json ~/code/imported
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

## 当前打包策略

- 干净 Git 仓库：导出 git bundle
- 脏 Git 仓库：导出 git bundle，并额外导出工作区 overlay zip
- 非 Git 项目：导出 zip

## 命令

- scan：扫描本地目录
- export：扫描并输出 bundle/zip 归档，以及 manifest.json
- import：根据 manifest.json 将归档导入到目标目录

## 说明

- 默认会排除 node_modules、.venv、dist、build、target、.next、.cache、.git
- scan 与 export 支持 `--large-dir-threshold-mb` 调整大目录阈值
- 导出 zip 时会读取项目根目录的 .gitignore，并叠加默认排除目录
- bundle 导入后如果存在 overlay zip，会在 clone 后覆盖工作区文件，以保留未提交改动
- import 遇到已存在目录时默认跳过，可用 `--on-existing replace` 覆盖