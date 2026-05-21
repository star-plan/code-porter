# code-porter

“开发者工作区迁移 / 项目资产整理 / Git Bundle 导入导出器”

## 适用场景

* 多语言技术栈（Python / TS / Go / C#）
* 项目数量多
* 有部分历史项目没规范化
* 有大量 AI 生成代码与实验项目
* 不想依赖 Windows OpenSSH 的复杂兼容性

## 总体架构

```text
Windows 工作机
    ↓ 本地扫描与导出
    Git 仓库 → git bundle
    非 Git 项目 → zip
    脏 Git 仓库 → bundle + overlay zip
    ↓ 拷贝导出目录
MacBook Pro
    ↓ 本地导入
    bundle → git clone
    zip → 解压恢复
```

## 第一阶段：Windows 本地扫描项目

扫描规则：

- 查找常见项目标记：

```text
package.json
pyproject.toml
go.mod
Cargo.toml
*.sln
```

- 判断：

```text
是否 Git 仓库
是否有 remote
是否有未提交改动
是否存在大目录
是否命中默认垃圾目录
是否值得导出
```

---

# 第二阶段：本地导出备份包

## A. 干净 Git 仓库

直接导出 bundle：

```bash
git bundle create project.bundle --all
```

manifest 里记录：

```json
{
    "name": "land-go",
    "package_kind": "bundle",
    "package_path": "artifacts/land-go-xxxx.bundle"
}
```

## B. 脏 Git 仓库

除了 bundle，再额外导出当前工作区 overlay zip：

```text
project.bundle
project.worktree.zip
```

这样既保留 Git 历史，也保留未提交文件。

## C. 非 Git 项目

导出 zip。

zip 打包时：

* 优先读取项目根目录 .gitignore
* 叠加默认排除目录

默认排除：

```text
node_modules
.venv
dist
build
target
.next
.cache
.git
```

---

# 第三阶段：跨机器复制导出目录

把整个导出目录复制到 MacBook，例如：

```text
exports/windows-backup/
    manifest.json
    artifacts/
        project-a.bundle
        project-b.worktree.zip
        project-c.zip
```

复制方式不限：

* U 盘
* SMB
* iCloud Drive
* 移动硬盘

---

# 第四阶段：Mac 本地导入

## A. bundle

```bash
git clone project.bundle
```

## B. bundle + overlay zip

先 clone bundle，再把 overlay zip 解压覆盖到工作区。

## C. zip

直接解压到目标目录。

---

# CLI 设计

## scan

本地扫描项目并输出 JSON：

```bash
uv run code-porter scan C:/code/1 --json-output reports/scan.json
```

## export

本地扫描并导出备份包：

```bash
uv run code-porter export C:/code/1 ./exports/windows-backup
```

## import

从 manifest 导入到 Mac 目标目录：

```bash
uv run code-porter import ./exports/windows-backup/manifest.json ~/code/imported
```

