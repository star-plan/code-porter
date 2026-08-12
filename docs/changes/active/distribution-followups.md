---
title: "分发渠道后续完善"
type: plan
status: planning
date: 2026-08-12
description: |
  在已有 PyInstaller Release、Scoop、Homebrew 基础上，按调研路线图逐步增强
  Linux 原生包与签名等能力。背景见 docs/research/distribution-packaging.md。
---

# 分发渠道后续完善

## 一、背景与范围

已落地（见 [跨平台分发与包管理](../../research/distribution-packaging.md)）：

- PyInstaller 多平台二进制 + GitHub Release + `SHA256SUMS`
- Scoop：`star-plan/scoop` 独立仓自同步
- Homebrew：`star-plan/homebrew-tap`（含 Linux）

本 plan 跟踪**尚未完成**的增强项，避免调研结论只留在 research 里无落地。

**不在范围：** 改应用「代码库导出」的 bundle/zip 策略；自建完整 apt 源（明确搁置）。

## 二、任务清单

### P0 — 文档与首个可用包版本

- [ ] 确认 `star-plan/scoop` / `star-plan/homebrew-tap` 已公开且 sync workflow 可用
- [ ] 打出带完整四平台二进制的 tag Release，触发 packaging 仓同步
- [ ] README / Release notes 写明：Linux 可用 `brew tap star-plan/tap`

### P1 — Linux deb / rpm

- [ ] 评估 nfpm（或同类）接入 `publish.yml`
- [ ] 基于已有 `code-porter-linux-*` 生成 `.deb` / `.rpm` 并挂到 Release
- [ ] 文档补充 `dpkg` / `dnf` 安装示例
- [ ] （可选）packaging 仓或 README 交叉链接

### P2 — 信任与开发者渠道

- [ ] Windows 代码签名方案调研与成本评估
- [ ] macOS notarization 可行性
- [ ] （可选）Aqua / mise 安装描述

### P3 — 社区源

- [ ] 用户量足够时考虑 AUR
- [ ] 更长期：Scoop extras / Homebrew 官方路径（单独评估）

## 三、验收标准

1. 普通用户可在 Win 用 Scoop、在 macOS/Linux 用 Homebrew 安装到与最新 tag 一致的版本（允许 packaging cron 短延迟）。
2. P1 完成后：无 brew 的 Linux 用户可仅用系统包工具安装 Release 中的 deb/rpm。
3. 应用仓 git 历史仍**无**因 version bump 产生 Scoop/Homebrew bot commit。

## 四、相关路径

- `docs/research/distribution-packaging.md`
- `packaging/code-porter.spec`
- `.github/workflows/publish.yml`
- https://github.com/star-plan/scoop
- https://github.com/star-plan/homebrew-tap
