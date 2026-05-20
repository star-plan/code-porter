# code-porter

“开发者工作区迁移 / 项目资产整理 / Git 优先同步器”

## 适用场景

* 多语言技术栈（Python / TS / Go / C#）
* 项目数量多
* 有部分历史项目没规范化
* 有大量 AI 生成代码与实验项目
* 不想污染新 Mac 环境

## 总体架构

```text
鸡哥（Windows）
    ↓ OpenSSH Server
MacBook Pro
    ↓ 自动扫描
分类处理：
    Git仓库 → clone/pull
    非Git项目 → rsync源码
    垃圾目录 → 自动排除
```

## 第一阶段：鸡哥开启 OpenSSH Server

已完成，使用以下命令测试：

```bash
ssh kunkun
```

使用 scp 或 rsync 从 Windows 复制文件，需要以下命令：

```bash
scp "deali@192.168.10.174:/C:/code/temp.txt" ./temp.txt
```

---

# 第二阶段：自动扫描项目

比如扫描：

```text
package.json
pyproject.toml
go.mod
Cargo.toml
*.sln
```

判断：

* 是否 Git 仓库
* 是否有 remote
* 是否存在大目录
* 是否有 node_modules
* 是否值得迁移

甚至输出：

```json
{
  "name": "iugam-memex",
  "type": "node",
  "git": true,
  "remote": true,
  "size": "1.2GB",
  "path": "D:/Projects/iugam-memex"
}
```

---

# 第三阶段：分类迁移（重点）

## Git 优先

检查 git work tree 是否干净：

```bash
git status
```

如果不干净，按非 git 项目处理

## A. 有 Git Remote

git work tree 干净的情况下，直接：

```bash
git clone
```

## B. 本地 Git 但没 Remote

先创建一个临时目录：

```powershell
$TEMP_DIR = "$env:TEMP\migration-bundles"
mkdir $TEMP_DIR -Force
```

自动：

```bash
git bundle create "$TEMP_DIR\[project-name].bundle" --all
```

然后 Mac：

```bash
git clone project.bundle
```

这个比 rsync `.git` 更专业。

很多人不知道 `git bundle`。

它本质是：

> “单文件 Git 仓库备份”

超级适合迁移。

全部迁移完成后，清理临时文件:

```powershell
Remove-Item $TEMP_DIR -Recurse -Force
```

---

## C. 非 Git 项目

使用 scp 或 rsync

排除无用目录，如：

```text
node_modules
.venv
dist
build
target
.next
.cache
```

