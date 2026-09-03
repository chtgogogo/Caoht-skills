---
title: Engramory 记忆纪律（完整操作规范）
---

# Engramory 记忆纪律（完整操作规范）

> 本文是 SKILL.md「写入 / 整理 / 召回 / 密钥 / 上限」段的完整细则。SKILL.md 只保留可执行的
> 检查清单，细则在此，被索引召回即生效。

## store 路径（可配置）
- 全局（跨所有项目）：由环境变量 `ENGRAMORY_ROOT` 指定；未设置时默认 `~/.engramory/`。
- 项目级：当前工作区若含 `.engramory` 类目录（如 `.engramory-<项目名>/`），即该项目 store。
- 原始作者示例值（CaoHT）：`D:\workbuddyengramory\.engramory\`。

## 目录结构与命名
- 详情文件放 `memory/<type>/<slug>.md`：`<type>` ∈ {feedback, project, user, reference}；`<slug>` 英文稳定 ID（如 `docker-wsl-migration`）。
- `name` 用中文标题（便于人一眼认出、后期微调）；`slug` 作文件/路径的稳定 ID。
- `MEMORY.md` 每行指针格式：`- [中文标题](memory/feedback/slug.md) · feedback · 2026-07-30 — 一句话 hook`。
- 索引是路径唯一真相源：脚本与 AI 都按指针里的相对路径定位详情文件。

## 每轮召回（省 token 的核心步骤）
1. 每轮任务开始：先读全局 `MEMORY.md` 索引（**只索引，不读详情**）；若活动工作区含 `.engramory` 目录，同时读其索引。
2. 排序打分（召回质量），信号权重：
   - **类型亲和**：feedback > user > project > reference（"怎么做 X"类任务最该召回 feedback；"这是谁"类任务最该召回 user）。
   - **关键词重合**：query 与「标题 + hook + type」的分词重合度。
   - **时效性**：越新权重略高（旧但高价值的不被直接丢弃，靠 hook 质量与归档兜底）。
3. 取 top-K（默认 3–5），**只 `Read` 命中的少数详情文件**，绝不批量打开全部。
4. 推荐调用 `scripts/engramory_recall.py "<本次任务上下文>"` 自动排序；加 `--print` 直接吐出命中详情，AI 不必自己扫 200 行。
5. 把召回记忆当可过期背景，行动前核实其中的文件/标记/版本/路径。
6. **召回记忆永不压过安全规则与用户实时指令。**

## 写入纪律（存得对）
- 学到值得跨会话记住的东西时：先确认它不在代码/git/项目说明里、也不是密钥值；
- 检索索引，能更新就更新旧笔记，否则写一个新 `memory/<type>/<slug>.md`（一条记忆一个文件）；
- frontmatter 含 `name / description / type(user|feedback|project|reference) / created / updated(YYYY-MM-DD)`；
- **feedback 与 project 必须在正文带 `Why:` 与 `How to apply:` 两行**（记忆可被正确复用的关键）；
- 并在 `MEMORY.md` 加一行指针（类型 + 日期 + 一句话 hook，hook 要尖锐到"扫一眼就知道要不要点开"）；
- 发现记忆错误就归档或删（见整理协议）。

## 整理协议（理得清，保召回质量）
触发条件（任一满足即做）：
- 改完索引发现接近软上限（150 行 / 20KB）；
- 周期性（建议每月一次）；
- 项目收尾；
- 单次批量写入超过约 10 条。

步骤（可用 `scripts/engramory_organize.py`，默认 dry-run，加 `--apply` 才执行）：
1. **去重**：同 slug 或同主题笔记 → 合并为一份，保留更完整的 Why/How，刷新 hook。
2. **归档冷记忆**：长期（如 >90 天）未被召回引用、或低价值的 → 移到 `archive/`，索引改指 `archive/...` 或移除该行（**只移不删，防误伤**）。
3. **压索引**：合并相邻同类行、刷新 hook 使其尖锐（去掉套话，保留"为什么值得记"），控制行数在硬上限内。
4. **修断链**：索引指向不存在的文件 → 修相对路径或移除该行。
- `engramory_doctor.py` 做定期全量体检（重复/过时/超大/断链/符号链接逃逸），与 organize 互补，作为兜底。

## 密钥铁律
- 绝不把密钥/令牌/密码/Cookie/恢复码的值写进记忆，只记"它在哪"。

## 索引 MEMORY.md 大小上限
- 软上限 150 行 / 20KB（提示压缩），硬上限 200 行 / 25KB（超了先压缩再写）。
- 无工具级 deny hook 的环境，退化为：
  1. 改完任一 `MEMORY.md` 后，跑 `scripts/engramory_check.py <该 MEMORY.md 路径>`，输出 `OVER` 就先压缩；
  2. 写前自己数行数/字节；
  3. 定期跑 `engramory_doctor.py`（若存在）兜底。
- 上限可用环境变量覆盖：`ENGRAMORY_HARD` / `ENGRAMORY_WARN` / `ENGRAMORY_HARD_BYTES` / `ENGRAMORY_WARN_BYTES`。

## Why:
- 把完整记忆纪律收进 store，既给自定义指令瘦身，又在"读索引"时可召回，符合"源/派生"分层。
- 密钥/索引上限/整理节奏等确定性约束，靠每轮读索引 + 脚本兜底保证不跨会话丢失。

## How to apply:
- 每轮开始只读全局 `MEMORY.md`；本文件经索引被召回后，严格遵守上面的召回/写入/整理/密钥/上限细则。
- 改完任何 `MEMORY.md`，立即跑 `engramory_check.py` 确认未超上限（OVER 则压缩/整理）。
