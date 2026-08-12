---
title: "code-porter 文档目录"
type: reference
status: active
date: 2026-08-12
description: |
  code-porter 项目文档总索引与默认阅读路径。
---

# code-porter 文档目录

## 活文档

| 文档 | 类型 | 什么时候读 |
|------|------|------------|
| [产品设计](./product-design.md) | 产品设计 | 确认产品定位、适用场景、能力边界与非目标 |
| [技术架构](./tech-design.md) | 技术设计 | 确认技术选型、模块职责、打包策略与数据模型 |
| [Change Plans 索引](./changes/README.md) | 变更规划 | 查询局部优化、新功能实施计划 |
| [调研与背景](./research/README.md) | 调研 | 查方案对比、备选路径与「为什么这样选」 |

## 默认阅读顺序

- **改产品范围或能力边界**：先读 `product-design.md`
- **改实现、策略或模块结构**：先读 `tech-design.md`
- **做局部优化或新功能**：先在 `changes/active/` 写计划，再实施；完成后移到 `completed/`
- **查分发/包管理等选型背景**：读 `research/`，落地任务仍进 `changes/`

## 文档分层

```text
docs/
├── README.md              # 文档总导航（本文件）
├── product-design.md      # 产品定位、场景、能力边界
├── tech-design.md         # 技术选型、架构、打包策略
├── research/              # 调研结论与方案对比（非默认开工入口）
│   ├── README.md
│   └── distribution-packaging.md
└── changes/
    ├── README.md          # Change Plans 使用规则
    ├── active/            # 正在推进的变更计划
    ├── completed/         # 已完成的变更计划
    └── archived/          # 过时或被替代的历史方案
```

## 设计决策速查

| 问题 | 当前结论 | 主要来源 |
|------|----------|----------|
| 产品定位 | 纯本地代码库导入/导出与工作区迁移工具 | [product-design.md](./product-design.md) |
| 运行方式 | 本地 CLI，无服务端；开发者 `uvx`，普通用户可用预编译/包管理器 | [tech-design.md](./tech-design.md) |
| 用户代码打包策略 | 干净 Git → bundle；脏 Git → bundle + overlay；非 Git → zip | [tech-design.md](./tech-design.md) |
| 本工具如何分发 | PyInstaller Release；Scoop / Homebrew 独立仓；Linux 默认同 brew | [tech-design.md](./tech-design.md) · [调研](./research/distribution-packaging.md) |
| 技术栈 | Python 3.12+ / Typer / Rich / pathspec | [tech-design.md](./tech-design.md) |
