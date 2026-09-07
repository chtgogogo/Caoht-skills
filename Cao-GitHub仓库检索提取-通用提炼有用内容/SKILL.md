---
name: github-repo-extract
version: v1.1
description: 检索并提炼 GitHub 仓库有用内容的标准工作流。当用户提供 GitHub
  仓库链接或列表，或要求分析、参考、借鉴、转译、评估某个开源仓库（游戏/Web/AI/工具均可）时触发。覆盖：查询元数据/目录树/README → 5
  维度价值判断（可用/可转译/设计/架构/视觉）→ 按 License 安全下载（MIT/CC0/Apache 可下，GPL/无
  License/Private 受限）→ 沉淀分析文档。内置版权红线：禁止商用素材绝不纳入。优先用 gh CLI 鉴权拉取，规避匿名 API 限流。
metadata:
  agent_created: true
---

# GitHub 仓库检索提取（通用）

> 版本：v1.1 ｜ 通用 SOP，适用于任何工程类型。
>
> 天倾项目专用版见技能 `github-repo-extract-tianqing`（项目配置文件，不随本通用版发布）。
>
> 若你的环境有本 skill 的多份副本，任一改动后请整体同步其余副本并 sha256 校验，避免漂移。

## 触发条件
用户提供 GitHub 仓库链接或列表，或明确要求"分析 / 参考 / 借鉴 / 转译 / 评估"某仓库。

## 工作流

### 1. 识别工程上下文
先确认当前工程：类型、技术栈（project.godot / package.json / requirements.txt / Cargo.toml）、文档体系（是否双文档 + DOCS_INDEX）、本地目录约定（third_party/ reference_repos/ docs/）。
若无文档体系，建议先建一个 DOCS_INDEX.md + 架构总览，再开始分析，否则分析成果无法沉淀。

### 2. 查询阶段
**拉取方式：优先 `gh` CLI（已登录鉴权、无匿名 60 次/小时限流）；`api.github.com` 直连仅作兜底。**

| 字段 | 推荐命令（gh，鉴权） | 兜底 API 路径 |
|---|---|---|
| 元数据 | `gh repo view {o}/{r} --json stars,licenseInfo,primaryLanguage,description,repositoryTopics` | /repos/{o}/{r} |
| 目录树 | `gh api repos/{o}/{r}/git/trees/HEAD?recursive=1` | /repos/{o}/{r}/git/trees/HEAD?recursive=1 |
| README | `gh api repos/{o}/{r}/readme -H "Accept: application/vnd.github.raw"` | /repos/{o}/{r}/readme |
| 关键源码 | `gh api repos/{o}/{r}/contents/{p} --jq '.content' \| base64 -d` | /repos/{o}/{r}/contents/{p} |

- **兜底直连**：GitHub 域名被 hosts 拦截 / gh 不可用时，用 Python `urllib.request` 直连 `api.github.com`（绕过 DNS）。
- **文件内容解码落盘**：`contents/{path}` 与 `readme` 接口返回的 `content` 为 base64 字符串。用 `gh api ... --jq '.content' | base64 -d > 本地路径`，或用 Python `base64.b64decode` 后写文件；**切勿把 base64 原文当源码落盘**。大仓库 trees 递归可能很大，按需取关键路径即可。
- 判断价值等级：★★★★★（核心参考）→ ★★★★（重要参考）→ ★★★（有借鉴点）→ ★★（边缘相关）→ ★（存档查阅）→ ☆（无用）。

### 3. 价值判断矩阵（5 维度）

| 维度 | 含义 | 游戏示例 | Web 示例 | AI 示例 |
|---|---|---|---|---|
| ① 可直接使用 | 资产/插件/配置立即可用 | shader、粒子预设、CC0 素材 | 组件库、CSS 框架 | 预训练模型、prompt 模板 |
| ② 可提取知识/转译 | 跨语言/框架设计模式移植 | C#→GDScript | Java→Python | PyTorch→TensorFlow |
| ③ 设计提炼 | 核心机制/算法/架构决策 | Roguelite 循环、难度曲线 | 状态管理、路由 | 模型架构、训练策略 |
| ④ 架构分层 | 目录结构/模块划分 | autoload/entities/ui | MVC/MVVM | 数据→模型→服务→API |
| ⑤ 视觉/规范 | UI 模式/设计语言 | 粒子参数、调色规律 | 组件规范 | 可视化规范 |

### 4. 下载决策矩阵（License 红线）

| 条件 | 决策 | 存放 |
|---|---|---|
| MIT / Apache / CC0 / 公共领域 | 可全量下载，自由参考修改 | {工程}/third_party/ 或 reference_repos/ |
| GPL-3.0 / AGPL | **只下 README 学方法论，不下载源码，不抄码转译** | 文档标注警告 |
| 无 License | 只下骨架学方法论，标注「无 License，不可抄码」 | reference_repos/ 加 _NO_LICENSE 后缀 |
| 作者标注 Private/保密 | 只参考公开设计理念，**不下载任何文件** | 仅记录思路 |
| 仓库 >100MB | 选择性下载关键文件，不 clone 全库 | 按需 |
| 纯资源索引/文档 | 不下载，在线浏览 | 文档记 URL |

> ⚠️ 版权红线（用户硬规矩）：禁止商用（CC-BY-NC 等）素材绝不纳入工程；外部参考代码保留所有权利的，仅作只读参考，不整段照搬进可发布构建。
>
> 📌 License 检测补充：API `licenseInfo` 字段常因仓库仅在根目录放 `LICENSE`/`COPYING`/`LICENSE.md` 文件而返回 null。当 `licenseInfo` 为 null 时，**额外检查仓库根目录是否存在上述文件**再判定，不要直接当「无 License」处理。

**下载命名规则**：前缀标来源 `{Repo缩写}_{原名}`，同仓库统一前缀（VS_=vampire-survivors，HS_=HordeSurvival），保持原扩展名。

### 5. 文档更新
- 双文档体系：同步 AI 侧 + 人看版 + 两份 DOCS_INDEX。
- 单文档：更新主文档 + 索引。
- 无文档：根目录建 REFERENCE_ANALYSIS.md + 在 README 加链接。

对每个有价值仓库至少含：

```
### N. {owner}/{repo}
- Stars / Language / License / Updated
- 价值等级
- 一句话评价
**对本工程价值**：可直接用 / 可学方法 / 设计提炼
**License 警示**（如有）
**本地路径**（如已下载）
```

同类仓库提供不同实现时，追加方法对比表（维度 | 参考仓库方案 | 当前工程方案 | 谁的更好 | 建议）。

### 6. 编码注意
- 中文用 `Python open(path, 'w', encoding='utf-8')` 写；避免 PowerShell 管道传中文（会损坏）。
- 写后验证：中文字符数 > 0 且问号数 = 0。
- 文件路径尽量用英文，避免中文路径跨平台兼容问题。

### 7. 完成检查清单
- [ ] 所有仓库 License 已检查并标注（含 licenseInfo 为 null 时查根目录文件）
- [ ] 应下载文件已下载到正确目录（内容为解码后的真实源码，非 base64 原文）
- [ ] 文档已同步到所有必须更新位置
- [ ] DOCS_INDEX 已更新，快速导航已添加
- [ ] 涉及代码对比的已追加对比章节
- [ ] 中文内容已通过编码验证（0 个问号）
