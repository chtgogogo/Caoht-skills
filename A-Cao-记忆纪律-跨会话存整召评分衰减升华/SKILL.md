---
name: memory-scoring-lifecycle
version: v3.2
description: Agent 记忆纪律 v3.1（通用版入口）。适用于任何 agent、任何记忆存储位置。当需要给记忆打分/分类/衰减/升华、设计触发词、检索寻址、做体检、或用整套文件化长期记忆规则时触发。规则共享一份，记忆 store 每个 agent 各自建立、互不共享。启发词：【记忆纪律 v3.1】。
metadata:
  agent_created: true
---

# 记忆纪律 v3.1（通用入口）

> 启发词：`【记忆纪律 v3.1】`。本 skill 是通用记忆纪律的入口，**与具体 agent 的存储位置解耦**。

## 通用性约定（必读）

1. **规则共享一份**：完整规则在 `D:\Deepseek-ALL\记忆规则\`（00–12 文档），所有 agent 共享这一份，规则里**不含任何 agent 专属路径**。
2. **记忆 store 各自建立、不共享**：每个 agent 在**自己的记忆存储位置**建自己的 store（自己的 `MEMORY.md` 索引 + 按需加载的详情文件）。**不要在 store 里写死别人家的路径**。
   - **你的 store 根由你自己的 agent 配置决定**：看你自己的 `AGENTS.md` 里的 `MEMORY_ROOT`、环境变量、或你系统注入的记忆位置；没有的话，就在你自己习惯的记忆位置新建一个。
   - 例：Codex 用 `D:\codexengramory\`；WorkBuddy 用 `D:\workbuddyengramory\.engramory\`；其它 agent（如 DeepSeek Harness）用它们各自配置的位置。
3. **常驻引导卡各自放**：把「记忆纪律 v3.1 常驻引导卡」放进**你自己 store 的 `MEMORY.md` 顶部**，路径写你自己的。
4. **脚本各自复制**：把 `tools/memory_maintain.py` 复制到**你自己 store 的 `tools/`** 下运行。

## 权威规则位置（通用）

- 完整规则（00–12）：`D:\Deepseek-ALL\记忆规则\`（`00_记忆系统总纲.md` 含常驻引导卡模板）
- 确定性脚本：`D:\Deepseek-ALL\记忆规则\tools\memory_maintain.py`（复制到自己 store 用）

## 7 条最小核心（每次必做）

1. 写入：三记五不记 → 本质层必写、细节按需 → 三层触发词（现象/实体/本质 + 同义）
2. 打分：V = 5 + 影响(0/10/20) + 复发(0/5/15) + 成本(0/5/15) + 稀缺(0/10) + 强调(20)；R = 相关(精确6/沾边3/弱1) + 新鲜(7天4/30天2/更早0)
3. 排序：排序分 = (V/10)×0.6 + R×0.4
4. 检索：路由表 O(1) 直达 → 渐进披露（先头部后全文）
5. 命中：V+2~8、strength+1（衰减结算交给脚本）
6. 升华：root_cause_signature 相同累计 ≥3 → 铁律进 CORE
7. 收尾：跑 `check` + 更新 CORE「系统状态」

## 冷启动（某个 agent 第一次用）

1. 在自己 store 根建 `MEMORY.md`，顶部放「常驻引导卡」（启发词 + 7 条核心 + 指向）。
2. 建各库空 index。
3. 第一条记 PROFILE（你是谁）。
4. 把 `memory_maintain.py` 复制到自己 store 的 `tools/`。

## 与旧版本的关系

- 本 skill 由旧 `engramory-discipline`（存储层 8 条）+ 旧 `memory-scoring-lifecycle` v1.1（评分层）**合并升级**而来。
- 旧 engramory 8 条、旧 v1.1 已被 v3.1 取代；v3.1 保留索引习惯（指针、150/200 行上限、秘密只记位置），并新增路由/树寻址/token 预算/本质优先/工程保障/脚本。
