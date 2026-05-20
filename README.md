# code-porter

一个面向开发者工作区迁移的 Git 优先扫描器。它会扫描本地目录或远端 Windows 主机上的项目，判断项目类型、Git 状态、是否存在 remote，并给出推荐迁移策略：git clone、git bundle 或 rsync/scp 源码复制。

## 技术选型

- 使用 uv 管理 Python 环境与依赖
- 使用 typer 构建 CLI
- 使用 rich 输出表格与 JSON

## 安装

```bash
uv sync
```

## 用法

扫描本地目录：

```bash
uv run code-porter scan-local ~/code ~/lab --json-output reports/local-scan.json
```

扫描远端 Windows 主机：

```bash
uv run code-porter scan-remote kunkun D:/Projects C:/code --json-output reports/remote-scan.json
```

基于扫描报告生成迁移计划：

```bash
uv run code-porter plan reports/remote-scan.json ~/code/imported --source-host kunkun --json-output reports/migration-plan.json
```

输出内容包括：

- 项目名称与路径
- 项目类型（Python / Node / Go / Rust / .NET）
- 是否 Git 仓库
- 是否存在 Git remote
- work tree 是否干净
- 目录大小
- 推荐迁移策略与原因
- 对应的迁移命令模板

## 当前策略

- 干净且有 remote 的 Git 仓库：推荐 git clone
- 干净但没有 remote 的 Git 仓库：推荐 git bundle
- 脏 Git 仓库：按非 Git 项目处理，推荐 rsync/scp
- 非 Git 项目：推荐 rsync/scp

## 命令

- scan-local：扫描本地目录
- scan-remote：通过 SSH + PowerShell 扫描远端 Windows 目录
- plan：根据扫描 JSON 生成 clone、bundle、rsync 命令模板

## 说明

- 默认会排除 node_modules、.venv、dist、build、target、.next、.cache、.git
- 远端扫描依赖 SSH 可直接执行 PowerShell 与 git 命令
- 当前版本重点是“扫描与计划”，会生成可执行命令模板，但不会自动批量执行迁移