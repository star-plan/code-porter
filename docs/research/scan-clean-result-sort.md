---
title: "scan / clean 扫描结果排序"
type: research
status: active
date: 2026-08-12
description: |
  调研 scan、clean 结果列表的当前排序行为，以及增加用户可控排序的 CLI 设计与实现建议。
---

# scan / clean 扫描结果排序

## 一、背景

`scan` 与 `clean` 已支持筛选（`--status` / `--profile`）和表格展示，但结果顺序固定，用户无法按体积、名称等维度重排。迁移/清理场景里，**按体积找大户**、**按类型/策略分组扫一眼**是高频诉求。

本文只做方案调研，不直接改代码。落地时请另建 `docs/changes/active/` 计划。

## 二、当前行为（代码事实）

### 2.1 `scan`

| 环节 | 行为 | 位置 |
|------|------|------|
| 发现项目后 | 按 `Path` 字典序：`sorted(project_roots)` | `scanner.scan_local_roots*` |
| `--status` 过滤后 | 保持过滤前相对顺序 | `cli._filter_reports_by_status` |
| 表格 / JSON | 直接使用上述顺序 | `cli.scan` |

可用排序字段（`ProjectReport`）：

- `name` / `path`
- `project_type`
- `size_bytes`（表格显示 `size_human`）
- `is_git_repo` / `has_remote` / `is_clean`
- `packaging_strategy` / `worth_exporting`

### 2.2 `clean`

| 环节 | 行为 | 位置 |
|------|------|------|
| 发现 targets 后 | `sort(key=(profile, project_name.lower(), path.lower()))` | `cleaner.discover_clean_targets` |
| profile 顺序 | **字符串字典序** → `build` → `cache` → `deps` | 与 `PROFILE_ORDER = (deps, cache, build)` **不一致** |
| 表格 / JSON | 使用 plan 内顺序 | `cli.clean` / `CleanPlan` |

可用排序字段（`CleanTarget`）：

- `size_bytes`（回收体积，对 clean 最有价值）
- `project_name` / `path` / `name`
- `profile`（deps / cache / build）
- apply 后还有 `status`（deleted / failed / …）

### 2.3 其它命令

- `check`：与 scan 相同，按 path 排序；本次可列为可选扩展。
- `export`：扫描后按 path 顺序导出；排序主要影响展示与进度条顺序，**不改变打包语义**，可后做。

## 三、用户场景与价值

| 场景 | 命令 | 期望排序 | 价值 |
|------|------|----------|------|
| 找最大的项目，决定是否先 clean | scan | size 降序 | 高 |
| 找最能腾空间的垃圾目录 | clean | size 降序 | **最高** |
| 按语言扫一眼 | scan | type | 中 |
| 先看脏仓库 / 无 remote | scan | 已有 `--status`，排序为补充 | 中（筛选优先） |
| 按打包策略分组 | scan | package / strategy | 中 |
| 按项目名浏览 | 两者 | name | 低（接近现状） |

结论：**clean 按 size 降序收益最大**；scan 按 size / name / type / package 覆盖主要浏览需求。`--status` 已覆盖「只看某类」，排序不必再做复杂的 git 复合键（可作为 v2）。

## 四、业界 CLI 惯例（简要）

| 工具 | 模式 |
|------|------|
| `ls` | 短选项 `-S`（size）、`-t`（time）；方向用 `-r` |
| `du` + `sort -h` | 管道外置排序 |
| `gh` / 部分现代 CLI | `--sort field` + `--order asc\|desc` |
| `docker images` | `--format` 多，排序相对弱 |

对本工具更贴合的是 **显式字段名 + 可选反向**（与现有 `--status` 命名风格一致），而不是只学 `ls` 的一字母魔法。

## 五、推荐 CLI 设计

### 5.1 选项形态（推荐）

```text
--sort KEY          # 按字段排序
--reverse / -r      # 反转方向
```

- 短选项：`--sort` 可挂 `-S`（注意与 scan 的 `-s/--status` 不冲突）。
- **不要**用 `-size` / `size:desc` 混合语法作为第一版；解析成本高、help 难读。
- 未知 `KEY` → exit 2，错误文案列出允许值（对齐 `--status` / `--profile`）。

### 5.2 `scan` 的 KEY

| KEY | 主键 | 默认方向 | 次要键（稳定排序） |
|-----|------|----------|-------------------|
| `path` | `path`（大小写不敏感） | asc | —（**保持接近现状**） |
| `name` | `name.lower()` | asc | `path` |
| `size` | `size_bytes` | **desc** | `name` |
| `type` | `project_type.value` | asc | `name` |
| `package` | `packaging_strategy.value` | asc | `name` |
| `export` | `worth_exporting`（True 先） | desc-ish | `name` |

说明：

1. **默认不传 `--sort` 时行为不变**（仍 path 序），避免破坏脚本/快照测试。
2. `size` 的**字段默认方向为降序**（大项目在上）；若用户再加 `--reverse`，则变为升序。即：`--sort` 的「自然方向」按字段语义，不是永远 asc。
3. 实现时用「base_reverse」：`effective_reverse = field_default_desc XOR user_reverse`。

可选 v2（不做第一版）：

- `git`：复合键（non-git / dirty / no-remote / clean）
- 多键：`--sort size,name`（复杂，收益有限）

### 5.3 `clean` 的 KEY

| KEY | 主键 | 默认方向 | 次要键 |
|-----|------|----------|--------|
| `profile` | `PROFILE_ORDER` 索引 | asc（deps→cache→build） | size desc, path |
| `size` | `size_bytes` | **desc** | path |
| `project` | `project_name.lower()` | asc | size desc |
| `name` | 目录 basename | asc | size desc |
| `path` | path | asc | — |

