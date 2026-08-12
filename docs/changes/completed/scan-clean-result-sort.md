---
title: "scan / clean 扫描结果排序"
type: plan
status: completed
date: 2026-08-12
description: |
  为 scan / clean 增加 --sort / --reverse，并修正 clean 默认 profile 顺序。
---

# scan / clean 扫描结果排序

## 一、背景与范围

基于 [research/scan-clean-result-sort.md](../../research/scan-clean-result-sort.md) 实现 MVP：

- `scan` / `clean`：`--sort` / `-S` + `--reverse` / `-r`
- JSON 与表格同一顺序
- clean 默认排序改为 `PROFILE_ORDER` + 体积降序

## 二、任务清单

- [x] `cli.sort_project_reports` / `sort_clean_targets`
- [x] `scan` / `clean` 接入选项与校验
- [x] `cleaner.discover_clean_targets` 默认序修正
- [x] 单测（size、reverse、filter 后 sort、非法 KEY、clean profile）
- [x] README + tech-design 更新

## 三、验收标准

1. `scan --sort size` 大项目在前；`--reverse` 反过来
2. `scan --status dirty --sort name` 先筛再排
3. 未知 sort key → exit 2
4. `clean --sort size` / 默认 profile 序符合 `PROFILE_ORDER`
5. `--json` 顺序与表格一致

## 四、相关路径

- `src/code_porter/cli.py`
- `src/code_porter/cleaner.py`
- `tests/test_scan_cli.py`
- `tests/test_clean.py`
- `README.md`
- `docs/tech-design.md`
