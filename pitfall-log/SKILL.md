---
name: pitfall-log
description: 跨会话、跨 agent 共享的踩坑与经验教训沉淀库（根因层＋案例层＋极轻索引）。遇到报错、失败、反常现象、反复排查不下的问题时，先检索本库再动手改，避免同一 bug 每次都从零重修；解决了棘手问题后随手记一条，下次再犯直接照修法修。当用户说「看看踩坑记录」「之前怎么解决的」「有没有类似的坑」「记一下这个坑」「翻车了」「教训」时，或你刚花很多轮才修好一个 bug、不想下次从头再来时，用本 skill。计数与去重由随包脚本完成（案例数／命中数不手填），升华走「候选 → 验证适用范围 → 提升」闸门。正式 bug 诊断修复流程转 diagnosing-bugs，本 skill 只管沉淀与查阅。
metadata:
  displayName: 踩坑日志（Pitfall Log）
  agent_created: true
  version: 1.7.0
---

# 踩坑日志（Pitfall Log）

省 token 的踩坑沉淀体系：**极轻索引**（`pitfall-log.md`，常驻上下文）＋ **根因层**（`roots/RT-*.md`，检索入口）＋ **案例层**（`pitfalls/YYYY-MM-DD-*.md`，深读用）。根因是一级公民，案例挂在根因下；设计理由与口径全文见 `references/calibration.md`。

```
<log-root>/
├── pitfall-log.md              # 极轻索引（单一事实源）
├── roots/RT-<三位编号>-<slug>.md        # 根因层：一个根因一个文件（含 roots/archive/）
├── pitfalls/YYYY-MM-DD-<slug>.md       # 案例层：一条坑一个文件（含 pitfalls/archive/）
└── _candidates/C-RT-*.md               # 升华候选区（候选 → 验证 → 提升才变铁律）
```

`<log-root>` 现值见 `personal.local.md`；首次使用先和用户确认，别写死路径。所有 agent 共用同一个库（单一事实源）。

## 铁律（九条，每条都有工具判据背书，违者即腐化）

1. **先查再修**：要修任何非显然问题，检索本库先行；检索前先跑一次 `check`（毫秒级只读，红了先向用户报告漂移）——读侧不校验，烂索引会被读十次、指错十次。
2. **唯一写入通道是脚本**：登记、计数、退役、并档、提升全走 `scripts/pitfall_log.py`；事件 ID、指纹、索引行、三侧数字全部由工具生成，**任何数字都不手填**。手改文件是例外且必须事后 `counts --apply` + `check` 复核。
3. **编号只增不改**：`RT-0NN` 被引用中，改号即断链。新建根因族必须 `add --make-root "一句话根因"`，绝不凭可能打错的号凭空立族；重复根因族用 `merge-roots` 并档，不要弃号。
4. **命中判据＝因果要件**：问题被独立解决**之前**，记录被看见且对解决产生实际助力＝命中；解决之后才发现已有记录＝纯归类，不算。怎么看见的不限（主动检索、干活途中撞见都算）。宁少勿虚——虚高的数字等于没有数字。
5. **升华必须过闸**：候选要补齐「适用范围／反例／边界／复核日期」四要素，反例写「无」＝没过闸，边界含「所有任务／永远」类无边界措辞＝没过闸，同一根因下适用条件不同 ⇒ 分轨各立候选。**禁止「数到次数自动变永久规则」**。
6. **已提升的铁律有回归期**：提升记录强制带「下次复核日期」，到期没复核 `check` R14 翻红——没有回归期的规则迟早变教条。
7. **旧条目不追改**：历史条目缺「复现／验证」不补写（＝替当年的自己编证据），`kind: legacy` 自动退出缺段自检；新条目一律按模板。
8. **没有删除通道的库必腐**：错记／过时的条目用 `retire --case/--rt --reason` 软删进 `archive/`（理由必填，可逆），.empty 根因族不退役只并档。索引案例数超工具上限（`check` R11 会报警）就该退役冷条目。
9. **改本包任何规则/模板/脚本 ⇒ 全处同步 + 跑 `selftest`**：阈值等常量以脚本为唯一权威（`counts --json` 可见），正文与模板只指工具、不抄数字；同步后 md5 对账（细节见 `references/concurrency.md`）。

