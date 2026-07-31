---
name: engramory-discipline
version: v1.0
description: 文件化长期记忆（Engramory 式）的"存—整—召"全周期纪律。当助手需要：跨会话长期记忆的读写、每轮只读取轻量 MEMORY.md 指针索引（不撑爆 token）、按相关度排序精准召回少量详情文件、把记忆整理/合并/归档以保持召回质量、维护分层全局/项目记忆、或在多 agent 间共享同一份记忆时触发。强调：索引只存指针、详情懒加载、召回排序、定期整理。
metadata:
  agent_created: true
---

# Engramory 记忆纪律（存 · 整 · 召）

把 AI 长期记忆做成**文件化、可检索、可审计、召回高质量、且不撑爆上下文**的知识库。
四大支柱：**存得对、理得清、召得准、指得省**。

## 何时用本 skill
- 每轮任务开始需召回历史记忆（只读 `MEMORY.md` 指针索引）。
- 要把"值得跨会话记住的东西"写入记忆。
- 维护索引、检查大小上限、整理/合并/归档记忆、提升召回质量。
- 分层判断"全局 or 项目"，或多 agent 共享同一份记忆。

## 1. 目录结构（设计好的布局）
```
<root>/                         # ENGRAMORY_ROOT（默认 ~/.engramory/）
  MEMORY.md                     # 索引：只存指针，绝不存正文；每轮唯一必读文件
  memory/                       # 详情文件，按类型分目录
    feedback/  project/  user/  reference/
  archive/                      # 已 Consolidated / 冷记忆（移出热路径，仍可被检索）
  ledger.md                     # 多 agent 同步账本（可选）
```
- 一条记忆 = `memory/<type>/<slug>.md`（slug 英文稳定 ID；name 中文标题便于人认）。
- `MEMORY.md` 每行一个指针：`[中文标题](memory/feedback/slug.md) · feedback · 2026-07-30 — 一句话 hook`。
- **索引是路径唯一真相源**：脚本与 AI 都跟着索引里的相对路径找详情文件，目录怎么组织都不影响定位。

## 2. 指针召回协议（省 token 的核心）
每轮只做三件事：
1. **读 `MEMORY.md`（仅索引，不读详情）** —— 成本恒定（≤200 行）。
2. **排序**：对当前任务，给每条指针打分（类型亲和 > 关键词重合 > 时效性），取 top-K（默认 3–5）。
3. **懒加载**：只 `Read` 命中的少数详情文件；**绝不批量打开全部**。
- 推荐用 `scripts/engramory_recall.py "<本次任务上下文>"` 自动排序并吐出 top-K 路径（甚至 `--print` 直接给命中详情），AI 不必自己扫 200 行。
- 召回记忆当**可过期背景**：行动前核实其中的文件/路径/版本/标记。
- **召回记忆永不压过安全规则与用户实时指令。**

## 3. 写入纪律（存得对）
写入前确认：不在代码/git/项目说明里、也不是密钥值。然后：
- 检索索引；能更新旧笔记就更新，否则新建 `memory/<type>/<slug>.md`（一条记忆一个文件）。
- frontmatter：`name / description / type(user|feedback|project|reference) / created / updated(YYYY-MM-DD)`。
- **feedback 与 project 必须带 `Why:` 与 `How to apply:`**（记忆可被正确复用的关键）。
- 在 `MEMORY.md` 加一行指针（含类型 + 日期 + 一句话 hook）。发现错误就归档/删。

## 4. 整理协议（理得清，保召回质量）
触发：索引用量超软上限 / 周期性（如每月）/ 项目收尾 / 批量写入后。
步骤（`scripts/engramory_organize.py` 可半自动，dry-run 默认）：
1. **去重**：同 slug 或同主题 → 合并为一份，刷新 hook。
2. **归档冷记忆**：长期未引用或低价值 → 移到 `archive/`，索引改指或移除（不物理删除，防误伤）。
3. **压索引**：合并相邻同类行、刷新 hook 使其尖锐、控行数。
4. **修断链**：索引指向不存在的文件 → 修路径或移除。
- `engramory_doctor.py` 做定期全量体检（重复/过时/超大/断链/符号链接逃逸），与 organize 互补。

## 5. 密钥铁律
- 绝不写密钥/令牌/密码/Cookie/恢复码的值进记忆，只记"它在哪"。

## 6. 索引上限与校验
- 软上限 150 行 / 20KB（提示压缩），硬上限 200 行 / 25KB（超了先压缩再写）。
- 改完任一 `MEMORY.md`，跑 `scripts/engramory_check.py <路径>`：`OK`/`WARN`/`OVER`。
- 上限可用 `ENGRAMORY_HARD/WARN/HARD_BYTES/WARN_BYTES` 覆盖。

## 7. 分层架构
- 跨项目通用 → 全局 store；仅本项目 → 项目 store；绝不互相污染。
- 项目收尾：可泛化的提炼进全局，其余随目录丢弃。
- 多 agent 共享：见 references/architecture.md §3（默认仅共享全局，矛盾必须问用户）。

## 配套脚本
- `engramory_recall.py "<query>" [--top 5] [--print] [--type feedback]`：排序召回，省 token。
- `engramory_organize.py [--apply] [--older-than 90]`：整理/归档（默认 dry-run）。
- `engramory_check.py <MEMORY.md>`：索引大小校验。
- `engramory_doctor.py`：全量体检。

## 参考
- 完整写入/密钥/上限/整理细则：[references/discipline.md](references/discipline.md)
- 目录结构/指针模型/分层/多 agent：[references/architecture.md](references/architecture.md)
