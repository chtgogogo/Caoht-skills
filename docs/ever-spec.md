# ever-spec（Cao-EverSpec：常新规格·规格驱动开发）· 详解页

> [← 返回总目录](../README.md#skills-index) ｜ 目录＝[`ever-spec/`](../ever-spec/SKILL.md) ｜ name＝`ever-spec` ｜ 当前版本见其 frontmatter

**一句话**：规格驱动开发（SDD）独立件——把「系统当前承诺什么行为」变成机器可查、能红能绿的行为契约；主战场＝**巨型存量项目翻新重构**（棕地反提），增量工程是上限证明。

**三件套**：一层契约（`specs/`＋`changes/` 三动词增量）＋一把能红的尺（spec_check，31 条具名断言 selftest 自证）＋一条机读追踪链（trace_matrix＋render_agents）。

**什么时候用它**（启动路由七步，详见 SKILL.md）：设计期前奏（发散/收敛/重组）→ 已有规格先跑尺 → 立约落 specs → 改行为开变更包 → 追踪矩阵 → 管线适配 → 棕地反提与逆向升格。

**配套**：六支自证脚本（spec_check/trace_matrix/render_agents/evidence_chain/catalog/reanchor）＋doc_lint 文档体系尺＋reconcile 漂移对账（四列表判定列留人）＋dag 卡图工具＋evals 效果评测包。

**设计依据**：对标 OpenSpec（它有的我们有、它的缺点我们补、它没有的我们也有——证据哈希链/棕地反提/文档自咬）；上游取证四元组见 `references/供应链登记.md`（OpenSpec npm 1.15.0＋spec-kit 快照＋RFC 2119/8174＋MADR＋superpowers 理念级引用）。

**诚实边界**：这把尺只查机械面（句式/编号/文件在不在盘上），**语义漂移查不到**——那层靠测试与人工验收（细则 §6 四条边界）。

---

[← 返回总目录](../README.md#skills-index)
