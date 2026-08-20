---
title: "clean 识别 .NET bin/obj（邻居文件规则）"
type: plan
status: completed
date: 2026-08-21
description: |
  用父目录是否存在 .csproj/.fsproj/.vbproj/.sln 判定 bin/obj 是否为可重建产物，
  避免把脚本仓、Go/C 工程里的 bin 当垃圾删掉。
---

# clean 识别 .NET bin/obj（邻居文件规则）

## 一、背景与范围

当前 `clean` **只按目录 basename** 匹配 `CLEAN_PROFILES`。`bin` / `obj` 故意不在名单里：这两个名字在脚本仓、工具仓里经常是源码或手工产物，全局匹配会误伤。

结果是典型 .NET 布局无法回收，例如：

```text
C:\code\starblog\starblog\demo\OutboxSmokeTest\
  OutboxSmokeTest.csproj
  bin\Debug
  obj\project.assets.json
```

该 git 根甚至没有 `.sln`（扫描类型为 `unknown`），但 walk 已经能走到这些目录。缺的是上下文判定，不是项目发现。

**本计划只改 clean 目标识别与 apply 再校验。** 不做：

- 把 `bin`/`obj` 写入全局 `DEFAULT_EXCLUDES`（会误伤非 .NET 的 `bin/`）
- 按 `ProjectType.DOTNET` 开闸（无 sln 的仓、混合仓会漏或误伤）
- 自定义 `<OutputPath>`（如仓库根 `artifacts/`）
- scan 把 `*.csproj` 当作项目标记（正交，follow-up）
- zip 导出排除 .NET `bin`/`obj`（应用同一套邻居规则，follow-up）

## 二、规则

1. 无歧义目录名（`node_modules`、`dist`、`target` 等）仍只按 basename
2. 目录名为 `bin` 或 `obj`，且**父目录文件**中存在 `.csproj` / `.fsproj` / `.vbproj` / `.sln`（大小写不敏感）→ `build` profile
3. 无上述邻居文件的 `bin`/`obj` → 不是 clean 目标
4. 发现与 apply 共用同一判定函数；apply 不得只查 basename（否则 dry-run 能列出、真删会被 skip）
5. 仍永不删除 `.git`；默认 dry-run

不采用的备选：

- 以扫描得到的项目类型为准：类型挂在 git 根上，管不到嵌套 csproj
- 以 `obj/project.assets.json` 等指纹为主：空 `bin`、只 restore 未 build 会漏；邻居规则对 .NET 惯例已足够

## 三、任务清单

- [x] `cleaner.py`：上下文规则 + 发现/apply 共用判定
- [x] 交互 checkbox 的 build 列表标明 `.NET bin/obj`（避免用户以为所有 `bin` 都会删）
- [x] 单测：命中 csproj/fsproj/vbproj/sln；放过脚本 `bin/`；嵌套在 python/unknown git 根下仍命中；apply 删除与再校验 skip
- [x] README + `tech-design.md` 写明邻居规则
- [x] 本计划完成后移入 `completed/`

## 四、验收标准

1. `Foo.csproj` 旁边的 `bin/`、`obj/` 出现在 `build` 候选中；`--apply -p build` 可删，源码与 csproj 保留
2. 纯脚本仓 `bin/run.sh`（无工程文件邻居）不出现在候选里
3. git 根无 `.sln`、类型为 unknown/python 时，嵌套 `demo/*/bin` 仍能列出
4. 对「无邻居文件的 `bin`」构造 CleanTarget 再 apply → `skipped`，目录仍在
5. 默认 dry-run 行为不变；`.git` 仍受保护
6. CLI help 仍可按 cp1252 编码（Windows Rich `legacy_windows_render`）

## 五、相关路径

- `src/code_porter/cleaner.py`
- `src/code_porter/cli.py`（profile 展示文案）
- `tests/test_clean.py`
- `README.md`
- `docs/tech-design.md`

## 六、Follow-up（不在本计划）

1. `infer_project_type` 认 `*.csproj` / `*.fsproj` / `*.vbproj`，使无 git 的独立工程目录也能被 scan/clean 当成项目
2. export/scan 的排除列表对 .NET `bin`/`obj` 使用同一套邻居规则，避免 zip 把构建产物打进去
3. 可选：`.vs`、`TestResults`、老 NuGet `packages`（同样需要上下文，不能按名字全局删）
