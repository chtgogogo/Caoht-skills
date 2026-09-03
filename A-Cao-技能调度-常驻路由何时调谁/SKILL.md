---
name: useskill
version: v2.1
description: Skill 调度唯一入口（常驻）。负责本地唯一库检索、市场兜底、调用执行、异常降级与透明性报告。当用户说"调用/使用/找 skill"、任务超出自身基础能力需借助已装 skill、或连续 2 次工具调用失败/某子任务 3 轮无进展时触发；本地无匹配转 find-skills 市场兜底。
metadata:
  agent_created: true
priority: high
---

# useskill — Skill 统一调度规则

本 skill 是所有具体 skill 调用的**唯一前置调度器**。禁止绕过本规则直接读取其他 skill 执行体。

## 一、触发与短路

**触发（满足任一）**：
- 用户显式说「调用 / 使用 / 找 skill / useskill」
- 任务超出自身基础能力（生成特定格式文件、外部检索、图像处理…）
- 兜底：连续 2 次工具调用失败，或某子任务 3 轮无实质进展

**短路（直接正常对话）**：纯打招呼、闲聊、单行知识问答、简单总结等不涉及外部工具的任务。

## 二、调度状态机（核心流程）

### State 1 · DIRECT_MATCH（已知名字快路径）
- 条件：用户点名 skill 名，或上下文有明确 skill 引用。
- 动作：按第三节解析路径读目标 `SKILL.md` 执行；本机缺失 → 跳 State 3。

### State 2 · LOCAL_SEARCH（本地语义检索）
- 条件：未知具体名字，但明确任务类型。
- 动作：
  1. 索引优先：查映射表 `__SKILL详细名映射表.md`（中文文件夹名 ↔ name 字段 ↔ 功能），按任务选 1–3 候选；
  2. 目录兜底：无索引/未命中，扫唯一库下各 `SKILL.md` 的 `name` + `description` 做语义匹配；
  3. 选中读 `SKILL.md` 执行；无匹配 → 跳 State 3。

### State 3 · MARKET_FETCH（市场兜底）
- 调用 `find-skills` 按任务关键词检索 WorkBuddy 市场。
- 命中：提示用户 → 经 `skills-security-check` 审计 → 标准安装 → 回 State 1。
- 未命中：如实告知「暂无可用 skill」，建议用 `skill-creator` 自建。

## 三、路径解析（唯一库 + 自动发现）

按优先级确定 skill 库根目录：
1. 环境变量 `WORKBUDDY_SKILLS_PATH`（非标目录时用它覆盖）
2. **唯一权威库：`D:\Deepseek-ALL\skills\`**（默认，全部 skill 本体在此）
3. 兜底标准路径：`~/.workbuddy/skills/`、`./skills/`、`~/.codex/skills/`（仅用于找本调度器自身，不用于找 skill 本体）

**定位 skill 本体按 `name` 字段（调用键），不是文件夹名**（文件夹是详细中文说明书名）：
- 先查映射表拿中文文件夹名 → 读 `D:\Deepseek-ALL\skills\<中文文件夹名>\SKILL.md`
- 兜底：`grep -rl "^name: <调用键>" D:\Deepseek-ALL\skills`

## 四、异常处理与降级

被调 skill 出错（超时 / API 异常 / 死循环）：
1. **参数修正重试**：同 skill 换保守参数重试 1 次
2. **同级降级**：换功能相近备选 skill 重试 1 次
3. **硬终止上报**：仍失败立即停止，不静默掩盖，回复末尾输出错误与已尝试路径

## 五、透明性报告（强制）

每次经本 skill 触发调度后，回复末尾追加标准块：

```
---
### 🛠️ Skill 调度报告
- **已调用**: `[skill_name]`（无则 `无`）
- **触发原因**: [显式指令 / 任务匹配 / 兜底激活]
- **检索路径**: [DIRECT_MATCH / LOCAL_SEARCH / MARKET_FETCH]
- **异常与降级**: [正常 / 简述降级动作]
---
```

## 六、调用统计（每次调度后更新）

每次成功调度一个 skill 后，更新 `D:\Deepseek-ALL\skills\__usage_stats.md`：

- 找到该 skill（按 `name` 字段）那一行 → **次数 +1**、**最近调用**改为今天；
- 没有该行 → 新建一行；
- 保持按「次数」**降序**排列（最常用的排最上面）。

> 这是「宁多勿少」的统计，做对即可；统计文件很小，多读一次不费 token。用户问「哪些 skill 最常用 / 调用次数」时，直接读这个文件回答。

## 七、维护

- 新增 / 删除 / 改名 skill → 同步更新 `D:\Deepseek-ALL\skills\__SKILL详细名映射表.md`（调用键 `name` 字段保持稳定）
- 分类索引过时 → 改 `D:\AI提炼\skill-index\` 下文件
- 调度逻辑升级 → 只改本 SKILL.md

> **版本与副本同步（v2.1）**：本调度器 SKILL.md 在各宿主各放一份逐字一致副本（`useskill` 文件夹）；真正的 skill 本体只在 `D:\Deepseek-ALL\skills\`，不要在 `E:\.workbuddy\skills` 或 `D:\AI提炼\skills` 里再维护第二套。