说明：

1. 发现阶段可继续固定内部顺序；**展示/JSON 前**在 CLI 层再 sort，避免污染 apply 顺序语义（apply 顺序对结果正确性无硬性要求，但保持确定性更好）。
2. 修正「profile 用字符串排序 ≠ PROFILE_ORDER」可作为排序落地时的**默认排序小改进**：仅当用户未指定 `--sort` 时，改为按 `PROFILE_ORDER` + size desc + path。这是轻微行为变化，应在 change plan 中写清，并更新单测期望。
3. 若希望更激进：clean **默认**改为 `size` 降序。推荐分两步——第一版默认只修正 profile 序；默认 size 作为可选 follow-up，避免一次改太多。

### 5.4 与 JSON / 筛选的关系

```text
scan:  discover → filter(--status) → sort(--sort) → render / --json / --json-output
clean: discover → (preview all) → filter profiles → sort → render / apply / json
```

- **JSON 应与表格同一顺序**，方便管道与人读一致。
- 排序在 filter 之后，只对「最终展示集合」生效。
- summary 聚合与顺序无关，无需改。

### 5.5 不建议做的事

1. **交互式点表头排序**（Rich 表非 TUI）——超出当前 CLI 产品边界。
2. **依赖用户 `| sort`**——跨平台与 `size_human`（`1.2GB`）不友好。
3. **export 强制按 size 导出**——无产品收益，增加 diff 噪音。

## 六、实现草图（供 change plan 引用）

建议全部放在 `cli.py` 表现层（或抽 `sorting.py` 若单测希望纯函数、无 Rich）：

```python
SCAN_SORT_CHOICES = ("path", "name", "size", "type", "package", "export")
CLEAN_SORT_CHOICES = ("profile", "size", "project", "name", "path")

def sort_project_reports(
    reports: list[ProjectReport],
    key: str | None,
    *,
    reverse: bool = False,
) -> list[ProjectReport]:
    """Sort scan reports; None key keeps input order."""
    ...

def sort_clean_targets(
    targets: list[CleanTarget],
    key: str | None,
    *,
    reverse: bool = False,
) -> list[CleanTarget]:
    """Sort clean targets; None key keeps input order."""
    ...
```

Typer 选项示例：

```python
sort_by: str | None = typer.Option(
    None,
    "--sort",
    "-S",
    help=f"Sort results by field. Choices: {', '.join(SCAN_SORT_CHOICES)}",
    case_sensitive=False,
)
reverse: bool = typer.Option(False, "--reverse", "-r", help="Reverse sort order"),
```

`size` 等「默认降序」字段：

```python
DESC_DEFAULT_KEYS = {"size"}  # scan; clean 同理可含 size
want_desc = (key in DESC_DEFAULT_KEYS) ^ reverse
```

## 七、测试要点

1. `scan --sort size`：较大 `size_bytes` 的项目排在前面；`--reverse` 反过来。
2. `scan --sort name --status dirty`：先 filter 再 sort；只含 dirty。
3. `scan --sort weird` → exit 2 + allowed list。
4. `scan --json --sort size`：JSON 数组顺序与 sort 一致。
5. `clean --sort size --no-interactive`：最大 target 在上。
6. `clean --sort profile`：deps → cache → build（`PROFILE_ORDER`）。
7. 默认无 `--sort`：scan 仍 path 序；clean 若改默认 profile 序需断言 PROFILE_ORDER。

## 八、方案对比

| 方案 | 优点 | 缺点 | 建议 |
|------|------|------|------|
| A. `--sort` + `--reverse` | 清晰、可扩展、对齐 `--status` | 比单字母稍长 | **采用** |
| B. 仅 `-S`/`-t` 风格 | 短 | 字段少、难扩展 | 不单独采用 |
| C. 默认改 size 降序、无 flag | 零学习 | 破坏兼容与测试 | 不作为唯一手段 |
| D. 管道外置 sort | 零开发 | 人读 size 列不可靠 | 不推荐 |

## 九、推荐落地范围（MVP）

**做：**

1. `scan`：`--sort` / `-S` + `--reverse` / `-r`，KEY：`path|name|size|type|package|export`
2. `clean`：同样选项，KEY：`profile|size|project|name|path`
3. JSON 与表格共用排序
4. 单测覆盖主路径与非法 KEY
5. README + `--help` 更新

**可选同 PR：**

- clean 默认排序改为 `PROFILE_ORDER` + 同 profile 内 size desc（小改进）

**暂缓：**

- `check` / `export` 排序
- 复合 git 键、多键 sort
- 把 clean 全局默认改成 size（可观察用户反馈后再定）

## 十、相关路径

- `src/code_porter/cli.py` — 选项、filter 后 sort、render
- `src/code_porter/scanner.py` — 当前 path 排序（可不动）
- `src/code_porter/cleaner.py` — 当前 profile 字符串排序
- `src/code_porter/models.py` — `ProjectReport` 字段
- `tests/test_scan_cli.py` / `tests/test_clean.py`
- `README.md` — 用法示例

## 十一、结论

1. **需求成立**：排序是 scan/clean 列表 UX 的自然下一刀，与已有筛选互补。
2. **API**：`--sort KEY` + `--reverse`；`size` 默认大到小。
3. **实现层**：CLI 表现层排序即可，不必改扫描算法。
4. **clean 默认 profile 序**存在小 bug/不一致（字典序 vs `PROFILE_ORDER`），可一并修。
5. 下一步：在 `docs/changes/active/` 建 plan 后实施 MVP。
