---
title: "跨平台分发与包管理"
type: research
status: active
date: 2026-08-12
tags:
  - distribution
  - packaging
  - scoop
  - homebrew
  - pyinstaller
  - release
description: |
  面向普通用户与开发者的安装路径调研：PyInstaller 预编译、Scoop 独立 bucket、
  Homebrew tap、Linux 包管理选项、以及后续可完善的路线图。
---

# 跨平台分发与包管理

> 状态：`active` 调研结论已指导首轮落地；细节与备选方案随发布体系迭代补充。  
> 关联活文档：[技术架构](../tech-design.md) · 变更计划见 [changes](../changes/README.md)

## 1. 背景与目标

### 1.1 问题

- 开发者路径：`uvx code-porter` / PyPI 很方便，但依赖 [uv](https://docs.astral.sh/uv/) 或 Python 环境。
- 普通用户：希望**下载即可用**，或通过本机已有**包管理器**安装，不想先装 Python/uv。
- 本工具为**纯本地 CLI**，依赖系统 `git`（刻意不内嵌 libgit2），分发物不能替代 Git 安装。

### 1.2 目标分层

| 受众 | 期望体验 | 渠道 |
|------|----------|------|
| 开发者 | 一行命令、可跟进 PyPI 版本 | `uvx` / `pip` / 源码 `uv run` |
| 普通用户（Win） | 包管理器或单文件 exe | Scoop / GitHub Release |
| 普通用户（macOS） | 包管理器或单文件二进制 | Homebrew / GitHub Release |
| 普通用户（Linux） | 尽量包管理器；否则二进制或 deb/rpm | Homebrew（Linux）/ Release / 后续 deb·rpm |
| 维护者 | 发版不污染应用仓 git 历史 | 独立 packaging 仓自同步 |

### 1.3 与「仓库迁移打包策略」的区分

本文件讨论的是 **code-porter 自身如何发布给用户**。

`tech-design.md` 中的「打包策略」（bundle / zip / overlay）是指**用户代码库**的导出格式，二者勿混淆。

---

## 2. 预编译二进制（standalone）

### 2.1 为何需要

`uvx` 对 Python 生态友好，但对「电脑里没有 uv/Python」的用户仍有门槛。  
目标：在 Windows / Linux / macOS 提供可直接运行的控制台程序，由 GitHub Actions 构建并挂到 GitHub Release。

### 2.2 方案对比

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| **PyInstaller**（`--onefile`） | 成熟、资料多、纯 Python CLI 易打通；GHA matrix 成熟 | 不能交叉编译；one-file 冷启动略慢；未签名易触发 SmartScreen/Gatekeeper | **首选** |
| Nuitka | 真编译、部分场景更快 | CI 慢、依赖 C 编译器、调试成本高 | 本工具 I/O + git 子进程为主，收益有限 |
| PyApp | bootstrap 小 | **首跑需联网**拉 Python/依赖，不符合「下载即离线可用」 | 不作主路径 |
| cx_Freeze | 可用 | one-file 与生态叙事弱于 PyInstaller | 备选 |
| zipapp / shiv / pex | 轻 | 仍需本机 Python | 不满足普通用户 |
| PyOxidizer 等 | — | 维护面变窄 | 不推荐新上 |

### 2.3 已采纳做法

| 项 | 选择 |
|----|------|
| 工具 | PyInstaller + `packaging/code-porter.spec` |
| 形态 | `--onefile` + `console=True`（保证 questionary / Rich TTY） |
| 入口 | `src/main.py` → `code_porter.cli:main` |
| CI | tag `v*.*.*` → matrix 构建 → `SHA256SUMS` → GitHub Release |
| 本机预览 | `uv sync --group dev` + `uv run pyinstaller --noconfirm --clean packaging/code-porter.spec` |

### 2.4 Release 资产命名（约定）

| 平台 | 资产名 |
|------|--------|
| Windows x64 | `code-porter-windows-amd64.exe` |
| Linux x64 | `code-porter-linux-amd64` |
| macOS Intel | `code-porter-macos-amd64` |
| macOS Apple Silicon | `code-porter-macos-arm64` |

校验文件：`SHA256SUMS`（`sha256sum` 风格：`hash  filename`）。

### 2.5 平台现实约束

| 平台 | 现象 | 对策 |
|------|------|------|
| Windows | SmartScreen / 杀软误报 | 文档说明绕过；中长期代码签名 |
| macOS | Gatekeeper | 右键打开；中长期 notarize |
| Linux | glibc 绑定构建 runner | 选用较稳 runner（如 ubuntu-22.04）；文档 `chmod +x` |
| 全平台 | 仍需系统 **Git** | README / Release notes 写清前置条件 |
| 全平台 | CLI 双击无意义 | 引导终端运行，而非 GUI 双击 |

### 2.6 刻意不做

- 不把系统 `git` 打进二进制（与技术设计一致）。
- 不为「更小体积」过早上 Nuitka；有明确性能/体积证据再评估。

---

## 3. Scoop（Windows）

### 3.1 痛点（历史对照：ship 本仓 bucket）

曾在应用仓内维护 `bucket/*.json`，发版 workflow **回写 version/hash 并 push 本仓**，导致：

- 主仓多出 bot commit，本地频繁被迫 `pull`
- 包索引历史与产品代码历史绑在一起
- 不符合 Scoop「bucket 即独立 Git 仓库」的常见模型

### 3.2 结论：独立 Scoop bucket 仓

| 项 | 选择 |
|----|------|
| 仓库 | [`star-plan/scoop`](https://github.com/star-plan/scoop) |
| 清单位置 | `bucket/ship.json`、`bucket/code-porter.json` 等 |
| 用户命令 | `scoop bucket add star-plan https://github.com/star-plan/scoop` → `scoop install code-porter` |
| 版本更新 | **packaging 仓自同步**（schedule + workflow_dispatch + 可选 `repository_dispatch`），读各应用最新 GitHub Release + checksum |
| 应用仓 | **不**再 commit Scoop manifest；可选 `PACKAGING_TOKEN` 发版后 dispatch 立即同步 |

### 3.3 为何不「只写 checkver、不提交 hash」

Scoop 安装读的是 bucket 里**当前** JSON 的 `version` + `hash`。  
`checkver` / `autoupdate` 只是维护者/机器人的更新辅助，**不能**让用户在无更新清单时装到最新版。  
因此必须有人或 bot 改 JSON——关键是**改在独立仓**，而不是应用仓。

### 3.4 与官方 bucket

提交 `main` / `extras` 可作长期目标（审核慢、标准严），**不能替代**自有 bucket 的快速发版通道。

### 3.5 清单要点（code-porter）

- `architecture.64bit.url` 使用 `#/code-porter.exe` 重命名
- `depends: git`（Scoop 依赖）
- `autoupdate.hash.url` 指向 Release 的 `SHA256SUMS`
- 无带二进制的 GitHub Release 前，manifest 可为占位版本；首个完整 Release 后由 sync 写入真 hash

---

## 4. Homebrew（macOS / Linux）

### 4.1 仓库

| 项 | 选择 |
|----|------|
| 仓库 | [`star-plan/homebrew-tap`](https://github.com/star-plan/homebrew-tap) |
| Tap 名 | `star-plan/tap`（GitHub 仓库名 `homebrew-tap` 的映射） |
| 用户命令 | `brew tap star-plan/tap` → `brew install code-porter` |
| 同步方式 | 与 Scoop 类似：tap 内 workflow 从最新 Release + `SHA256SUMS` 重写 Formula |

### 4.2 Linux 上的 Homebrew

**Homebrew 在 Linux 上可用**（常称 Linuxbrew）。  
Formula 中写了 `on_linux` 后，与 macOS **共用同一 tap**，无需为 Linux 再维护一套「Homebrew 以外」的默认路径。

文档应明确写出：

```bash
# macOS / Linux（Homebrew）
brew tap star-plan/tap
brew install code-porter
```

避免用户误以为 brew 仅限 macOS。

### 4.3 Formula 形态

- 当前 Release 为**裸二进制**（非 tar.gz）时，`url` 直链资产，`bin.install "asset-name" => "code-porter"`
- `depends_on "git"`
- 多 arch：`on_macos` / `on_linux` × `on_arm` / `on_intel`

### 4.4 与 GoReleaser 的关系

`wechat-clone` 等可用 GoReleaser 直接推 Formula；code-porter 为 Python + PyInstaller，采用 **tap 仓脚本同步** 与 ship 对齐即可。

---

## 5. Linux 包管理（除 Homebrew 外）

Linux 没有统一的「一个官方包管理器」。在已有 **GH Release 多架构二进制 + Homebrew tap** 的前提下，按投入排序如下。

### 5.1 总表

| 方案 | 用户体验 | 维护成本 | 与现有 Release 契合 | 建议 |
|------|----------|----------|---------------------|------|
| **Homebrew (Linux)** | `brew install …` | **已有** | 已有 linux 资产 | **默认 Linux 包管理路径** |
| **Release 附带 .deb / .rpm** | `dpkg -i` / `dnf install ./x.rpm` | 中（nfpm 等） | 高 | **P1 后续完善** |
| **Aqua** | `aqua g -i …` | 低～中 | 极适合 GH Release | 开发者向，可选 |
| **mise / asdf 插件** | `mise use -g …` | 中 | 适合 | 已用 mise 的受众 |
| **AUR** | `yay -S …` | 中 | 可以 | 仅 Arch |
| **自建 apt/yum 源** | `apt install code-porter` | 高（签名/托管） | 要额外基建 | 暂不值得 |
| **Snap / Flatpak** | `snap install` | 中高 | 可以 | CLI 偏重，不优先 |
| **curl \| sh** | 一行脚本 | 低 | 简单 | 可作补充，不作主推 |

### 5.2 为何暂不上完整 apt 源

- 需要长期托管、GPG 签名、多发行版适配。
- 发版勤的小 CLI 维护比不划算。
- **deb/rpm 单文件挂在 Release** 已覆盖「用系统包工具装本地包」的大部分运维场景。

### 5.3 .deb / .rpm（后续方向）

思路：对已有 `code-porter-linux-*` **再包一层**（nfpm / fpm），不重编业务逻辑。

```text
Release 可增加：
  code-porter_*_amd64.deb
  code-porter-*.x86_64.rpm
```

用户：

```bash
sudo dpkg -i code-porter_*_amd64.deb
sudo dnf install ./code-porter-*.rpm
```

验收与任务应落到 `changes/active/`，不在此文当作已完成能力。

### 5.4 Aqua（可选）

[Aqua](https://aquaproj.github.io/) 从 GitHub Release 装 CLI，与 Scoop 心智接近。  
适合「一台机器装很多 CLI」的开发者；可自建 registry 或向上游 registry 贡献。

### 5.5 不推荐作主路径

- Snap / Flatpak：沙箱与体积对单文件 CLI 不划算。
- 仅安装脚本：安全叙事弱，可文档备选，不替代包管理器。

---

## 6. 渠道总览与职责边界

```text
                    tag vX.Y.Z
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
      PyPI            GitHub          （应用仓）
   uvx / pip          Release         不写 scoop/brew 清单
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
     star-plan/scoop        star-plan/homebrew-tap
     (Windows Scoop)        (macOS + Linux brew)
              │                     │
              └──────────┬──────────┘
                         ▼
              schedule / dispatch 自同步
              读 Release 资产 + SHA256SUMS
```

| 仓库 | 职责 |
|------|------|
| `star-plan/code-porter` | 源码、测试、PyPI、构建二进制、创建 Release |
| `star-plan/scoop` | Scoop manifests + sync workflow |
| `star-plan/homebrew-tap` | Homebrew formulas + sync workflow |
| `heyoungai/ship` 等 | 同样只发 Release；清单不进应用仓 |

### 6.1 `PACKAGING_TOKEN`（可选）

- 应用仓发版后可对 scoop / homebrew-tap 发 `repository_dispatch`（`app-release`）。
- **未配置**时 packaging 仓仍按 cron 拉取，最终一致，仅延迟约数小时。
- Token 需对上述 packaging 仓具备写 contents（及触发 workflow 所需权限）。

---

## 7. 当前默认策略（摘要）

稳定结论，应与 [tech-design.md](../tech-design.md) 选型表一致：

1. **开发者**：PyPI + `uvx code-porter`
2. **预编译**：PyInstaller one-file，GHA 多平台，Release + `SHA256SUMS`
3. **Windows 包管理**：Scoop → `star-plan/scoop`（独立仓）
4. **macOS / Linux 包管理（默认）**：Homebrew → `star-plan/tap`
5. **Linux 增强（未完成）**：Release 增加 deb/rpm；视需求再考虑 Aqua / AUR
6. **应用仓不提交** Scoop/Homebrew 版本回写，避免污染 git 历史

---

## 8. 后续完善路线图

按投入与收益排序，实施时请拆到 `changes/active/`：

| 优先级 | 项 | 说明 |
|--------|----|------|
| P0 | 文档写清 Linux 可用 Homebrew | 低成本，避免认知偏差 |
| P0 | 完整二进制 Release + packaging 仓 sync | 用户 `scoop`/`brew` 可装最新版 |
| P1 | Release 产出 `.deb` / `.rpm` | 服务器与无 brew 用户 |
| P2 | Windows 代码签名 / macOS 公证 | 减少系统拦截 |
| P2 | Aqua / mise 描述（可选） | 开发者工具链 |
| P3 | AUR | Arch 用户多时再做 |
| P3 | 官方 Scoop extras / Homebrew core | 用户量与成熟度够再考虑 |
| 搁置 | 自建完整 apt/yum 源、Snap 主路径 | 成本高、收益不确定 |

---

## 9. 相关路径

| 路径 / 仓库 | 说明 |
|-------------|------|
| `packaging/code-porter.spec` | PyInstaller 规格 |
| `.github/workflows/publish.yml` | 测试、二进制、PyPI、Release、可选 notify |
| `.github/workflows/build-binaries.yml` | PR/手动二进制冒烟 |
| [star-plan/scoop](https://github.com/star-plan/scoop) | Scoop bucket |
| [star-plan/homebrew-tap](https://github.com/star-plan/homebrew-tap) | Homebrew formulas |
| 本文件 | 分发调研结论与备选方案 |

---

## 10. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-08-12 | 初版：汇总 PyInstaller、Scoop 独立仓、Homebrew tap、Linux 包管理调研与路线图 |
