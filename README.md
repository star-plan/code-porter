# code-porter

一个面向开发者工作区迁移的 Git 优先扫描器。它会扫描本地目录或远端 Windows 主机上的项目，判断项目类型、Git 状态、是否存在 remote、是否命中大目录/垃圾目录，以及是否值得迁移；随后可以生成或直接执行 git clone、git bundle、源码同步等迁移动作。

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

按扫描报告执行迁移：

```bash
uv run code-porter execute reports/remote-scan.json ~/code/imported --source-host kunkun --dry-run
uv run code-porter execute reports/remote-scan.json ~/code/imported --source-host kunkun --apply
```

输出内容包括：

- 项目名称与路径
- 项目类型（Python / Node / Go / Rust / .NET）
- 是否 Git 仓库
- 是否存在 Git remote
- 首个 Git remote 名称与 URL
- work tree 是否干净
- 目录大小
- 是否值得迁移与原因
- 大目录命中情况
- 默认忽略目录命中情况
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
- execute：根据扫描 JSON 直接执行迁移，支持 dry-run

## 说明

- 默认会排除 node_modules、.venv、dist、build、target、.next、.cache、.git
- scan-local 和 scan-remote 支持 `--large-dir-threshold-mb` 调整大目录阈值
- 远端扫描依赖 SSH 可直接执行 PowerShell 与 git 命令
- 远端非 Git 项目执行时会通过 PowerShell + robocopy + Compress-Archive 打包后再用 scp 拉回本地解压
- 对于已存在且 remote URL 一致的目标 Git 目录，会自动将 clone 动作降级为 pull