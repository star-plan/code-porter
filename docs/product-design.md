---
title: "产品设计"
type: design
status: active
date: 2026-08-12
tags:
  - product
  - scope
description: |
  code-porter 的产品定位、适用场景、核心能力、用户旅程与非目标。
---

# code-porter 产品设计

## 一、产品定位

code-porter 是一个**纯本地运行**的代码库导入/导出工具，面向开发者工作区迁移与项目资产整理。

一句话：

> 在当前机器上扫描项目，优先把 Git 仓库导出为 git bundle，把非 Git 项目导出为 zip；拷贝导出目录到另一台机器后，基于 manifest 批量导入。

它解决的核心问题：

- 多台开发机之间迁移大量本地项目，不想依赖完整 Git remote 或复杂的 OpenSSH 配置
- 历史项目、实验项目、AI 生成代码混杂，需要先扫描再决定怎么打包
- 脏工作区也要带走（历史 + 未提交改动）

## 二、适用场景

| 场景 | 说明 |
|------|------|
| 跨机器迁移工作区 | Windows 工作机 → MacBook 等，整包拷贝后恢复 |
| 多语言本地资产整理 | Python / TypeScript / Go / Rust / C# 等混部目录 |
| 历史与实验项目归档 | 部分未规范化、无 remote 的项目也能带走 |
| 换机 / 重装前备份 | 先 `check` / `scan`，再 `export` 落盘 |
| 离线或弱网络环境 | 不依赖推远程；bundle / zip 可走 U 盘、SMB、移动硬盘等 |

## 三、核心能力

| 能力 | 命令 | 用户价值 |
|------|------|----------|
| 扫描 | `scan` | 发现项目、判断 Git 状态、推荐打包策略、筛选状态 |
| 安全检查 | `check` | 重装/迁移前检查 unpushed、脏工作区等风险 |
| 清理 | `clean` | 预览并删除可重建垃圾目录（deps / cache / build） |
| 导出 | `export` | 按策略生成 bundle / overlay / zip + `manifest.json` |
| 导入 | `import` | 按 manifest 在目标机恢复项目 |

### 3.1 扫描（scan）

- 按项目标记发现仓库根（如 `package.json`、`pyproject.toml`、`go.mod` 等）
- 判断：是否 Git、是否有 remote、是否干净、大目录、默认垃圾目录、是否值得导出
- 默认紧凑表格 + 汇总；可选 JSON 输出与状态筛选

### 3.2 清理（clean）

- 按 profile 发现可重建目录：`deps` / `cache` / `build` / `all`
- **默认 dry-run**；真删需显式 `--apply`
- **永不删除** `.git`
- 交互终端可用 checkbox 勾选 profile

### 3.3 导出（export）与导入（import）

见 [技术架构 · 打包策略](./tech-design.md#三打包策略)。产品侧保证：

1. 导出目录自包含：`manifest.json` + `artifacts/`
2. 导入只依赖本地文件与本机已安装的 `git`（bundle 场景）
3. 脏仓库尽量同时保留历史与工作区未提交内容

## 四、典型用户旅程

```text
Windows 工作机
    ↓ scan / check / clean（可选）
    ↓ export → 导出目录（manifest + artifacts）
    ↓ 拷贝（U 盘 / SMB / 云盘 / 移动硬盘）
MacBook / 目标机
    ↓ import ← manifest.json
    ↓ 工作区恢复完成
```

阶段说明：

| 阶段 | 做什么 | 产出 |
|------|--------|------|
| 1. 扫描与决策 | `scan` / `check`，必要时 `clean` 减体积 | 项目清单与风险认知 |
| 2. 本地导出 | `export` 按策略打包 | `manifest.json` + artifacts |
| 3. 跨机复制 | 任意离线/在线介质 | 目标机上的导出目录 |
| 4. 目标机导入 | `import` 按 manifest 恢复 | 目标路径下的项目树 |

## 五、产品原则

1. **纯本地**：无账号、无云服务、无常驻守护进程
2. **Git 优先**：有完整历史时优先 bundle，而不是整树 zip
3. **脏仓库不丢改动**：bundle 保历史，overlay zip 保工作区
4. **可重建内容默认排除**：`node_modules`、`.venv`、构建产物等
5. **安全默认值**：clean 默认 dry-run；import 遇已存在目录默认跳过
6. **人读优先、机器可读可选**：终端表格默认；JSON 按需打开

## 六、获取与安装（产品视角）

工具面向两类安装预期（技术细节见 [tech-design §九](./tech-design.md) 与 [分发调研](./research/distribution-packaging.md)）：

| 用户 | 期望 | 当前主路径 |
|------|------|------------|
| 开发者 | 一行命令、跟进版本 | `uvx code-porter` / PyPI |
| 普通用户 | 不装 Python，用本机包管理器或下载二进制 | Windows：Scoop；macOS/Linux：Homebrew；或 GitHub Release |

**统一前置：** 本机安装 Git 并在 `PATH` 中可用。

后续增强（deb/rpm、签名等）不改变「纯本地 CLI」产品定位，只拓宽到达路径；跟踪见 [changes/active/distribution-followups.md](./changes/active/distribution-followups.md)。

## 七、当前非目标

以下不在当前产品主路径内：

1. 作为远程 Git 托管或 CI 制品分发平台
2. 依赖 Windows OpenSSH / 复杂远程同步协议完成迁移
3. 图形界面（GUI）
4. 自动推送到任意 Git 远程并完成双向同步
5. 对二进制大文件（LFS 等）的专门治理策略（按通用 Git/zip 行为处理）
6. 自建完整 Linux apt/yum 软件源（优先 Release 资产与 Homebrew；见调研文档）

## 八、与文档其他层的关系

- 能力「做什么、不做什么」以本文为准
- 实现细节、模块边界、策略算法见 [tech-design.md](./tech-design.md)
- 分发/包管理选型背景见 [research/](./research/README.md)
- 局部新功能或机制调整先写 [changes/](./changes/README.md)，再改代码
