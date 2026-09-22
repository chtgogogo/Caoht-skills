# superpowers 附属文件快照

> 来源：github.com/obra/superpowers（MIT）· skills/ 目录下各 skill 的附属模板、技术文档与脚本。
> **快照版本：main @ 5bf4e7801107**（2026-09-22 经 GitHub API 抓取，与 depends/ 下 12 份主 SKILL.md 副本同一 commit，互引一致）。
> 文件按原仓库目录结构存放、保持原样未加头（提示词模板需可整篇直接复制使用）。

## 收录清单（16 个）

| 文件 | 被谁引用 | 用途 |
|---|---|---|
| subagent-driven-development/implementer-prompt.md | 主 SKILL（派遣实现者时整篇套用） | 实现者子代理提示词模板 |
| subagent-driven-development/task-reviewer-prompt.md | 主 SKILL（卡级评审第一轮） | 任务规格评审提示词 |
| subagent-driven-development/re-review-prompt.md | 主 SKILL（返工后复审） | 复审提示词 |
| subagent-driven-development/scripts/review-package | 主 SKILL | 评审打包脚本 |
| subagent-driven-development/scripts/sdd-workspace | 主 SKILL | 工作区管理脚本 |
| subagent-driven-development/scripts/task-brief | 主 SKILL | 任务简报生成脚本 |
| executing-plans/scripts/task-start | 主 SKILL | 单卡开始标记脚本 |
| executing-plans/scripts/task-done | 主 SKILL | 单卡完成标记脚本 |
| requesting-code-review/code-reviewer.md | requesting/executing-plans/subagent-driven 三处引用 | 代码质量评审子代理提示词 |
| test-driven-development/writing-good-tests.md | 主 SKILL（TDD 时参照） | 如何写出好测试 |
| brainstorming/visual-companion.md | 主 SKILL（可选视觉伴侣节） | 磨设计时把方案画成可视化框架的说明（浏览器预览服务未收，见下） |
| systematic-debugging/root-cause-tracing.md | 主 SKILL（排障时参照） | 根因追踪四件套 |
| systematic-debugging/defense-in-depth.md | 主 SKILL（排障时参照） | 纵深防御式修复 |
| systematic-debugging/condition-based-waiting.md | 主 SKILL（排障时参照） | 条件等待替代 sleep |
| systematic-debugging/condition-based-waiting-example.ts | 上一行的配套示例 | 条件等待代码示例 |
| systematic-debugging/find-polluter.sh | root-cause-tracing.md 引用 | 测试污染定位脚本 |

## 刻意未收（及原因）

- brainstorming/scripts/（server.cjs、helper.js、frame-template.html 等 5 个）：visual-companion 可视化配件的本地预览服务，需 Node 环境，与本流水线无关；
- systematic-debugging/CREATION-LOG.md、test-academic.md、test-pressure-1/2/3.md：作者创建日志与写 skill 时的自测文件；
- diagnosing-superpowers / using-superpowers / writing-skills 三个 skill 整体：与本流水线无关（见 ../依赖与自检.md）。
