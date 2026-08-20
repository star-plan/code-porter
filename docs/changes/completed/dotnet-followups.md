---
title: ".NET follow-ups：项目标记、scan/zip 排除、更多垃圾目录"
type: plan
status: completed
date: 2026-08-21
description: |
  认 *.csproj / *.slnx 为项目标记；scan/zip 用同一套邻居规则排除 bin/obj；
  再回收 .vs、TestResults、老 NuGet packages。
---

# .NET follow-ups：项目标记、scan/zip 排除、更多垃圾目录

## 一、背景与范围

接 [dotnet-bin-obj-clean.md](./dotnet-bin-obj-clean.md) 的 follow-up：

1. `infer_project_type` 认 `*.csproj` / `*.fsproj` / `*.vbproj` / `*.slnx`，无 git 的独立工程也能被 scan/clean
2. scan 体积统计与 zip 导出对 .NET `bin`/`obj` 使用同一套邻居规则（不把 `bin` 写入全局 `DEFAULT_EXCLUDES`）
3. 再覆盖 `.vs`、`TestResults`、老 NuGet `packages`，同样避免按名字误伤

无 git 的 solution 里每个 csproj 若都当独立项目，export 会重复打包。因此 DOTNET 标记在向上解析根时，在找到 `.git` 之前可收束到最近的 `.sln` / `.slnx` 目录。同仓库里嵌套 csproj 会把类型提升为 `dotnet`，避免 `package.json` 因 walk 顺序抢先。

## 二、任务清单

- [x] 抽出 `junk.py`：.NET 文件名与上下文 junk 判定，cleaner/scanner/archive 共用
- [x] `infer_project_type` 认 csproj/fsproj/vbproj/sln/slnx；DOTNET 根可收束到最近解决方案文件
- [x] scan walk / zip walk 跳过上下文 junk；`.vs` 作为无歧义 basename 进入 cache + `DEFAULT_EXCLUDES`
- [x] `TestResults`：邻居有工程文件，或目录内（含一层子目录）有 `.trx` → `build`
- [x] `packages`：目录内有 `repositories.config` → `deps`
- [x] 单测与 README / tech-design / product-design

## 三、验收标准

1. 无 git、仅有 `Foo.csproj` 的目录可被 scan 为 `dotnet`，clean 能列出旁边的 `bin`/`obj`
2. 无 git、根上有 `.sln` / `.slnx`、底下多个 csproj → 一个项目（解决方案根）
3. git 根无 sln、嵌套 csproj → 仍一个 git 项目，类型为 `dotnet`（含嵌套 package.json 的情况）
4. zip 不含 csproj 旁的 `bin`/`obj`，但包含脚本仓 `scripts/bin/run.sh`
5. scan 不把 .NET `bin` 计入体积；脚本 `bin` 仍计入
6. `.vs` 可 clean（cache）；`TestResults` 有 `.trx` 时可 clean；有 `repositories.config` 的 `packages` 可 clean；普通 `packages/` 源码目录不可 clean

## 四、相关路径

- `src/code_porter/junk.py`
- `src/code_porter/scanner.py`
- `src/code_porter/cleaner.py`
- `src/code_porter/archive.py`
- `tests/test_scanner.py`
- `tests/test_clean.py`
- `tests/test_archive_flow.py`