## 命令（`python` 启动器是通用写法；本机别名等事实见 `personal.local.md`）

```bash
python scripts/pitfall_log.py --root <log-root> init                 # 建骨架（索引＋三模板＋候选区）
python scripts/pitfall_log.py --root <log-root> add --title <标题> --root-cause RT-0NN \
        [--kind full|lite] [--severity 红|橙|黄] [--make-root "一句话根因"] \
        [--also-root RT-0MM] [--date YYYY-MM-DD] [--author …] [--tags a,b] \
        [--summary 一句话后果] [--body-file F] [--slug 覆盖]
python scripts/pitfall_log.py --root <log-root> hit --case <事件ID> --scenario … [--solved …]
python scripts/pitfall_log.py --root <log-root> hit --root-cause RT-0NN --scenario …  # 只读根因层就解决时的命中
python scripts/pitfall_log.py --root <log-root> counts [--json] [--apply]             # 从事件记录现刷三侧数字
python scripts/pitfall_log.py --root <log-root> check [--json] [--deep]               # 一致性自检；--deep 连模板常量一起对
python scripts/pitfall_log.py --root <log-root> promote --scan                        # 到阈值 → 生成候选
python scripts/pitfall_log.py --root <log-root> promote --gate C-RT-009-01-xxxx [--apply]
python scripts/pitfall_log.py --root <log-root> retire --case <事件ID> --reason …     # 案例退役（软删进 archive/）
python scripts/pitfall_log.py --root <log-root> retire --rt RT-0NN --reason …         # 空根因族退役
python scripts/pitfall_log.py --root <log-root> merge-roots --into RT-0AA --from RT-0BB [--yes]
python scripts/pitfall_log.py --root <log-root> audit [--sample K]                    # 命中抽审（只读）
python scripts/pitfall_log.py selftest                                                # 随包自证（无需 --root）
```

退出码：0 正常／绿 ｜ 1 `check` 见红或闸门没过或 selftest FAIL 或写后读回验收不过 ｜ 2 用法与目录错 ｜ 3 候选不在场（先 `--scan`）｜ 4 同一事件 ID 换内容被拒 ｜ 5 抢锁失败。`--root` 必填（不给默认当前目录＝防在哪跑都假绿），指向不存在的目录直接非 0 退出、不假建。

## 检索流程（先根因，后案例）

1. `check`（红非零 → 先报告漂移，再继续）。
2. 读 `pitfall-log.md`（极省）→ 扫「根因索引表」，看哪条根因的**识别信号**对得上当前问题。
3. 对上 → 读 `roots/RT-*.md`，拿**通用对策**先试。
4. 不够 → 再挑该根因下的具体案例深读找灵感。
5. 解决了 → 用 `hit` 给**真正帮上忙的那一个** +1（案例层与根因层分开记、各一次），并告知用户。
6. 全对不上 → 自己排查；解决后按下面的记录流程记一条。

## 记录流程

1. 确定稳定的 `<log-root>`（空库先 `init`）。
2. **归因**：这是**一类**问题还是这**一件**事？在 `roots/` 找现成同根因 → 有就 `add --root-cause RT-0NN` 挂上去；没有才 `--make-root` 立族。
3. 写案例：`add --title … --severity … --kind full|lite [--body-file F]`。🔴 不可再生损失必须用完整八段（工具会当场把 🔴＋lite 升格）。
4. 索引同步在**同一次 add 里**做完（锁内：插行＋刷数字＋根因 frontmatter＋写后读回验收）。`counts --apply` 是修复别人手改／历史漂移的通道，不是每次记录的必经步骤。
5. 收尾跑一次 `check`：红 0 才算这条真在册。

## 案例格式（八段完整复盘 ／ 四段轻量）

标题行：`# <一句话标题> —— <最直接的后果> 🔴/🟠/🟡`（分级按**损失能否挽回**：🔴 不可再生损失／🟠 结果错但可回滚／🟡 只浪费时间；分级只写标题、不进 frontmatter）。模板在 `assets/entry-template.md`（八段＋单列「我自己引入的错」）与 `assets/entry-lite-template.md`（四段，只给 🟠/🟡），工具 `init` 时同步进库 `pitfalls/_template*.md`。

