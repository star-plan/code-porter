---
title: "Change Plans 索引"
type: reference
status: active
date: 2026-08-12
description: |
  说明 code-porter 中什么样的工作应进入 changes/，以及 active/completed/archived 的使用方式。
---

# Change Plans 索引

`docs/changes/` 用来承载**局部变更计划**：先落到文档，再实施。

适用于：

1. 值得追踪的局部优化或新能力
2. 跨多个模块的机制调整
3. 不足以单独成「产品大版本叙事」，但不能只留在聊天记录里的改动

## 当前 active plans

| 文档 | 状态 | 定位 |
|------|------|------|
| [distribution-followups.md](./active/distribution-followups.md) | planning | 分发渠道后续：deb/rpm、签名、文档与 packaging 仓完善 |

## 已完成 plans

| 文档 | 完成日期 | 定位 |
|------|----------|------|
| [dotnet-bin-obj-clean.md](./completed/dotnet-bin-obj-clean.md) | 2026-08-21 | clean：仅当邻居有 .NET 工程文件时回收 bin/obj |
| [scan-clean-result-sort.md](./completed/scan-clean-result-sort.md) | 2026-08-12 | scan/clean 结果 `--sort` / `--reverse` |

## 已归档 plans

过时或已被后续计划替代的历史方案，保留查阅，不再作为待办。

| 文档 | 归档日期 | 说明 |
|------|----------|------|
| （暂无） | — | 移入 `archived/` |

## 目录约定

```text
changes/
├── README.md
├── active/        # 正在推进的 change plan
├── completed/     # 已完成的 change plan
└── archived/      # 过时或被替代的历史方案
```

## 什么时候使用 changes/

**适合：**

1. 调整扫描策略、打包规则、clean profile、manifest 字段等
2. 新增子命令或明显改变 CLI 行为
3. 需要几轮实现和验证、值得保留背景与验收标准的改动

**不适合：**

1. 产品定位或长期架构真相的改写 → 直接更新 `product-design.md` / `tech-design.md`
2. 一次性很小的文案或单测修补 → 直接改代码即可
3. 已经稳定的长期规则摘要 → 沉淀进 tech/product 设计文档正文

## 维护规则

1. 新计划先放到 `active/`，文件名用 kebab-case，例如 `scan-status-filter.md`
2. 文档开头使用 YAML frontmatter（`title` / `type: plan` / `status` / `date`）
3. 正文建议包含：背景与范围、任务清单、验收标准、相关代码路径
4. 完成后移到 `completed/`，并更新本索引表
5. 内容过时或被替代时移到 `archived/`
6. 若 change plan 演化成默认规则，把结论写回 `product-design.md` 或 `tech-design.md`

## 最小模板

新建 `active/{topic}.md` 时可参考：

```markdown
---
title: "变更标题"
type: plan
status: planning
date: YYYY-MM-DD
description: |
  一两句说明范围。
---

# 变更标题

## 一、背景与范围

## 二、任务清单

- [ ] 任务 1
- [ ] 任务 2

## 三、验收标准

1. …

## 四、相关路径

- `src/code_porter/...`
```
