---
title: "调研与背景（research）"
type: reference
status: active
date: 2026-08-12
description: |
  调研结论、方案对比与背景材料索引。默认不从这里开工；实现以 tech/product 与 changes 为准。
---

# 调研与背景（research）

默认**不要**从这里开始读。改产品范围先看 [产品设计](../product-design.md)，改实现先看 [技术架构](../tech-design.md)，局部实施先写 [Change Plans](../changes/README.md)。

`research/` 存放**调研结论与方案对比**：记录「为什么这样选、还考虑过什么、后续可完善什么」。结论稳定后，把**当前默认策略**摘要写回 `tech-design.md` / `product-design.md`；细节与备选方案可长期留在本目录。

## 文档列表

| 文档 | 说明 |
|------|------|
| [跨平台分发与包管理](./distribution-packaging.md) | 预编译二进制、Scoop / Homebrew、Linux 包管理选型与路线图 |
| [scan / clean 扫描结果排序](./scan-clean-result-sort.md) | 列表排序 CLI 设计、字段语义、默认行为与 MVP 范围 |

## 维护约定

1. 新调研用 kebab-case 文件名，带 YAML frontmatter（`type: research`）
2. 文首标明 `status`：`draft` / `active` / `superseded`
3. 若结论改变默认发布方式，同步更新 `tech-design.md` 与本索引
4. 可执行的后续工作拆到 `changes/active/`，不要只在 research 里列愿望