| # | 段名 | 必须写到什么程度 |
|---|---|---|
| 1 | 现象 | 先一行上下文，再贴**屏上原文或具体数字**（转述会抹平关键特征） |
| 2 | 复现 | **最短命令序列**；撞不出来就写「未能复现＋当时条件」，不许留空 |
| 3 | 现场代码 | `文件:行` ＋ 关键片段，抄**改之前**那一版 |
| 4 | 根因（机制） | 本案例的具体机制，不停在表象；`root:` 字段才是根因族，两者别混 |
| 5 | 修法 | 代码级改动 ＋ 为什么改这里而不是别处 |
| 6 | 验证 | 修后**实测输出／断言条数**；「改好了」三个字不算验证 |
| 7 | 能否借鉴 | 全篇最值钱：可迁移规律＋**适用条件**（空缺的条目永远进不了升华候选） |
| 8 | 教训 | 一句下次能直接照做的命令句，范围贴着本案例 |

文末「命中记录」表是命中的唯一分母（一行＝一次）；「我自己引入的错」没有就写「无」——**空节比缺节可检查**。

## 升华（候选 → 验证适用范围 → 提升）

1. `promote --scan`：同根因案例数或单条命中数达到**工具阈值**（阈值是脚本常量，`promote --help` 可见现值；两边不同轴别混用）→ 只在 `_candidates/` 生成候选，不改任何规则。
2. 人填四要素（候选模板 `assets/candidate-template.md`）；`promote --gate <编号>` 干跑点名缺什么——反例「无」、无边界措辞、过期复核日期都过不了闸。
3. `promote --gate <编号> --apply` ⇒ 候选置「已提升」＋来源与根因 `escalated: true` ＋索引两处加 ⭐ ＋写入「下次复核日期」。第 4 步人做：把铁律抄进各 agent 自己 store 的 CORE 铁律区（互认关系见 `references/interop.md`）。
4. 闸门没过就留在候选区继续攒——这正是「固定次数自动升永久规则」被关掉的样子。

## 命名与发布（本 skill 的已知偏差，明示不隐瞒）

- 规范要求 frontmatter `name` 与挂载目录名一致：本 skill 挂载键是 `pitfall-log`（各宿主 junction 名一致，满足规范）。
- 权威库目录名是**本机中文命名规则**（`A-Cao-…`），仅本机成立；发布仓统一映射为英文目录 `pitfall-log/`（映射表见发布工具的 publish_map 第七节）。宿主巡检判据键（`name: pitfall-log`、`roots/` 目录名、索引名 `pitfall-log.md`）**不许改**。

## 降级路径

- `pitfall_log.py` 不可用时：按模板手工建档＋索引行（数字留空由下一个能跑工具的会话 `counts --apply` 刷齐）。
- 整个 skill 不在场时：直接读 `<log-root>/pitfall-log.md` 索引（`personal.local.md` 有现值）。

## 深读指针（何时读哪个）

| 文件 | 什么时候读 |
|---|---|
| `references/calibration.md` | 需要解释「这个数怎么算的」、或要写第三方解析器对接本库时——指标口径全表、事件 ID/指纹、命中判据全文 |
| `references/concurrency.md` | 多 agent 同写一个库、或升级分发本 skill 时——锁/原子写四步、诚实边界、全处同步与 md5 对账操作单 |
| `references/incidents.md` | 想以史为鉴时——历次真缺陷判例（恒真判据家族、写后读回、搜索键选错等），防再犯 |
| `references/interop.md` | 接入记忆体系、宿主巡检报警时——RT 号同源、存哪边判据、巡检键与降级路径 |

## 版本

权威迭代记录**只在 `CHANGELOG.md` 一处**（本文件不重抄，防双写漂移）；当前版本见 frontmatter `metadata.version`。改前跑 `python scripts/pitfall_log.py selftest`，改后按铁律 9 同步全部副本。
