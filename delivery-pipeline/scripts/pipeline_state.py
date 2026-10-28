#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline_state — 交付流水线的**单一权威状态**工具（state.json 是真源，Markdown 看板由它生成）。

为什么需要它：台账.md 与 state.json 两份手工平行维护时，旧规则写的是「两份不一致以 updated_at
较新者为准并当场回写」——那是拿**整份文件**按墙钟时间互相覆盖。A 窗改了 stage① 的产物路径、
B 窗改了 card#4 的验收要点，两份各新在**不同字段**上，较新者一覆盖，较旧那份里的有效修改整片丢失
（并发教科书里的 lost update／丢失更新；墙钟排序的危险见 Martin Kleppmann 的「Coordination risks
of using time in distributed systems」）。本脚本把仲裁单位从「文件」降到「字段」，并只认一个真源：

  · `state.json` = 唯一可写的权威记录，带单调递增的 `rev`
  · `台账.md` / `任务板.md` = **渲染产物**（含 AUTO-BEGIN 标记，禁止手改；无内容就不建文件）
  · 手上已有旧台账 → `sync --from-md` 做**逐字段**并账：state 里没有的才吸收，
    state 已有值的冲突一律登记「待人工裁决」，脚本永不自动判胜负、永不整份回写

并发口径与 Cao-多Agent团队协作文档治理 的 `scripts/edit_lock.py` 一致：
  · 单写者：写盘一律「临时文件 + fsync + os.replace」原子替换，写后读回逐字节对账，不符即回滚
  · 多写者：租约锁（O_CREAT|O_EXCL 独占创建 + 心跳 + 代次 generation + 陈旧锁需显式 --takeover）
    + 乐观版本校验 `--expect-rev`（你读到的 rev 不等于当前 rev ⇒ 拒绝写入，退出码 6）
    ⇒ 接管者写完 rev 变了，旧写者醒来那一笔必然撞 6，盖不掉别人的字段改动

写完成的硬闸（不是散文约定，是退出码）：
  · `stage --state done` 要求四字段齐：输入版本 / 产物位置 / 完成条件 / 恢复点，
    且登记的产物在盘上真实存在 ⇒ 否则退 4 拒绝标记完成
  · `stage --state done` 另过**产物清单硬闸**（v1.14.0）：本步盘上**必须**有的文件缺一件即退 4
    （①=文档层六件＋设计纪要；②=选型记录；③=PRD＋specs/；⑤=卡头每个模块的 MODULE.md）——
    堵「只登记一部分产物就标 done」（Qoder 2026-10-15 平行项目实测漏洞）；合法退出只有
    `docs --opt-out <路径> --why <用户原话>`，记进权威记录，不静默跳过
  · `stage --state doing` 过**顺序硬闸**（v1.14.0）：⑥ 开工前 ⑦④⑤ 必须 done（na/skipped 算显式豁免）
  · `card --state done` 要求 `metric`（实测数字）非空 ⇒ 否则退 4（台账模板「状态只有实测值」的机检面）

文档层默认落地（v1.14.0，secure-by-default）：`init` 默认搭 L0 骨架六件（缺才建、棕地不覆盖），
① 填实后删骨架标记才算落地；此后 `check` 持续验「① done 之后文件还在盘上」——删文件照样判红。

只用标准库；不读任何本机特有工具；缺件时明确降级不报错（`--root` 指哪都行）。

用法（`--root` 是项目根，状态存放 `<root>/docs/pipeline/`）：
  py scripts/pipeline_state.py init   --root R --project 名字 --mode 标准 --apply
  py scripts/pipeline_state.py mode   --decide --signal files_touched=2 --signal has_code=false
  py scripts/pipeline_state.py plan   --root R --tier 轻量
  py scripts/pipeline_state.py render --root R [--view ledger|board|all] [--apply]
  py scripts/pipeline_state.py check  --root R
  py scripts/pipeline_state.py sync   --root R --from-md 旧台账.md [--apply]
  py scripts/pipeline_state.py stage  --root R --id ③ --state doing --input-version PRD@v0.3 \
                                      --artifact docs/pipeline/PRD.md --criteria "PRD 含全局验收" \
                                      --recovery "第 4 节待补" [--expect-rev 12 --holder 执行窗] --apply
  py scripts/pipeline_state.py card   --root R --id c4 --state done --metric "来源数≥1 实测 3" --apply
  py scripts/pipeline_state.py gate   --root R --name security_L1 --value passed --apply
  py scripts/pipeline_state.py docs   --root R --show                 # 文档层逐件状态（在盘/骨架/已退出）
  py scripts/pipeline_state.py docs   --root R --opt-out docs/human/小白手册.md --why "天 2026-10-15 明确不要" --apply
  py scripts/pipeline_state.py answer --root R --id 落盘目录 --answer docs/pipeline/ --source 用户已说明 --apply
  py scripts/pipeline_state.py answer --root R --id 落盘目录 --check        # 已答 ⇒ 退 0，别再问
  py scripts/pipeline_state.py action --root R --id push-github --kind git --done --idem-key k1 --apply
  py scripts/pipeline_state.py action --root R --id push-github --check      # 已做过 ⇒ 退 0，跳过重放
  py scripts/pipeline_state.py deps   --root R --record 路径|来源|版本 --map 上游路径=本地路径 --check-links a.md,b.md
  py scripts/pipeline_state.py resume --root R
  py scripts/pipeline_state.py show   --root R [--json]
  py scripts/pipeline_state.py selftest

退出码：0 成功/一致/已答过已做过 ｜ 1 检查不通过（漂移、断链、未答未做）
        2 参数或路径错误 ｜ 3 权威记录缺失（先跑 init）｜ 4 写入前置未齐（四字段/产物/metric）
        5 锁被他人有效持有 ｜ 6 版本冲突（--expect-rev 与当前 rev 不符）
        7 锁租约已过期（确认对方没在写再加 --takeover）｜ 8 派生物含手写内容（不许覆盖）
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime

TS_FMT = "%Y-%m-%d %H:%M:%S"
SCHEMA_VERSION = 2
STATE_NAME = "state.json"
DEFAULT_SUBDIR = os.path.join("docs", "pipeline")
LEDGER_NAME = "台账.md"
BOARD_NAME = "任务板.md"
LOCK_NAME = "state.lock.json"
LOCK_LEASE_DEFAULT = 3600
STALE_GRACE = 5.0

AUTO_BEGIN = "<!-- AUTO-BEGIN 本块由 pipeline_state.py 从 state.json 生成，请勿手改（改权威记录后重跑 render --apply） -->"
AUTO_END = "<!-- AUTO-END -->"
AUTO_MARK = "AUTO-BEGIN"

# 阶段序列按 SKILL.md §二索引（⑫收尾排在最后，④⑦为卡库与验收前置，编号沿用原有写法不重排）
STEP_ORDER = ["①", "②", "③", "④", "⑤", "⑦", "⑥", "⑧", "⑨", "⑩", "⑪", "⑬", "⑭", "⑫"]
STEP_NAME = {
    "①": "磨设计", "②": "技术选型分层深选", "③": "PRD", "④": "任务卡库初始化",
    "⑤": "切任务卡", "⑦": "验收先于实现", "⑥": "执行与派遣", "⑧": "台账与状态",
    "⑨": "知识图谱外挂", "⑩": "里程碑回看", "⑪": "代码评审", "⑬": "安全基线检查",
    "⑭": "对外陈述口径检查", "⑫": "收尾",
}
TIER_STEPS = {
    "轻量": ["①", "③", "⑥", "⑧", "⑬", "⑫"],
    "标准": ["①", "②", "③", "④", "⑤", "⑦", "⑥", "⑧", "⑩", "⑪", "⑬", "⑫"],
    "企业": list(STEP_ORDER),
}
# 该档**应当存在**的派生文件。轻量档刻意不含任务板/记录清单/模块地图——审查点名的「空文档堆」。
TIER_FILES = {
    "轻量": [STATE_NAME, LEDGER_NAME],
    "标准": [STATE_NAME, LEDGER_NAME, BOARD_NAME],
    "企业": [STATE_NAME, LEDGER_NAME, BOARD_NAME],
}
ALIASES = {"复杂": "企业", "轻": "轻量", "标": "标准", "企": "企业", "跳步优化": "标准"}
CARD_STATES = ("todo", "doing", "done", "blocked")
STAGE_STATES = ("todo", "doing", "done", "skipped", "na")
GATE_VALUES = ("passed", "failed", "pending", "na")
STAGE_REQUIRED_FIELDS = ("input_version", "artifacts", "done_criteria", "recovery_point")

# ---- v1.14.0 文档层「默认落地、显式退出」（secure-by-default：默认全落地，削弱必须显式退出并记账）----
# 触发：天 2026-10-15 令「除非用户强调某个不需要，否则一定要按规则落地文档，否则这个项目有文档、
# 下个项目又没文档」。外部对标：agents.md 开放约定（AGENTS.md＝给 agent 的 README，跨工具）；
# secure-by-default（NIST SP 800-53 SA-8(23)／GDPR Art.25：削弱型配置必须显式 opt-in）；
# golden path「govern by construction, not inspection」（合规是路径的属性，不是审查步骤）。
# Qoder 实测漏洞（2026-10-14/15 平行项目）：文档层整层缺失，且 ① 只登记 PRD.md 就标 done——
# 旧硬闸只验「登记过的在不在盘上」，不验「该有的有没有」。本版把后者写进脚本。
DOC_L0_FILES = [
    "AGENTS.md",                  # 给 AI 的路由件（≤120 行、U 型排版，细则 §2）
    "CONTEXT.md",                 # 术语表（细则 §1）
    "docs/权威源表.md",            # 占位符→真实路径，只此一处改（细则 §1）
    "docs/human/小白手册.md",      # 叙事层首版（细则 §5.4）
    "docs/human/故障对照表.md",    # 现场自救表（细则 §5.5）
    "docs/pipeline/上下文体检.md",  # 四格对账读数（细则 §4）
]
SCAFFOLD_MARK = "<!-- pipeline-scaffold -->"   # 骨架标记：① 标 done 时盘上不许还有它
# 阶段产物清单硬闸：all＝任何档位都要；non_light＝轻量档豁免（索引原文：轻量①＝一句话版、③规格层可省）
STAGE_MANIFEST = {
    "①": {"all": list(DOC_L0_FILES), "non_light": ["docs/pipeline/设计纪要.md"]},
    "②": {"non_light": ["docs/pipeline/技术选型记录.md"]},
    "③": {"all": ["docs/pipeline/PRD.md"], "non_light_glob": ["specs/*/spec.md"]},
}
# v1.16.0：init 额外搭的三份大计划产物骨架（标准/企业档；轻量档不搭——索引原文轻量①一句话版、③规格层可省）。
# 章节结构与 references/大计划产物模板.md §2/§3 一致，改结构两处同步改。
PLAN_STUB_FILES = ["docs/pipeline/设计纪要.md", "docs/pipeline/技术选型记录.md", "docs/pipeline/PRD.md"]

# v1.17.0 结构锚：填实后的产物应保留骨架的章节/表格结构——锚=无节号的子串（容忍节号与措辞微差，只锚
# "该有的结构块在不在"，不锚内容对错——那是语义面，靠出题验收与人）。三平行窗实测：薄骨架+无锚 ⇒
# 同一 skill 两个 AI 填出两种章节结构（AGENTS.md 实录）、故障表列数各自发挥。缺失记「警」不是红：
# 老项目与刻意自定义结构不炸 check，但当场点名让人看见。改锚=改对应骨架+本表，两处同步。
STRUCTURE_ANCHORS = {
    # v1.20.0：AGENTS.md 分「生成区＋人写区」——AUTO-END 是生成区的存在锚（丢了=生成件被手删，
    # 下次 render 会重建，但这之前人读的是旧账）；文档登记表仍在人写区（"什么时候读哪份"是人判断）
    "AGENTS.md": ["文档登记表", "AUTO-END"],
    "CONTEXT.md": ["| 术语 |"],
    "docs/权威源表.md": ["| 占位符 |"],
    "docs/human/小白手册.md": ["你怎么用它"],
    "docs/human/故障对照表.md": ["贴给 AI 的原话"],
    "docs/pipeline/上下文体检.md": ["四个有效性指标当期读数"],
    "docs/pipeline/设计纪要.md": ["盘问决策"],
    "docs/pipeline/技术选型记录.md": ["冲突调和"],
    "docs/pipeline/PRD.md": ["用户视角走查清单"],
}
PRE_EXEC_DEPS = {   # v1.18.0 铺满：DAG 依赖（不是全线性链）——每步只挡「真依赖它的」；na/skipped 豁免
    "②": ("①",),
    "③": ("②",),
    "④": ("③",),
    "⑤": ("④",),
    "⑦": ("③",),
    "⑥": ("⑦", "④", "⑤"),   # v1.14.0 原有：卡没切、验收基线没就绪就不许施工
    "⑩": ("⑥",),
    "⑪": ("⑥",),
    "⑬": ("⑥",),
    "⑫": ("⑬",),              # 铁律 10：收尾前必须先过安检 L1；⑭ 由 gate 管（na 合法）
}   # ⑧ 台账是持续动作不锁；⑨ 默认 na。单步直调=用户点名某一步 ⇒ --skip-auth "授权说明" 放行并留痕

# init 搭的骨架：只给「该写什么、去哪找细则」，不替 ① 写内容——填实后删掉首行 scaffold 标记。
# v1.17.0 加厚：骨架内嵌章节与表格结构（结构锚见 STRUCTURE_ANCHORS）——填实=往固定框架里填行，
# 结构零发挥；三平行窗实测「AGENTS.md 骨架一行待填 → 两个 AI 填出两种章节结构」，薄骨架=不稳定根源。
DOC_STUBS = {
    "AGENTS.md": """<!-- pipeline-scaffold -->
# AGENTS.md — <项目名>（骨架·① 填实后请删掉上一行标记）

> 给 AI coding agent 的项目路由（agents.md 开放约定，跨工具通用）。README 是给人看的，本文件是给 agent 看的。
> **两区结构（v1.20.0）**：上面 `AUTO-BEGIN`／`AUTO-END` 之间是**生成区**，由
> `py scripts/render_agents.py --root <项目根> --apply` 整块覆盖（现状读数／改动落点速查／模块路由），
> **禁手写，也不许在里面手写任何数字**；下方是**人写区**，只填红线、代码风格、命令、与「什么时候读哪份文档」。
> 填实要求＝流水线 `references/项目文档与上下文规范.md` §2：≤120 行、U 型排版（红线最前、登记表最后）、
> 登记表每行必须写「什么时候读它」；过程与状态权威＝`docs/pipeline/state.json`。

<!-- AUTO-BEGIN 由 scripts/render_agents.py 生成 · 禁手改（手改会被下次生成覆盖） -->
<!-- AUTO-END -->

## 项目一句话

（一两句：这是什么、给谁用、边界在哪）

## 构建与测试命令（原样可复制）

（bash 代码块：装依赖 / 启动 / 测试，各一条命令＋一行注释）

## 代码风格

（3~5 条本项目硬约定；确实没有写「经确认：无（日期，依据）」）

## 红线摘要

（3~5 条不可违背约束，一句话一条；项目宪章在 PRD 头部时这里放指针不抄条款）

## 文档登记表（什么时候读什么）

| 文档 | 读它的时机 |
|---|---|
| `docs/pipeline/state.json` | 任何状态/进度问题（唯一权威，台账.md 是它生成的视图） |
| （按项目补，每行必须写触发时机） |  |
""",
    "CONTEXT.md": """<!-- pipeline-scaffold -->
# CONTEXT.md — 术语表（骨架·① 填实后请删掉上一行标记）

> AI 与新人共用的词汇口径。每个词写「在本项目里指什么」，禁抄字典定义。
> 需求与规格条款里的主语一律用本表口径；想加同义词先来这里登记（同一信息只此一处）。

| 术语 | 在本项目里指什么 |
|---|---|
| （按项目补） |  |
""",
    "docs/权威源表.md": """<!-- pipeline-scaffold -->
# 权威源表（骨架·① 填实后请删掉上一行标记）

> 占位符 → 真实路径的唯一修改处（细则 §1）。别处一律写占位符或指针，路径变了只改这一张表。

| 占位符 | 真实路径 | 什么时候读它 |
|---|---|---|
| `<落盘目录>` | 待填 | 流水线产物都在这里 |
| （按项目补） |  |  |
""",
    "docs/human/小白手册.md": """<!-- pipeline-scaffold -->
# 小白手册 — <系统名>怎么用（骨架·① 填实后请删掉上一行标记）

> 核对日期：____-__-__ ｜ 对照的事实：<设计稿或代码版本；代码未定稿时写明「按设计稿写，定稿后校准」>
> 给项目主人看的大白话说明：从界面层写起、一套比喻贯穿到底、每条说法＝「你点 X → 界面出现 Y」（能亲手验）。
> 填实要求＝细则 §5.4 每模块六栏（它管什么/数据怎么走/实现了哪些功能/怎么做的·讲逻辑不讲工程/怎么算做对了/防的是什么坏事）。

## 这个系统是什么

## 你怎么用它（点击动线，按 1. 2. 3. 编号写）

## 它怎么算做对了（验收口径）

## 它防的是什么坏事

## 出错了怎么办（指向《故障对照表》）
""",
    "docs/human/故障对照表.md": """<!-- pipeline-scaffold -->
# 故障对照表 — 现场/演示自救（骨架·① 填实后请删掉上一行标记）

> 核对日期：____-__-__ ｜ 症状 → 多半是 → 你这样做 → 求助时把「贴给 AI 的原话」整段复制给它。
> 🔴 第四列风险最高：报错原话常夹本机路径/密钥——贴出去前按 R9 脱敏（只删机密，其余一字不改）。

| 症状 | 多半是 | 你这样做 | 贴给 AI 的原话 |
|---|---|---|---|
| 待填 |  |  | 「<现象>，完整输出如下：<贴原文>」 |
""",
    "docs/pipeline/上下文体检.md": """<!-- pipeline-scaffold -->
# 上下文体检（骨架·① 填实后请删掉上一行标记）

> 四格对账读数（细则 §4，人读人写、禁塞进 state.json）。

## ① 四个有效性指标当期读数

| 指标 | 当期读数 | 说明 |
|---|---|---|
| 冷启动轮数 |  |  |
| 对账不一致率 |  |  |
| 出题答错率 |  |  |
| 文档触发的返工次数 |  |  |

## ② 下次开窗口该贴哪几份模块卡

## ③ 出题验收答错的那些（答错那条＝文档要补的内容）

## ④ 小白版作废登记（哪句要重写；改了啥/为什么改归 ADR 与提交记录，不记这里）
""",
}

# v1.16.0：三份大计划产物骨架（标准/企业档 init 搭）。只给结构与逐节指引，不替执行者写内容；
# 12 维检查单与填实细则＝references/大计划产物模板.md（结构与该文件 §2/§3 一致，改结构两处同步改）。
PLAN_STUBS = {
    "docs/pipeline/设计纪要.md": """<!-- pipeline-scaffold -->
# 设计纪要 — <项目名>（骨架·① 填实后请删掉上一行标记）

> 核对日期：____-__-__ ｜ 对照的事实：<需求源＋状态版本；代码未施工时写明「行为条款为承诺非实现」>
> 填实要求与 12 维盘问决策检查单＝交付流水线 skill 的 `references/大计划产物模板.md`。

## 1. 交付物定义（铁律 12：谁用/怎么用/第一次交付长什么样——可验证）

## 2. 目的 / 用户 / 约束 / 成功标准

## 3. 盘问决策留痕（对照模板 §1 检查单 12 维逐项：每行=结论/理由/缺陷与翻车场景；需求没写的口径标【推定】；禁跳项）

## 4. 核心方案与取舍（含「为什么这样切」）

## 5. 范围裁剪（YAGNI：明确不做什么＋被问起的回答口径）

## 6. 已知缺陷（如实列，面试/汇报可主动披露）

## 7. 下一步
""",
    "docs/pipeline/技术选型记录.md": """<!-- pipeline-scaffold -->
# 技术选型记录 — <项目名>（骨架·② 填实后请删掉上一行标记）

> 核对日期：____-__-__ ｜ 对照的事实：<实测安装版本/本机环境读数>
> 填实要求＝流水线 SKILL.md §二 层0~15（每层 5~9 候选、缺陷如实、标准档走 ★+核心层；落盘固定在 docs/pipeline/ 防 ADR 换家）。

## 0. 层 0 · 项目目标与约束（决定后面所有层）

## 1. 各层候选表（标准档至少覆盖：语言与运行时/后端框架/数据存储/前端/测试；每候选带「缺陷/翻车场景」，只说优点＝卖货）

## 2. 实测证据（安装/启动/版本读数的命令与退出码，落盘 evidence/ 并在此给路径）

## 3. 冲突调和（单独最优 ≠ 组合最优：降级/替换/补位＋理由）

## 4. 跳过的层与「什么时候会需要它」
""",
    "docs/pipeline/PRD.md": """<!-- pipeline-scaffold -->
# PRD — <项目名>（骨架·③ 填实后请删掉上一行标记）

> 核对日期：____-__-__ ｜ 对照的事实：<需求源＋specs 条款号区间>｜ 行为条款为承诺/实现（择一）
> 结构与填实要求＝`references/大计划产物模板.md` §3（与 init 骨架一致，改结构两处同步）；行为条款只写进 specs/，本文件功能列表只写编号与一句话。宪章判据（spec_check C11）：5–8 条、每条可判定、零模糊词。

## 项目宪章（5–8 条，每条可判定：带数字/单位/不得禁止只允许这类硬词）

## 产品定位（一句话）

## 交付物定义（指针指设计纪要 §1，不重抄）

## 核心功能列表（P0/P1/P2；每行=编号｜一句话｜需求号；P2 无则写「经确认：无（日期，依据）」）

## 用户流程（按用户操作时序）

## 全局验收标准（机检/测试/走查/实测，全部指向落盘证据）

## 用户视角走查清单（逐条真点，禁 API 直调代替——铁律 12）

## 技术选型（指针指技术选型记录，不复述）
""",
}


def setup_stdout():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass


def now_str():
    return datetime.now().strftime(TS_FMT)


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha_head(v, n=12):
    return (v or "")[:n]


def die(code, msg):
    print("[E%d] %s" % (code, msg))  # v1.25.0：稳定错误 token——上游 CI/AI 可按 [E<码>] 机读，不依赖中文措辞
    raise SystemExit(code)


# ---------------------------------------------------------------- 路径与原子读写
class Ctx(object):
    def __init__(self, root, subdir=None):
        self.root = os.path.abspath(root)
        self.dir = os.path.join(self.root, subdir or DEFAULT_SUBDIR)
        self.state_path = os.path.join(self.dir, STATE_NAME)

    def rel(self, p):
        return os.path.relpath(p, self.root).replace("\\", "/") if p.startswith(self.root[:2]) else p

    def abspath(self, name):
        return os.path.join(self.dir, name)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def write_atomic(path, data, need_dir=True):
    """临时文件 → fsync → os.replace → 写后读回对账；不符即恢复改前内容。"""
    if need_dir:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
    old = read_bytes(path) if os.path.lexists(path) else None
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(tmp, path)
    except OSError:
        if os.path.lexists(tmp):
            os.remove(tmp)
        raise
    if read_bytes(path) != data:
        if old is None:
            os.remove(path)
        else:
            with open(path, "wb") as f:
                f.write(old)
                f.flush()
                os.fsync(f.fileno())
        die(1, "[写后读回不符] %s ⇒ 已回滚改前内容，本次未生效" % path)
    return True


# ---------------------------------------------------------------- 权威记录
def blank_state(project, tier, mode_source):
    active = TIER_STEPS.get(tier, TIER_STEPS["标准"])
    stages = {}
    for sid in STEP_ORDER:
        stages[sid] = {
            "name": STEP_NAME[sid],
            "state": "todo" if sid in active else "na",
            "input_version": "",
            "artifacts": [],
            "done_criteria": "",
            "recovery_point": "",
            "evidence": [],
        }
    return {
        "schema": SCHEMA_VERSION,
        "project": project,
        "mode": tier,
        "mode_source": mode_source,
        "rev": 1,
        "updated_at": now_str(),
        "status": "进行中",
        "stages": stages,
        "cards": {},
        "answers": {},
        "external_actions": [],
        "gates": {"security_L1": "pending", "outward_claims": "na",
                  "model_eval": "na", "multi_agent": "na"},
        "deps": {"snapshot_at": "", "files": [], "path_map": {},
                 "link_check": "unchecked", "broken_links": []},
        "docs": {"tier": "L0", "opt_out": [], "decided_at": now_str()},
        "sync_conflicts": [],
        "history": [{"rev": 1, "at": now_str(), "event": "init mode=%s" % tier}],
    }


def dump_json(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load_state(ctx, must=True):
    if not os.path.isfile(ctx.state_path):
        if must:
            die(3, "[缺权威记录] 找不到 %s ⇒ 先跑 init（旧台账要先 sync --from-md 并账）"
                % ctx.abspath(STATE_NAME))
        return None
    try:
        st = json.loads(read_bytes(ctx.state_path).decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        die(4, "[权威记录非法] %s 解析失败：%s（拒绝当成空状态继续跑）" % (ctx.abspath(STATE_NAME), e))
    if not isinstance(st, dict):
        die(4, "[权威记录非法] 顶层不是对象")
    if int(st.get("schema", 0) or 0) > SCHEMA_VERSION:
        die(4, "[权威记录非法] schema=%s 比本脚本支持的 %s 新 ⇒ 请更新脚本，别猜格式"
            % (st.get("schema"), SCHEMA_VERSION))
    if not isinstance(st.get("rev"), int):
        die(4, "[权威记录非法] rev 不是整数")
    for k in ("stages", "cards", "answers", "gates", "deps"):
        if not isinstance(st.get(k), (dict, list)):
            die(4, "[权威记录非法] %s 类型不对（%s）" % (k, type(st.get(k)).__name__))
    st.setdefault("external_actions", [])
    st.setdefault("sync_conflicts", [])
    st.setdefault("docs", {"tier": "L0", "opt_out": []})
    return st


def bump(st, event):
    st["rev"] = int(st.get("rev", 0)) + 1
    st["updated_at"] = now_str()
    st.setdefault("history", []).append({"rev": st["rev"], "at": st["updated_at"], "event": event})
    del st["history"][:-60]
    return st


def save_state(ctx, st, event, apply=True):
    if not apply:
        print("[干跑] 未写盘｜计划事件：%s" % event)
        return False
    write_atomic(ctx.state_path, dump_json(st).encode("utf-8"))
    return True


# ---------------------------------------------------------------- 租约锁（与 edit_lock 同口径）
def lock_path(ctx):
    return os.path.join(ctx.abspath(".locks"), LOCK_NAME)


def read_lock(path):
    if not os.path.isfile(path):
        return None
    try:
        return json.loads(read_bytes(path).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"_corrupt": True}


def lock_try(ctx, holder, takeover=False, lease=LOCK_LEASE_DEFAULT):
    path = lock_path(ctx)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            cur = read_lock(path)
            if cur and cur.get("_corrupt"):
                die(4, "[锁记录损坏] %s 不是合法 JSON ⇒ 互斥已失效；人工确认无人正在写之后删掉该文件" % path)
            if cur is None:
                continue
            age = time.time() - _lock_ts(cur)
            # 判陈旧只能按锁记录里写的租约算（卡05 edit_lock 同口径）；
            # 用来者的 lease 会把「别人留下的 1s 短租约」判成永远不陈旧。
            held_lease = int(cur.get("lease_seconds") or lease)
            stale = age > max(held_lease, STALE_GRACE)
            if cur.get("holder") == holder and not stale:
                # 同一把椅子的同一个人：续心跳即可（卡05 edit_lock 同口径）。
                # 缺这一段，用 --keep-lock 取过锁的窗口会把自己锁死到自己交锁为止。
                cur["heartbeat_at"] = now_str()
                cur["lease_seconds"] = lease
                write_atomic(path, json.dumps(cur, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
                             need_dir=False)
                print("[锁沿用] 本窗已持有（代次不变=%s）⇒ 续心跳，不算重取" % cur.get("generation"))
                return {"path": path, "holder": holder, "generation": int(cur.get("generation", 1))}
            if stale:
                rec_lease = int(cur.get("lease_seconds", lease) or lease)
                if not takeover:
                    die(7, "[租约过期] %s 已 %.0fs 无心跳（它自己登记的租约 %ss，与你带来的 %ss 无关）"
                              "⇒ **不自动接手**：确认对方真没在写，再加 --takeover（代次 +1，旧写者随后被拒）"
                        % (cur.get("holder"), age, rec_lease, lease))
                return _lock_takeover(path, cur, holder, lease)
            die(5, "[锁被持有] %s 正在写（%.0fs 前心跳，它登记的租约 %ss）⇒ 本次不碰权威记录。"
                       "等它 unlock，或只交自己的草稿走单写队列"
                  % (cur.get("holder"), age, int(cur.get("lease_seconds", lease) or lease)))
        rec = {"holder": holder, "generation": 1, "acquired_at": now_str(),
               "heartbeat_at": now_str(), "lease_seconds": lease, "pid": os.getpid()}
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2, sort_keys=True)
        return {"path": path, "holder": holder, "generation": rec["generation"]}
    die(2, "[锁异常] 反复取不到也放不下：%s" % path)


def _lock_ts(rec):
    for k in ("heartbeat_at", "acquired_at"):
        v = rec.get(k)
        if v:
            try:
                return time.mktime(datetime.strptime(v, TS_FMT).timetuple())
            except ValueError:
                pass
    return 0.0


def _lock_takeover(path, cur, holder, lease):
    mark = path + ".takeover"
    try:
        fd = os.open(mark, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        die(5, "[接管被抢先] 另一个窗口正在接管 ⇒ 本次不动权威记录")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("%s %s\n" % (holder, now_str()))
    gen = int(cur.get("generation", 1)) + 1
    rec = dict(cur)
    rec.update({"holder": holder, "generation": gen, "acquired_at": now_str(),
                "heartbeat_at": now_str(), "lease_seconds": lease,
                "taken_over_from": cur.get("holder")})
    write_atomic(path, json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
                 need_dir=False)
    if os.path.lexists(mark):
        os.remove(mark)
    print("[接管成功] 代次 %s → %s，新持有者=%s（旧写者再写会撞 rev/代次校验）"
          % (cur.get("generation"), gen, holder))
    return {"path": path, "holder": holder, "generation": gen}


def lock_release(ctx, token):
    if not token:
        return
    rec = read_lock(token["path"])
    if rec and rec.get("generation") == token.get("generation") and rec.get("holder") == token.get("holder"):
        if os.path.lexists(token["path"]):
            os.remove(token["path"])


# ---------------------------------------------------------------- 渲染（派生物＝纯函数）
def _cards(st):
    out = []
    for cid, c in (st.get("cards") or {}).items():
        if isinstance(c, dict):
            row = dict(c)
            row["id"] = cid
            row.setdefault("state", "todo")
            out.append(row)
    order = {"doing": 0, "blocked": 1, "todo": 2, "done": 3}
    out.sort(key=lambda r: (order.get(r["state"], 4), r["id"]))
    return out


def render_ledger(st):
    """台账.md：只由 st 决定，不含当前时钟 ⇒ 同一状态重复渲染逐字相同（不动点）。"""
    L = [AUTO_BEGIN]
    L.append("# %s · 交付流水线台账（生成视图＝派生物，真源是 %s）" % (st.get("project", "未命名"), STATE_NAME))
    L.append("")
    L.append("- 权威记录：`%s`｜本视图生成时 rev=%s｜模式：%s（定档来源：%s）｜项目状态：%s"
             % (STATE_NAME, st.get("rev"), st.get("mode"), st.get("mode_source", "?"),
                st.get("status", "进行中")))
    L.append("- 改状态的正确动作：改 `%s`（跑 stage/card/gate/answer/action）→ 本文件自动重写。"
             % STATE_NAME)
    L.append("- 手改本文件会被 `check` 判红；旧版「以 updated_at 较新者为准互相覆盖」已废除（会丢另一侧字段）。")
    L.append("")
    L.append("## 一、阶段进度（四字段齐才允许标 done）")
    L.append("")
    L.append("| 步 | 阶段 | 状态 | 输入版本 | 产物位置 | 完成条件 | 恢复点 |")
    L.append("|---|---|---|---|---|---|---|")
    for sid in STEP_ORDER:
        s = st.get("stages", {}).get(sid)
        if not s:
            continue
        ev = s.get("evidence") or []
        L.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            sid, s.get("name", ""), s.get("state", "todo"),
            s.get("input_version") or "—",
            "、".join(s.get("artifacts") or []) or "—",
            (s.get("done_criteria") or "—") + (("（证据：%s）" % "、".join(ev)) if ev else ""),
            s.get("recovery_point") or "—"))
    L.append("")
    L.append("## 二、在做（最多一张）")
    L.append("")
    doing = [c for c in _cards(st) if c["state"] == "doing"]
    if doing:
        L.append("| 卡 | 标题 | 执行者 | 开工时间 | 验收 | 备注 |")
        L.append("|---|---|---|---|---|---|")
        for c in doing:
            L.append("| %s | %s | %s | %s | %s | %s |" % (
                c["id"], c.get("title", ""), c.get("executor") or "—", c.get("started") or "—",
                c.get("acceptance") or "—", c.get("note") or "—"))
    else:
        L.append("_（无卡在「在做」）_")
    L.append("")
    L.append("## 三、待执行（按依赖顺序）")
    L.append("")
    todo = [c for c in _cards(st) if c["state"] == "todo"]
    if todo:
        L.append("| 卡 | 标题 | 依赖 | 可并行 | 验收要点 |")
        L.append("|---|---|---|---|---|")
        for c in todo:
            L.append("| %s | %s | %s | %s | %s |" % (
                c["id"], c.get("title", ""), "、".join(c.get("depends") or []) or "—",
                "是" if c.get("parallel_ok") else "否", c.get("acceptance") or "—"))
    else:
        L.append("_（无待执行卡）_")
    L.append("")
    L.append("## 四、做完")
    L.append("")
    done = [c for c in _cards(st) if c["state"] == "done"]
    if done:
        for c in done:
            L.append("- [x] %s %s（%s%s%s）" % (
                c["id"], c.get("title", ""), c.get("metric") or "无实测数字",
                ("，" + c["date"]) if c.get("date") else "",
                ("，模块：" + "、".join(c["modules"])) if c.get("modules") else ""))
    else:
        L.append("_（还没有完成的卡）_")
    L.append("")
    L.append("## 五、阻塞区")
    L.append("")
    blocked = [c for c in _cards(st) if c["state"] == "blocked"]
    if blocked:
        L.append("| 卡 | 阻塞原因 | 需要谁 |")
        L.append("|---|---|---|")
        for c in blocked:
            L.append("| %s | %s | %s |" % (c["id"], c.get("note") or "—", c.get("who") or "—"))
    else:
        L.append("_（无阻塞）_")
    L.append("")
    L.append("## 六、门禁（未 passed 不许收尾／投递）")
    L.append("")
    L.append("| 门禁 | 值 |")
    L.append("|---|---|")
    for k in sorted(st.get("gates", {})):
        L.append("| %s | %s |" % (k, st["gates"][k]))
    L.append("")
    L.append("## 七、已问过／已授权（不重复确认清单）")
    L.append("")
    ans = st.get("answers") or {}
    if ans:
        L.append("| 事项 | 结论 | 来源 | 记录时间 |")
        L.append("|---|---|---|---|")
        for k in sorted(ans):
            a = ans[k]
            L.append("| %s | %s | %s | %s |" % (k, a.get("answer", ""), a.get("source", "?"), a.get("at", "")))
    else:
        L.append("_（暂无登记。向用户要一个决定并拿到答复后，跑 `answer` 记进权威记录，后续会话不再问同一件事）_")
    L.append("")
    L.append("## 八、外部动作账（已生效的不许重做）")
    L.append("")
    acts = st.get("external_actions") or []
    if acts:
        L.append("| 动作 | 类型 | 幂等键 | 已生效 | 时间 | 证据 |")
        L.append("|---|---|---|---|---|---|")
        for a in acts:
            L.append("| %s | %s | %s | %s | %s | %s |" % (
                a.get("id"), a.get("kind", "—"), a.get("idem_key") or "—",
                "是" if a.get("effect_applied") else "否", a.get("at", ""), a.get("evidence") or "—"))
    else:
        L.append("_（无外部动作记录）_")
    L.append("")
    L.append("## 九、依赖快照（版本＋哈希＋路径映射）与链接检查")
    L.append("")
    d = st.get("deps", {})
    L.append("- 快照时间：%s｜已登记 %d 件｜依赖链接检查：%s" % (
        d.get("snapshot_at") or "未做", len(d.get("files") or []), d.get("link_check", "unchecked")))
    for f in (d.get("files") or []):
        L.append("  - `%s` ← %s %s（sha256 %s…）" % (
            f.get("path"), f.get("source", "—"), f.get("version", ""), sha_head(f.get("sha256"))))
    pmap = d.get("path_map") or {}
    for k in sorted(pmap):
        v = pmap[k]
        leak = ("  🔴 本机绝对路径（发布前必须改成相对形态或删掉）"
                if re.match(r"^[A-Za-z]:[\\/]", v) or v.startswith("/") else "")
        L.append("  - 路径映射：%s → %s%s" % (k, v, leak))
    for b in (d.get("broken_links") or []):
        L.append("  - 🔴 断链：%s" % b)
    L.append("")
    L.append("## 十、文档层（L0 默认落地；显式退出必须带理由，v1.14.0）")
    L.append("")
    docs = st.get("docs") or {}
    opted = {o.get("path"): o for o in (docs.get("opt_out") or [])}
    L.append("- 文档档：%s｜默认清单 %d 件｜已显式退出 %d 件（逐件在盘状态跑 `docs --show`；"
             "① done 后缺件会被 `check` 判红）"
             % (docs.get("tier", "L0"), len(DOC_L0_FILES), len(opted)))
    for f in DOC_L0_FILES:
        o = opted.get(f)
        if o:
            L.append("  - ⊘ %s —— 退出理由：%s（%s）" % (f, o.get("why", "?"), o.get("at", "")))
        else:
            L.append("  - ○ %s" % f)
    L.append("")
    conf = st.get("sync_conflicts") or []
    if conf:
        L.append("## 十一、待人工裁决（并账时两侧值不同，脚本未自动取舍）")
        L.append("")
        for c in conf:
            L.append("- %s" % c)
        L.append("")
    L.append(AUTO_END)
    return "\n".join(L) + "\n"


def render_board(st):
    L = [AUTO_BEGIN]
    L.append("# %s · 任务板（生成视图＝派生物；认领状态写回 %s）" % (st.get("project", "未命名"), STATE_NAME))
    L.append("")
    L.append(AUTO_MARK and "认领：`card --id cN --state doing --executor 谁`；做完：`--state done --metric 实测数字`。")
    L.append("")
    todo = [c for c in _cards(st) if c["state"] in ("todo", "blocked")]
    if not todo:
        L.append(AUTO_END)
        return "\n".join(L) + "\n"
    for c in todo:
        L.append("- [ ] **%s %s**｜依赖：%s｜可并行：%s｜验收：%s" % (
            c["id"], c.get("title", ""), "、".join(c.get("depends") or []) or "—",
            "是" if c.get("parallel_ok") else "否", c.get("acceptance") or "—"))
        if c.get("note"):
            L.append("  - 阻塞：%s（需要 %s）" % (c["note"], c.get("who") or "用户"))
    L.append("")
    L.append(AUTO_END)
    return "\n".join(L) + "\n"


VIEW_FILE = {"ledger": LEDGER_NAME, "board": BOARD_NAME}
VIEW_FN = {"ledger": render_ledger, "board": render_board}


def render_all(ctx, st, apply=True, views=("ledger", "board")):
    """写派生文件；**空内容不建文件**（不产空文档堆），非派生文件一律拒写（退 8）。"""
    made, skipped = [], []
    allowed = TIER_FILES.get(st.get("mode", "标准"), [])
    for v in views:
        fname = VIEW_FILE[v]
        if fname not in allowed:
            skipped.append("%s（本档不建）" % fname)
            continue
        if v == "ledger" and st.get("status") == "已归档":
            # v1.25.0：已归档项目不再生成活台账（否则 archive 刚改名、下一次写命令又复活一份——
            # 正是 C19 要抓的「归档后台账复活」；归档态的台账读数以 台账_归档_*.md 为准）
            skipped.append("%s（项目已归档，不生成活台账）" % fname)
            continue
        text = VIEW_FN[v](st)
        if v == "board" and not (st.get("cards") or {}):
            skipped.append("%s（无卡可列 ⇒ 不建空看板）" % fname)
            continue
        target = ctx.abspath(fname)
        if os.path.lexists(target) and AUTO_MARK not in read_bytes(target).decode("utf-8", "replace")[:800]:
            die(8, "[拒写] %s 没有 AUTO-BEGIN 标记＝人手写的同名文件 ⇒ 不覆盖。"
                      "请先人工确认并改名，或把内容并账进权威记录" % target)
        if apply:
            write_atomic(target, text.encode("utf-8"), need_dir=False)
            made.append(fname)
        else:
            made.append(fname + "(干跑)")
    for s in skipped:
        print("  · 未生成 %s" % s)
    return made


# ---------------------------------------------------------------- check：派生物对账
def structure_anchor_misses(ctx, st):
    """v1.17.0 结构锚校验：① done 后，填实产物应保留骨架结构（锚=无节号子串）。
    返回 [(文件, 缺的锚...)]；只查「在盘且非骨架」的件——缺件/骨架态是硬闸的事，这里不管。
    缺失是「警」不是「红」：老项目与刻意自定义结构不炸 check，但必须当场点名。"""
    if (st.get("stages", {}).get("①") or {}).get("state") != "done":
        return []
    misses = []
    for f, anchors in STRUCTURE_ANCHORS.items():
        p = os.path.join(ctx.root, f)
        if not os.path.isfile(p):
            continue
        text = read_bytes(p).decode("utf-8", "replace")
        if SCAFFOLD_MARK in text:      # 骨架态归硬闸管
            continue
        lack = [a for a in anchors if a not in text]
        if lack:
            misses.append((f, lack))
    return misses


def cmd_check(ctx, st):
    problems = []
    allowed = TIER_FILES.get(st.get("mode", "标准"), [])
    for v in ("ledger", "board"):
        fname = VIEW_FILE[v]
        target = ctx.abspath(fname)
        if v == "ledger" and st.get("status") == "已归档":
            # v1.25.0：归档态不要求活台账（render 也不再生成）；活台账反而该不存在（C19 抓复活）
            if os.path.lexists(target):
                problems.append("项目已归档但活台账.md 仍在（spec_check C19 同口径；跑 archive 或人工移除）")
            continue
        if fname not in allowed:
            if os.path.lexists(target):
                problems.append("%s 不该存在（%s 档不建此件）" % (fname, st.get("mode")))
            continue
        if not os.path.lexists(target):
            if v == "board" and not (st.get("cards") or {}):
                continue
            problems.append("缺派生物 %s ⇒ 跑 render --apply" % fname)
            continue
        disk = read_bytes(target).decode("utf-8", "replace")
        if AUTO_MARK not in disk[:800]:
            problems.append("%s 没有 AUTO-BEGIN 标记＝已脱离权威记录" % fname)
        elif disk != VIEW_FN[v](st):
            problems.append("%s 与权威记录漂移（%d 行不同）⇒ 真源只有 %s：跑 render --apply 重渲染；"
                            "若 md 里有 state 缺的事实，先跑 sync --from-md 逐字段并账"
                            % (fname, _diff_lines(disk, VIEW_FN[v](st)), STATE_NAME))
    for sid, s in (st.get("stages") or {}).items():
        if not isinstance(s, dict) or s.get("state") != "done":
            continue
        miss = [k for k in STAGE_REQUIRED_FIELDS if not s.get(k)]
        if miss:
            problems.append("阶段 %s 标了 done 但四字段缺：%s（done 必须有输入版本/产物/完成条件/恢复点）"
                            % (sid, "、".join(miss)))
    for cid, c in (st.get("cards") or {}).items():
        if isinstance(c, dict) and c.get("state") == "done" and not c.get("metric"):
            problems.append("卡 %s 标了 done 但没有实测数字 metric（台账铁律：状态只有实测值）" % cid)
    g = st.get("gates", {})
    if st.get("status") == "已完成":
        if g.get("security_L1") != "passed":
            problems.append("项目状态写「已完成」但 security_L1=%s ⇒ 只认 passed（L1 常开，"
                            "无「跳过」一说，铁律 10）" % g.get("security_L1"))
        if g.get("outward_claims") not in ("passed", "na"):
            problems.append("项目状态写「已完成」但对外口径门禁=%s（铁律 11：只认 passed，或显式 na=确无对外物）"
                            % g.get("outward_claims"))
    gone, stub = doc_layer_gaps(ctx, st)   # v1.14.0：① done 之后文档层仍须在盘
    for f in gone:
        problems.append("① 已标 done 但文档层缺件：%s（done 之后文件还在盘上才算数；"
                        "要退出跑 docs --opt-out --why）" % f)
    for f in stub:
        problems.append("① 已标 done 但文档层仍是骨架未填实：%s（填实，或 docs --opt-out 显式退出）" % f)
    anchor_miss = structure_anchor_misses(ctx, st)   # v1.17.0 结构锚：警不是红
    if problems:
        print("[检查不通过] %d 条：" % len(problems))
        for p in problems:
            print("  ✗ %s" % p)
        for f, lack in anchor_miss:
            print("  ⚠ %s 缺结构锚：%s（结构应与 init 骨架一致，模板=references/大计划产物模板.md 与骨架本体；"
                  "老项目或刻意自定义请人工确认后忽略）" % (f, "、".join(lack)))
        return 1
    if anchor_miss:
        for f, lack in anchor_miss:
            print("[警] %s 缺结构锚：%s（结构应与 init 骨架一致；老项目或刻意自定义请人工确认后忽略——"
                  "这是警不是红，check 照常通过）" % (f, "、".join(lack)))
    print("[检查通过] 权威 rev=%s｜派生物与 state.json 逐字一致｜done 项四字段齐" % st.get("rev"))
    return 0


def _diff_lines(a, b):
    ae, be = a.splitlines(), b.splitlines()
    n = 0
    for i in range(max(len(ae), len(be))):
        if (ae[i] if i < len(ae) else None) != (be[i] if i < len(be) else None):
            n += 1
    return n


# ---------------------------------------------------------------- sync --from-md：逐字段并账
ZONE_RE = re.compile(r"^#{1,3}\s*[一二三四五六七八九十]+\s*、\s*(.+?)\s*$")
DONE_ROW_RE = re.compile(r"^\s*[-*]\s*\[[ xX]?\]\s*(?:卡)?\s*#?(\d+)\s*([^（(]*)(?:[（(]([^）)]*)[）)])?")
ROW_RE = re.compile(r"^\|\s*(?:卡)?\s*#?(\d+)\s*([^|\n]*)")
MODE_RE = re.compile(r"模式[:：]\s*(轻量|标准|企业|复杂)")
STATUS_RE = re.compile(r"(?:当前)?状态[:：]\s*(进行中|已完成|已归档)")
ZONE_KEY = {"在做": "doing", "待执行": "todo", "阻塞": "blocked", "做完": "done"}


def parse_md_ledger(path):
    """旧台账.md → 字段集（按卡号切开），不是整份文本——这是「不再整覆盖」的技术前提。"""
    cards, mode, status, notes = {}, "", "", []
    zone = ""
    for line in read_bytes(path).decode("utf-8", "replace").splitlines():
        m = ZONE_RE.match(line)
        if m:
            z = m.group(1)
            zone = next((ZONE_KEY[k] for k in ZONE_KEY if k in z), "")
            continue
        mm = MODE_RE.search(line)
        if mm:
            mode = ALIASES.get(mm.group(1), mm.group(1))
        sm = STATUS_RE.search(line)
        if sm:
            status = sm.group(1)
        if zone == "done":
            dm = DONE_ROW_RE.match(line)
            if dm:
                cards.setdefault("c%s" % dm.group(1), {
                    "title": (dm.group(2) or "").strip(),
                    "detail": (dm.group(3) or "").strip(),
                    "state": "done"})
        elif zone in ("doing", "todo", "blocked"):
            cm = ROW_RE.match(line)
            if cm and not set(cm.group(2)) <= set("-— :|"):
                cid = "c%s" % cm.group(1)
                cards.setdefault(cid, {"title": (cm.group(2) or "").strip().split("|")[0].strip(),
                                       "state": zone})
        elif line.startswith(">") and AUTO_MARK not in line:
            notes.append(line.lstrip("> ").strip())
    return {"cards": cards, "mode": mode, "status": status, "notes": notes}


def cmd_sync(ctx, st, md_path, apply=True):
    if not os.path.isfile(md_path):
        die(2, "[缺文件] 读不到 %s" % md_path)
    md = parse_md_ledger(md_path)

    def mutate(_ctx, s):
        """v1.25.0 收编进 guarded_write（旧版直调 write_atomic 无锁无 rev 校验——绕行写路径）。
        吸收/冲突判定基于锁内重读的 s 现算；无吸收无冲突 ⇒ 记「无需并账」且走 DryRunSkip 不落盘。"""
        absorbed, conflicts = [], []
        cards = s.setdefault("cards", {})
        for cid, row in sorted(md["cards"].items()):
            if cid not in cards:
                entry = {"title": row["title"], "state": row["state"], "source": "md 并账"}
                if row["state"] == "done" and row.get("detail"):
                    entry["metric"] = row["detail"]
                cards[cid] = entry
                absorbed.append("%s %s←md（state 里原本没有这条）" % (cid, row["state"]))
                continue
            cur = cards[cid]
            if cur.get("state") != row["state"]:
                conflicts.append("%s.state：state=%s ／ md=%s ⇒ 保留 state（不整覆盖），要改请跑 card --state"
                                 % (cid, cur.get("state"), row["state"]))
            if row["title"] and not cur.get("title"):
                cur["title"] = row["title"]
                absorbed.append("%s.title（state 里为空，从 md 补）" % cid)
            elif row["title"] and cur.get("title") and row["title"] != cur["title"]:
                conflicts.append("%s.title：state=「%s」／ md=「%s」⇒ 保留 state" % (cid, cur["title"], row["title"]))
            if row["state"] == "done" and row.get("detail") and not cur.get("metric"):
                cur["metric"] = row["detail"]
                absorbed.append("%s.metric" % cid)
            elif row["state"] == "done" and row.get("detail") and cur.get("metric") and row["detail"] != cur["metric"]:
                conflicts.append("%s.metric：state=「%s」／ md=「%s」⇒ 保留 state" % (cid, cur["metric"], row["detail"]))
        if md["mode"] and md["mode"] != s.get("mode"):
            conflicts.append("mode：state=%s ／ md=%s ⇒ 保留 state（改档要显式跑 mode --set）" % (s.get("mode"), md["mode"]))
        if md["status"] and md["status"] != s.get("status"):
            conflicts.append("status：state=%s ／ md=%s ⇒ 保留 state" % (s.get("status"), md["status"]))
        for n in md["notes"]:
            if n not in (s.get("notes") or []):
                s.setdefault("notes", []).append(n)
                absorbed.append("note")
        s["sync_conflicts"] = conflicts
        print("[逐字段并账] 来自 %s｜吸收 %d 项｜冲突 %d 项（冲突一律不自动取舍，写进台账「待人工裁决」）"
              % (md_path, len(absorbed), len(conflicts)))
        for a in absorbed:
            print("  + %s" % a)
        for c in conflicts:
            print("  ≠ %s" % c)
        if not absorbed and not conflicts:
            print("  （旧 md 每条都在权威记录里有名有姓 ⇒ 无需并账）")
            return _NO_CHANGE
        return 0

    rc = guarded_write(ctx, _WriteShim(apply=apply), "sync --from-md %s" % os.path.basename(md_path), mutate)
    return 0 if rc == 0 else rc


# ---------------------------------------------------------------- 写命令的公共闸
_NO_CHANGE = "__no_change__"  # mutate 返回它 ⇒ 判定「无可写变化」，不 bump 不落盘（幂等出口）


class _WriteShim(object):
    """给不走 argparse 的内部调用（如 selftest 直调 cmd_sync）补齐 guarded_write 所需的参数默认值。
    v1.25.0 的 B18 教训同款：新增参数必须同步这里，否则 AttributeError。"""

    def __init__(self, apply=True, expect_rev=None, holder=None, takeover=False, lease=LOCK_LEASE_DEFAULT):
        self.apply = bool(apply)
        self.expect_rev = expect_rev
        self.holder = holder
        self.takeover = takeover
        self.lease = lease
        self.keep_lock = False


def guarded_write(ctx, args, event, mutate):
    """取锁 → 锁内重读 → rev 校验 → 改 → 写 → 重渲染 → 放锁。
    v1.25.0 修 TOCTOU：校验点必须与写入点同在一个串行化段内（对照 RFC 9110 If-Match
    由服务器在处理写请求时比对、etcd txn 的 compare-then-put 同段语义）——旧版读与校验
    都在取锁之前，两窗「读得早、验得早、写得晚」可双双过闸后写盘，后写整份覆盖先写
    （lost update 且双双退 0）。现在锁内重读重验；不带 --holder 时无锁，退化为
    「mutate 前重读」，校验点同样紧贴写入点。"""
    if args.expect_rev is None and not args.holder:
        print("🔴 [裸写] 本次写入不带 --expect-rev 也不带 --holder ⇒ 无版本保护。"
              "多窗并行时可能覆盖他人改动——确认单写者再继续，或补 --expect-rev <你读到的 rev>。")
    token = lock_try(ctx, args.holder, takeover=args.takeover, lease=args.lease) if args.holder else None
    try:
        st = load_state(ctx)  # 锁内重读（v1.25.0：校验点=写入点）
        if args.expect_rev is not None and int(args.expect_rev) != int(st.get("rev", 0)):
            print("[版本冲突] 你读到 rev=%s，当前 rev=%s ⇒ 拒绝写入。"
                  "（旧版在这一步按时间戳整份覆盖，把另一侧的字段改动抹掉了）"
                  % (args.expect_rev, st.get("rev")))
            print("          请重读 state.json 把改动重放在新 rev 上；确要强行覆盖也仍然禁止——改用单写队列。")
            return 6
        rc = mutate(ctx, st)
        if rc == _NO_CHANGE:
            print("[干跑] 无可写变化，未写盘｜事件：%s" % event)
            return 0
        if rc:
            return rc
        if not args.apply:
            print("[干跑] 未写盘｜事件：%s" % event)
            return 0
        bump(st, event)
        write_atomic(ctx.state_path, dump_json(st).encode("utf-8"))
        audit_append(ctx, st, event)
        render_all(ctx, st, apply=True)
        print("[已写] %s｜rev=%s" % (ctx.state_path, st["rev"]))
        return 0
    finally:
        if token and not getattr(args, "keep_lock", False):
            lock_release(ctx, token)


def audit_append(ctx, st, event):
    """v1.25.0：history 外置 append-only 归档——state.json 的 history 只留最近 60 条（快照保持轻），
    每次成功写入同步追加一行 JSON 到 <状态目录>/history.log（永不裁剪）。审计线不再随快照蒸发。
    追加失败只警告不回滚主写（权威已落盘；审计尽力而为，缺口感知）。"""
    try:
        line = json.dumps({"rev": st.get("rev"), "at": st.get("updated_at"), "event": event},
                          ensure_ascii=False, sort_keys=True)  # 单行紧凑——log 一行一事件，禁 indent
        with open(os.path.join(ctx.dir, "history.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        print("⚠ [审计] history.log 追加失败（主写不受影响）：%s" % e)


# ---------------------------------------------------------------- 子命令
def scaffold_docs(ctx, st):
    """v1.14.0：init 默认搭 L0 文档骨架——让「落地文档」成为阻力最小的路（golden path：
    合规是路径的属性，不是审查步骤）。只搭缺的、棕地已有件一律不覆盖；显式不建走 --docs-tier none。"""
    docs = st.get("docs") or {}
    if docs.get("tier") == "none":
        print("[文档层] 显式不建（--docs-tier none，建账时用户已明确表态）")
        return []
    made = []
    for f in DOC_L0_FILES:
        p = os.path.join(ctx.root, f)
        if os.path.lexists(p):
            continue
        write_atomic(p, DOC_STUBS[f].encode("utf-8"), need_dir=True)
        made.append(f)
    if made:
        print("[文档层] 已搭骨架 %d 件（含 %s 标记，① 填实后删标记）：%s"
              % (len(made), SCAFFOLD_MARK, "、".join(made)))
    else:
        print("[文档层] 无一件可搭（盘上已全部存在——棕地项目不覆盖已有件）")
    return made


def scaffold_plans(ctx, st):
    """v1.16.0：init 额外搭三份大计划产物骨架（设计纪要/技术选型记录/PRD）——结构预设，
    不靠执行者现场发挥（三平行窗实测：同一规则跑出两种覆盖面）。只搭缺的、棕地不覆盖；
    轻量档不搭（索引原文：轻量①一句话版、③规格层可省——骨架反而是负担）。"""
    if st.get("mode") == "轻量":
        print("[计划骨架] 轻量档不搭三份大计划产物骨架（①一句话版/③可省，宁少不空）")
        return []
    made = []
    for f in PLAN_STUB_FILES:
        p = os.path.join(ctx.root, f)
        if os.path.lexists(p):
            continue
        write_atomic(p, PLAN_STUBS[f].encode("utf-8"), need_dir=True)
        made.append(f)
    if made:
        print("[计划骨架] 已搭 %d 份大计划产物骨架（含 %s 标记，对应阶段填实后删标记；12 维检查单=references/大计划产物模板.md）：%s"
              % (len(made), SCAFFOLD_MARK, "、".join(made)))
    else:
        print("[计划骨架] 无一份可搭（盘上已全部存在——棕地项目不覆盖已有件）")
    return made


def cmd_init(ctx, args):
    if os.path.lexists(ctx.state_path) and not args.force:
        print("[已存在] %s ⇒ 不覆盖（重来加 --force；旧台账用 sync --from-md 并账）" % ctx.state_path)
        return 0
    if not args.apply:
        print("[干跑] 将创建 %s｜mode=%s｜文档档=%s｜该档产物：%s"
              % (ctx.state_path, args.mode, args.docs_tier, "、".join(TIER_FILES[args.mode])))
        return 0
    os.makedirs(ctx.dir, exist_ok=True)
    st = blank_state(args.project or os.path.basename(ctx.root), args.mode, args.mode_source)
    st["docs"] = {"tier": args.docs_tier, "opt_out": [], "decided_at": now_str()}
    st["history"][0]["event"] = "init mode=%s docs=%s" % (args.mode, args.docs_tier)
    write_atomic(ctx.state_path, dump_json(st).encode("utf-8"))
    st = load_state(ctx)
    made = render_all(ctx, st, apply=True)
    made += scaffold_docs(ctx, st)
    made += scaffold_plans(ctx, st)
    print("[已建] %s｜rev=1｜派生物：%s" % (ctx.state_path, "、".join(made) or "无"))
    return 0


KNOWN_SIGNALS = {
    # 定档用（decide_tier）
    "files_touched", "modules_touched", "cards", "team_agents", "estimated_days",
    "has_code", "from_zero", "new_feature",
    # 点亮可选重步骤用（enabled_steps）
    "deploy", "public_net", "compliance", "eval_set", "external_claims", "parallel_cards",
}


def decide_tier(sig):
    """按任务范围自动选 轻量/标准/企业（外部审查建议 1）。计分判据写死在这里，可复算、可被探针打红。"""
    why = []
    score = {"轻量": 0, "标准": 0, "企业": 0}
    if not sig:
        # 🔴 「没信号」和「信号评估过且指向最低档」是两回事：不如实标出来，
        #    落最低档看起来就像结论，下次没人再补信号。
        return "轻量", [], ["未收到任何信号 ⇒ 落最低档（宁少步骤也不产空文档堆）。"
                            "**本行是默认值不是判定**，别当成「已经评估过」的依据"]
    files = _num(sig.get("files_touched"))
    modules = _num(sig.get("modules_touched"))
    cards = _num(sig.get("cards"))
    team = _num(sig.get("team_agents"), 1)
    days = _num(sig.get("estimated_days"))
    has_code = bool(sig.get("has_code"))
    from_zero = bool(sig.get("from_zero"))
    new_feature = bool(sig.get("new_feature"))
    if not has_code and not from_zero and files <= 3:
        score["轻量"] += 3
        why.append("不动代码、非从零、涉及文件 ≤3 ⇒ 轻量 +3")
    if files <= 3 and modules <= 1 and cards <= 1 and not from_zero:
        score["轻量"] += 2
        why.append("单模块单卡 ⇒ 轻量 +2")
    if files >= 4:
        score["标准"] += 2
        why.append("涉及文件 ≥4 ⇒ 标准 +2")
    if modules >= 2:
        score["标准"] += 2
        why.append("跨 ≥2 模块 ⇒ 标准 +2")
    if new_feature:
        score["标准"] += 2
        why.append("新功能（非纯改动）⇒ 标准 +2")
    if cards >= 2:
        score["标准"] += 1
        why.append("卡数 ≥2 ⇒ 标准 +1")
    if from_zero:
        score["企业"] += 2
        why.append("从零起项目 ⇒ 企业 +2")
    if team >= 2:
        score["企业"] += 2
        why.append("多分身／多人协作 ⇒ 企业 +2")
    if days >= 3:
        score["企业"] += 1
        why.append("预估 ≥3 天 ⇒ 企业 +1")
    if _num(sig.get("modules_touched")) > 4 or cards > 8:
        score["企业"] += 2
        why.append("模块 >4 或卡数 >8 ⇒ 企业 +2")
    top = max(score.values())
    winners = [k for k, v in score.items() if v == top]
    if len(winners) > 1:
        # 空信号已在开头返回，不会走到"三档全 0 分"；真出现同分就是该问人，不硬猜
        tie = sorted(winners)
        why.append("平档（%s 同分 %s）⇒ 置信度不足，需要用户拍板" % ("／".join(tie), top))
        return None, tie, why
    return winners[0], [], why


def _num(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def enabled_steps(tier, sig):
    """可选重步骤按项目特征点亮，不全部默认跑（外部审查建议 6）。"""
    on, off = [], []
    on.append(("security_L1", "常开（铁律 10 不可跳过）：静态自检＋敏感信息／本机绝对路径泄漏扫描，"
                              "纯文档项目同样要跑——本 skill 发布仓的泄漏事故就是它抓的"))
    if bool(sig.get("deploy")) or bool(sig.get("public_net")):
        on.append(("security_L2", "要上线／公网暴露 ⇒ 上线前档"))
    else:
        off.append(("security_L2", "未部署／不公网 ⇒ 不跑"))
    if tier == "企业" or bool(sig.get("compliance")):
        on.append(("security_L3", "企业档或书面合规要求 ⇒ 企业级档"))
    else:
        off.append(("security_L3", "非企业档且无合规要求 ⇒ 不跑"))
    if bool(sig.get("eval_set")):
        on.append(("model_eval", "已有评测集／指标基线 ⇒ 全量评测"))
    else:
        off.append(("model_eval", "无评测集 ⇒ 不跑（不编基线）"))
    if _num(sig.get("parallel_cards")) >= 3 and _num(sig.get("team_agents")) >= 2:
        on.append(("multi_agent", "无依赖卡 ≥3 且多分身 ⇒ 并行派遣"))
    else:
        off.append(("multi_agent", "默认接力，不并行"))
    if bool(sig.get("external_claims")):
        on.append(("outward_claims", "有对外陈述物（README／简历／汇报）⇒ ⑭ 必过"))
    else:
        off.append(("outward_claims", "无对外物 ⇒ 记 na"))
    if tier == "轻量":
        keep = ("security_L1", "outward_claims")
        on2 = [x for x in on if x[0] in keep]
        for n, w in on:
            if n not in keep:
                off.append((n, "轻量档不跑（该档步骤集合=%s）" % " ".join(TIER_STEPS["轻量"])))
        on = on2
    return on, off


def cmd_mode(ctx, args):
    if args.decide:
        sig = parse_signals(args.signal)
        tier, tie, why = decide_tier(sig)
        print("[任务范围信号] %s" % "、".join("%s=%s" % (k, v) for k, v in sorted(sig.items())))
        unknown = sorted(set(sig) - KNOWN_SIGNALS)
        if unknown:
            # 不点名就等于默许「写了信号＝参与了判定」——假信号比没信号更危险
            print("🔴 [不参与计分的信号键] %s ⇒ 拼错或本尺子不认，别把它们当成已评估的依据"
                  % "、".join(unknown))
        if tier is None:
            print("[自动选档] 判不了（%s 同分）⇒ **只在这种平档情形问用户**，别每阶段固定提问" % "／".join(tie))
            for w in why:
                print("  · %s" % w)
            return 1
        print("[自动选档] %s%s" % (tier, "（自动定档，告知即可，不追问）" if not args.ask else ""))
        for w in why:
            print("  · %s" % w)
        on, off = enabled_steps(tier, sig)
        print("\n[该档产物文件] %s" % "、".join(TIER_FILES[tier]))
        print("[该档步骤] %s" % " ".join(TIER_STEPS[tier]))
        print("[跳过的步骤] %s（不建对应文件）" % " ".join(s for s in STEP_ORDER if s not in TIER_STEPS[tier]))
        print("\n[按特征启用的可选步骤]")
        for n, w in on:
            print("  ✓ %s —— %s" % (n, w))
        print("[默认不跑]")
        for n, w in off:
            print("  ✗ %s —— %s" % (n, w))
        if _num(sig.get("team_agents"), 1) >= 2:
            # v1.28.0 ⑦ 大团队边界：命中即声明外挂，不给 skill 建权限体系（判据=模式判定.md §四）
            print("\n[team 外挂声明] team_agents≥2 ⇒ 权限/评审流/审计外挂 GitHub，本流水线不建权限体系：")
            print("  · 分支保护四件套：必需审批数／必需状态检查／CODEOWNERS 指到人／签名提交")
            print("  · 审计面：history.log（append-only）＋各 gate 的 evidence 落盘＝「控制运行记录」最小形态，可直接当审计证据底稿")
            print("  · --who 用真名或窗口身份映射，映射表放 personal.local.md（不入仓）")
        if args.apply:
            on_names = [x[0] for x in on]

            def mutate(_ctx, s):
                """v1.25.0 收编进 guarded_write（旧版直调 write_atomic 无锁无 rev 校验），
                并修门禁重置 bug：旧版无条件把三道可选门禁覆盖为 pending/na——已 passed 的
                会被重跑一次 decide 打回。现在只「点亮」：未判定态才写 pending/na，
                passed/failed 一律不动（security_L1 常开不受本段管辖）。"""
                s["mode"] = tier
                s["mode_source"] = "auto"
                for sid in STEP_ORDER:
                    g = s["stages"][sid]
                    want = "todo" if sid in TIER_STEPS[tier] else "na"
                    if g["state"] in ("na", "todo"):
                        g["state"] = want
                for n in ("model_eval", "multi_agent", "outward_claims"):
                    want_gate = "pending" if n in on_names else "na"
                    if s["gates"].get(n) not in ("passed", "failed"):
                        s["gates"][n] = want_gate
                    elif want_gate != s["gates"].get(n):
                        print("  🔒 [门禁保留] %s=%s 不因重跑定档被改写（要改请显式跑 gate）" % (n, s["gates"][n]))
                return 0

            rc = guarded_write(ctx, _WriteShim(apply=True), "mode --decide → %s" % tier, mutate)
            if rc == 0:
                print("\n[已写] 档位=%s 来源=auto（rev 见上）" % tier)
            return rc if rc != 0 else 0
        return 0
    if args.set:
        tier = ALIASES.get(args.set, args.set)
        if tier not in TIER_STEPS:
            die(2, "[参数错] 未知档位 %s（可用：轻量/标准/企业/复杂）" % args.set)

        def mutate(_ctx, st):
            st["mode"] = tier
            st["mode_source"] = "user"
            for sid in STEP_ORDER:
                s = st["stages"][sid]
                if sid in TIER_STEPS[tier]:
                    if s["state"] == "na":
                        s["state"] = "todo"
                else:
                    s["state"] = "na" if s["state"] in ("todo", "na") else s["state"]
            return 0
        return guarded_write(ctx, args, "mode --set %s" % tier, mutate)
    st = load_state(ctx, must=False)
    if st is None:
        print("（还没建权威记录）用 mode --decide 选档，或 init 时带 --mode")
        return 0
    print("mode=%s｜来源=%s｜步骤=%s" % (st["mode"], st.get("mode_source"), " ".join(TIER_STEPS[st["mode"]])))
    return 0


def parse_signals(items):
    sig = {}
    for s in items or []:
        if "=" not in s:
            die(2, "[参数错] --signal 要写成 键=值，收到：%s" % s)
        k, v = s.split("=", 1)
        sig[k.strip()] = _coerce(v.strip())
    return sig


def _coerce(v):
    low = v.lower()
    if low in ("true", "yes", "是"):
        return True
    if low in ("false", "no", "否"):
        return False
    try:
        return int(v)
    except ValueError:
        return v


def cmd_plan(ctx, args):
    tier = args.tier or (load_state(ctx, must=False) or {}).get("mode", "标准")
    tier = ALIASES.get(tier, tier)
    if tier not in TIER_STEPS:
        die(2, "[参数错] 未知档位 %s" % tier)
    print("[%s 档产物清单（只这些，不多建）]" % tier)
    for f in TIER_FILES[tier]:
        print("  · %s" % f)
    print("[%s 档步骤]" % tier)
    print("  " + " ".join(TIER_STEPS[tier]))
    print("[跳过（不建对应文件）]")
    print("  " + " ".join(s for s in STEP_ORDER if s not in TIER_STEPS[tier]))
    return 0


def cmd_render(ctx, args):
    st = load_state(ctx)
    views = ("ledger", "board") if args.view == "all" else (args.view,)
    if args.view == "all":
        made = render_all(ctx, st, apply=args.apply)
        if not made:
            print("[未生成任何派生物] 见上方「未生成」原因")
        return 0
    print(VIEW_FN[args.view](st))
    if not args.apply:
        print("（上面是干跑输出；--apply 才落 %s）" % VIEW_FILE[args.view])
        return 0
    made = render_all(ctx, st, apply=True, views=(args.view,))
    print("[已渲染] %s" % "、".join(made))
    return 0


def _manifest_gap(ctx, st, sid):
    """v1.14.0 产物清单硬闸：调用方只登记一部分产物就标 done ⇒ 退 4。

    Qoder 实测漏洞：① 只登记 PRD.md（盘上存在）就标 done，设计纪要与整个文档层缺失——
    旧硬闸只验「登记过的在不在」，不验「该有的有没有」。本函数补第二半。
    显式退出唯一通道：docs --opt-out <路径> --why <用户原话>（记进权威记录）。
    """
    spec = STAGE_MANIFEST.get(sid)
    if not spec and sid != "⑤":
        return 0
    docs = st.get("docs") or {}
    opted = {o.get("path") for o in (docs.get("opt_out") or [])}
    tier = st.get("mode", "标准")
    gone, stub = [], []
    if spec:
        required = [f for f in spec.get("all", []) if f not in opted]
        if tier != "轻量":
            required += [f for f in spec.get("non_light", []) if f not in opted]
        for f in required:
            p = os.path.join(ctx.root, f)
            if not os.path.isfile(p):
                gone.append(f)
            elif SCAFFOLD_MARK in read_bytes(p).decode("utf-8", "replace"):
                # v1.16.0：骨架检测从「仅文档层六件」放开到清单内所有产物——设计纪要/选型/PRD
                # 搭了骨架不填实同样不许标 done（此前三份不在 DOC_L0_FILES，骨架态照样放行=漏洞）
                stub.append(f)
        for g in (spec.get("non_light_glob", []) if tier != "轻量" else []):
            if not glob.glob(os.path.join(ctx.root, g)):
                gone.append("%s（无匹配文件）" % g)
    if sid == "⑤":
        # 卡头标了【动到的模块】，模块卡就必须在盘上（细则 §3 模块四件套之一）
        mods = set()
        for c in (st.get("cards") or {}).values():
            if isinstance(c, dict):
                mods.update(c.get("modules") or [])
        for m in sorted(mods):
            if not os.path.isfile(os.path.join(ctx.root, m, "MODULE.md")):
                gone.append("%s/MODULE.md（卡头标了模块却没有模块卡）" % m)
    if gone or stub:
        print("[拒绝标 done] 阶段 %s 产物清单硬闸未过（v1.14.0 默认落地、显式退出）" % sid)
        for f in gone:
            print("            缺：%s" % f)
        for f in stub:
            print("            仍是骨架未填实（含 %s）：%s" % (SCAFFOLD_MARK, f))
        print("            显式退出：docs --opt-out <路径> --why <用户原话>（记进权威记录，不静默跳过）")
        return 4
    return 0


def _pre_dep_gap(st, sid):
    """v1.14.0 顺序硬闸：⑥ 开工前 ⑦④⑤ 必须 done。

    Qoder 实测：④⑤ 还是 todo 就写完 755 行代码（卡没切、验收基线没就绪就施工）。
    na/skipped 算显式豁免；要跳步先跑 stage --id X --state skipped 留痕，不静默。
    """
    deps = PRE_EXEC_DEPS.get(sid)
    if not deps:
        return 0
    bad = [d for d in deps
           if (st.get("stages", {}).get(d) or {}).get("state") not in ("done", "na", "skipped")]
    if bad:
        print("[拒绝开工] 阶段 %s 的前置未完成：%s" % (sid, "、".join(bad)))
        print("            标准档顺序 ①②③④⑤⑦⑥：卡没切、验收基线没就绪就不许施工")
        print("            （na/skipped 算显式豁免；跳步先 stage --id X --state skipped 留痕）")
        return 4
    return 0


def doc_layer_gaps(ctx, st):
    """v1.14.0：① 标了 done，文档层就必须一直在盘上——防「标完 done 就把文件删了/根本没建」。
    返回 (缺失件, 仍是骨架的件)。① 未 done 时不要求（棕地老项目不受影响）。"""
    if (st.get("stages", {}).get("①") or {}).get("state") != "done":
        return [], []
    spec = STAGE_MANIFEST.get("①") or {}
    docs = st.get("docs") or {}
    opted = {o.get("path") for o in (docs.get("opt_out") or [])}
    tier = st.get("mode", "标准")
    required = [f for f in spec.get("all", []) if f not in opted]
    if tier != "轻量":
        required += [f for f in spec.get("non_light", []) if f not in opted]
    gone = [f for f in required if not os.path.isfile(os.path.join(ctx.root, f))]
    stub = [f for f in required
            if os.path.isfile(os.path.join(ctx.root, f))
            and SCAFFOLD_MARK in read_bytes(os.path.join(ctx.root, f)).decode("utf-8", "replace")]
    return gone, stub


def cmd_stage(ctx, args):
    if args.id not in STEP_ORDER:
        die(2, "[参数错] 未知阶段 %s（可用：%s）" % (args.id, " ".join(STEP_ORDER)))

    def mutate(_ctx, st):
        s = st["stages"].setdefault(args.id, {"name": STEP_NAME.get(args.id, args.id), "state": "todo",
                                              "input_version": "", "artifacts": [], "done_criteria": "",
                                              "recovery_point": "", "evidence": []})
        for k, v in (("state", args.state), ("input_version", args.input_version),
                     ("done_criteria", args.criteria), ("recovery_point", args.recovery),
                     ("status", None)):
            if v and k != "status":
                s[k] = v
        for a in args.artifact or []:
            if a not in s.setdefault("artifacts", []):
                s["artifacts"].append(a)
        for e in args.evidence or []:
            if e not in s.setdefault("evidence", []):
                s["evidence"].append(e)
        if args.state == "done":
            miss = [k for k in STAGE_REQUIRED_FIELDS if not s.get(k)]
            if miss:
                print("[拒绝标 done] 阶段 %s 四字段缺：%s" % (args.id, "、".join(miss)))
                print("            卡内口径：输入版本／产物位置／完成条件／恢复点 齐了才算「这步做完了」")
                return 4
            gone = [a for a in s["artifacts"] if not os.path.lexists(os.path.join(ctx.root, a))]
            if gone:
                print("[拒绝标 done] 阶段 %s 登记的产物在盘上不存在：%s" % (args.id, "、".join(gone)))
                return 4
            rc = _manifest_gap(ctx, st, args.id)   # v1.14.0：该有的有没有
            if rc:
                return rc
        if args.state == "doing":
            rc = _pre_dep_gap(st, args.id)         # v1.14.0：前置完不完；v1.18.0 铺满 DAG
            if rc:
                if getattr(args, "skip_auth", None):
                    # v1.18.0 单步直调 A 方案：用户点名某一步＝显式授权，闸放行但必须留痕——
                    # 口头跳步不算数，账上要有这一笔（谁点的、点什么理由）；v1.25.0 起强制 --who
                    if not getattr(args, "who", None):
                        die(2, "[参数错] --skip-auth 必须带 --who <授权人>（授权链四要素 who/what/when/why——"
                               "缺 who 的跳步授权无法归因，可被任何窗口自批）")
                    st.setdefault("skip_auths", []).append(
                        {"stage": args.id, "auth": args.skip_auth, "who": args.who, "at": now_str()})
                    print("[跳步授权·留痕] 阶段 %s 前置未满，凭 --skip-auth 放行（授权人：%s）：%s"
                          % (args.id, args.who, args.skip_auth))
                else:
                    return rc
        if args.state:
            st["cards"] = st.get("cards", {})
        return 0
    return guarded_write(ctx, args, "stage %s%s" % (args.id, " " + args.state if args.state else ""), mutate)


def cmd_card(ctx, args):
    cid = args.id if args.id.startswith("c") or not args.id[1:2].isdigit() else "c%s" % args.id.lstrip("卡#")
    fields = [("title", args.title), ("state", args.state), ("executor", args.executor),
              ("started", args.started), ("acceptance", args.acceptance), ("metric", args.metric),
              ("date", args.date), ("note", args.note), ("who", args.who)]
    if args.state and args.state not in CARD_STATES:
        die(2, "[参数错] 卡状态只能用 %s" % "／".join(CARD_STATES))

    def mutate(_ctx, st):
        c = st.setdefault("cards", {}).setdefault(cid, {})
        for k, v in fields:
            if v:
                c[k] = v
        if args.parallel_ok is not None:
            c["parallel_ok"] = bool(args.parallel_ok)
        for d in args.depends or []:
            if d not in c.setdefault("depends", []):
                c["depends"].append(d)
        for m in args.module or []:
            if m not in c.setdefault("modules", []):
                c["modules"].append(m)
        if c.get("state") == "done" and not c.get("metric"):
            print("[拒绝标 done] 卡 %s 没有实测数字 metric ⇒ 留在原状态（台账约束 1：状态只有实测值）" % cid)
            return 4
        return 0
    return guarded_write(ctx, args, "card %s%s" % (cid, " " + args.state if args.state else ""), mutate)


def cmd_docs(ctx, args):
    """v1.14.0 文档层档位与显式退出：默认全落地，退出必须带理由并记进权威记录。"""
    st = load_state(ctx)
    if args.show:
        docs = st.get("docs") or {"tier": "L0", "opt_out": []}
        opted = {o.get("path"): o for o in (docs.get("opt_out") or [])}
        print("[文档档] tier=%s｜默认清单 %d 件｜显式退出 %d 件"
              % (docs.get("tier", "L0"), len(DOC_L0_FILES), len(opted)))
        for f in DOC_L0_FILES:
            o = opted.get(f)
            if o:
                print("  ⊘ %s —— 已退出：%s（%s）" % (f, o.get("why", "?"), o.get("at", "")))
                continue
            p = os.path.join(ctx.root, f)
            if not os.path.isfile(p):
                print("  ✗ %s —— 缺失（① done 前必须落地）" % f)
            elif SCAFFOLD_MARK in read_bytes(p).decode("utf-8", "replace"):
                print("  ◐ %s —— 仍是骨架未填实" % f)
            else:
                print("  ✓ %s —— 已落地" % f)
        return 0
    if args.opt_out and not args.why:
        die(2, "[参数错] --opt-out 必须带 --why（用户原话或明确依据）——显式退出要留证据，禁静默跳过")

    def mutate(_ctx, st2):
        d = st2.setdefault("docs", {"tier": "L0", "opt_out": []})
        if args.tier:
            d["tier"] = args.tier
            d["decided_at"] = now_str()
        if args.opt_out:
            d.setdefault("opt_out", []).append({"path": args.opt_out, "why": args.why, "at": now_str()})
        return 0
    return guarded_write(ctx, args, "docs tier=%s opt-out=%s" % (args.tier or "—", args.opt_out or "—"), mutate)


def cmd_gate(ctx, args):
    if args.value not in GATE_VALUES:
        die(2, "[参数错] 门禁值只能用 %s" % "／".join(GATE_VALUES))
    if args.name == "outward_claims" and args.value == "na":
        # v1.14.0 软闸：Qoder 实测把 outward_claims 置 na、⑭ 整步跳过，而 README／演示是硬交付物。
        # 不拦死（标准档步骤集本来不含⑭），但已答事项里出现对外物时必须当场点名。
        st0 = load_state(ctx, must=False) or {}
        hits = [k for k, v in (st0.get("answers") or {}).items()
                if re.search(r"README|演示|面试|简历|汇报|作品集|对外|交付物", str((v or {}).get("answer", "")))]
        if hits:
            print("🔴 [门禁警告] outward_claims 要置 na，但已答事项里出现对外陈述物相关：%s"
                  % "、".join(sorted(hits)))
            print("             有 README／演示／简历类交付物的项目应保持 pending 并跑 ⑭；确认真没有对外物再置 na")

    def mutate(_ctx, st):
        st.setdefault("gates", {})[args.name] = args.value
        if args.evidence:
            st.setdefault("gate_evidence", {})[args.name] = args.evidence
        return 0
    return guarded_write(ctx, args, "gate %s=%s" % (args.name, args.value), mutate)


def cmd_answer(ctx, args):
    st = load_state(ctx)
    if args.check:
        a = (st.get("answers") or {}).get(args.id)
        if a:
            print("[已答过] %s ＝「%s」（来源：%s，%s）⇒ **这一项不要再问用户**"
                  % (args.id, a.get("answer"), a.get("source"), a.get("at", "")))
            return 0
        print("[未答过] %s ⇒ 可以问；拿到答复后用 answer 记进权威记录" % args.id)
        return 1

    def mutate(_ctx, st2):
        if not args.answer:
            die(2, "[参数错] answer 需要 --answer（或改用 --check 查询）")
        st2.setdefault("answers", {})[args.id] = {"answer": args.answer, "source": args.source,
                                                  "at": now_str()}
        return 0
    return guarded_write(ctx, args, "answer %s" % args.id, mutate)


def cmd_action(ctx, args):
    st = load_state(ctx)
    acts = st.setdefault("external_actions", [])
    hit = None
    for a in acts:
        if a.get("id") == args.id or (args.idem_key and a.get("idem_key") == args.idem_key):
            hit = a
            break
    if args.check:
        if hit and hit.get("effect_applied"):
            print("[已做过] %s（类型 %s，%s，证据 %s）⇒ 续传时**跳过**，别重复 push／提交／发消息"
                  % (hit.get("id"), hit.get("kind"), hit.get("at"), hit.get("evidence") or "无"))
            return 0
        print("[没做过] %s ⇒ 可以执行；生效后立刻 action --done 登记" % args.id)
        return 1

    def mutate(_ctx, st2):
        acts2 = st2.setdefault("external_actions", [])
        rec = None
        for a in acts2:
            if a.get("id") == args.id:
                rec = a
                break
        if rec is None:
            rec = {"id": args.id}
            acts2.append(rec)
        if args.kind:
            rec["kind"] = args.kind
        if args.idem_key:
            rec["idem_key"] = args.idem_key
        if args.evidence:
            rec["evidence"] = args.evidence
        if args.done:
            rec["effect_applied"] = True
            rec["at"] = now_str()
        return 0
    return guarded_write(ctx, args, "action %s%s" % (args.id, " done" if args.done else ""), mutate)


def cmd_resume(ctx, args):
    st = load_state(ctx)
    tier = st.get("mode", "标准")
    steps = TIER_STEPS.get(tier, [])
    stages = st.get("stages", {})
    nxt = None
    for sid in steps:
        s = stages.get(sid)
        if not s:
            continue
        if s.get("state") in ("todo", "doing", "blocked"):
            nxt = sid
            break
    print("[权威记录] %s｜rev=%s｜模式=%s（来源=%s）｜项目状态=%s"
          % (ctx.state_path, st.get("rev"), tier, st.get("mode_source", "?"), st.get("status")))
    if nxt is None:
        undone = [sid for sid in steps if stages.get(sid, {}).get("state") not in ("done", "skipped")]
        if undone:
            print("[续传结论] 本档未收尾的步骤：%s ⇒ 逐个补完或显式标 skipped（不许默默跳过）" % " ".join(undone))
        else:
            print("[续传结论] 本档步骤全部 done/skipped ⇒ 走 ⑫ 收尾后把项目状态改 已完成")
    else:
        s = stages[nxt]
        print("[续传结论] 从 %s %s 继续（state=%s）⇒ 前面的 %s 已 done/skipped，**不许重跑**"
              % (nxt, s.get("name"), s.get("state"),
                 " ".join(x for x in steps[:steps.index(nxt)])))
        print("  恢复点：%s" % (s.get("recovery_point") or "（该阶段没写恢复点 ⇒ 先补写再干活）"))
        print("  完成条件：%s" % (s.get("done_criteria") or "（未写 ⇒ 不许标 done）"))
        print("  输入版本：%s" % (s.get("input_version") or "（未记）"))
        have = [a for a in (s.get("artifacts") or []) if os.path.lexists(os.path.join(ctx.root, a))]
        miss = [a for a in (s.get("artifacts") or []) if not os.path.lexists(os.path.join(ctx.root, a))]
        print("  产物已在：%s" % ("、".join(have) or "无"))
        if miss:
            print("  产物尚缺：%s" % "、".join(miss))
    cs = _cards(st)
    print("[卡状态] 在办 %s｜待办 %s｜阻塞 %s｜已完成 %s" % (
        "、".join(c["id"] for c in cs if c["state"] == "doing") or "无",
        "、".join(c["id"] for c in cs if c["state"] == "todo") or "无",
        "、".join(c["id"] for c in cs if c["state"] == "blocked") or "无",
        "、".join(c["id"] for c in cs if c["state"] == "done") or "无"))
    skip = [a for a in (st.get("external_actions") or []) if a.get("effect_applied")]
    print("[不许重做的外部动作] %s" % ("、".join("%s(%s)" % (a["id"], a.get("kind")) for a in skip) or "无记录"))
    ans = st.get("answers") or {}
    print("[已问过／已授权，别再问] %s" % ("、".join(sorted(ans)) or "无"))
    conf = st.get("sync_conflicts") or []
    if conf:
        print("[待人工裁决] %d 条，见台账第十一节" % len(conf))
    return 0


def cmd_show(ctx, args):
    st = load_state(ctx)
    if args.json:
        print(dump_json(st))
        return 0
    print("项目=%s｜模式=%s｜状态=%s｜rev=%s" % (st.get("project"), st.get("mode"), st.get("status"), st.get("rev")))
    for sid in STEP_ORDER:
        s = st["stages"].get(sid, {})
        print("  %s %-12s %s" % (sid, s.get("name", ""), s.get("state", "-")))
    return 0


def cmd_status(ctx, args):
    """v1.25.0：项目状态字段的唯一合法写入通道。旧版无子命令能写 status ⇒ check 的收尾
    反查闸（status==已完成 才激活）是不可达代码，两道机器门禁永不触发、完成事件无法挂接
    下游。置「已完成」前反查两道门（判据与 cmd_check 收尾段同源）：security_L1 只认 passed
    （铁律 10）、outward_claims 只认 passed/na（铁律 11）。置「已归档」要求已完成＋归档件在盘。"""
    if not args.set:
        st = load_state(ctx, must=False)
        if st is None:
            print("（还没建权威记录，无状态可看）")
            return 0
        print("status=%s（改法：status --set 已完成｜已归档；置完成前反查两道门禁）" % st.get("status"))
        return 0
    want = args.set

    def mutate(_ctx, s):
        g = s.get("gates", {})
        if want == "已完成":
            if g.get("security_L1") != "passed":
                print("[拒绝置完成] security_L1=%s ⇒ 只认 passed（L1 常开，无「跳过」一说，铁律 10）"
                      % g.get("security_L1"))
                return 4
            if g.get("outward_claims") not in ("passed", "na"):
                print("[拒绝置完成] outward_claims=%s ⇒ 只认 passed（或显式 na=确无对外物）（铁律 11）"
                      % g.get("outward_claims"))
                return 4
            s["status"] = "已完成"
            return 0
        # 已归档
        if s.get("status") != "已完成":
            print("[拒绝归档] 当前 status=%s ⇒ 先 status --set 已完成（它反查两道门禁）" % s.get("status"))
            return 4
        s["status"] = "已归档"
        return 0

    return guarded_write(ctx, args, "status --set %s" % want, mutate)


def cmd_archive(ctx, args):
    """v1.25.0：台账归档的机器通道——活台账改名 台账_归档_YYYYMMDD.md ＋ status 置已归档。
    旧版改名是纯人工动作、无脚本子命令，C19 只能事后抓「复活」不能事前保证按规程归档。
    顺序：先经 guarded_write 反查（status 必须已完成、活台账必须在盘、目标件不存在），
    权威落盘成功后再执行改名（改名失败不回滚状态，警告感知——重跑本命令幂等续做）。"""
    import glob as _glob
    st = load_state(ctx)

    def mutate(_ctx, s):
        if s.get("status") != "已完成":
            print("[拒绝归档] 当前 status=%s ⇒ 先 status --set 已完成（反查两道门禁）" % s.get("status"))
            return 4
        live = ctx.abspath("台账.md")
        if not os.path.lexists(live):
            print("[拒绝归档] 活台账不在盘：%s（已归档过则重跑本命令只补 status）" % live)
            return 4
        s["status"] = "已归档"   # 与改名同一事务：先落账再动文件
        return 0

    rc = guarded_write(ctx, args, "archive", mutate)
    if rc:
        return rc
    live = ctx.abspath("台账.md")
    if not os.path.lexists(live):
        print("[已归档] 活台账已不在盘，status 已写（幂等）")
        return 0
    s2 = load_state(ctx)
    stamp = (s2.get("updated_at") or now_str())[:10].replace("-", "")
    target = ctx.abspath("台账_归档_%s.md" % stamp)
    if os.path.lexists(target):
        die(4, "[拒绝归档] 目标件已存在：%s（人工核对后处理，不自动覆盖）" % target)
    try:
        os.replace(live, target)
    except OSError as e:
        print("⚠ [归档] 改名失败（status 已写，重跑本命令续做）：%s" % e)
        return 1
    print("[已归档] %s → %s" % (live, target))
    return 0


def cmd_unlock(ctx, args):
    """交锁入口：只有登记的持锁窗本人能交（卡05 edit_lock release 同口径）。

    缺这一段，用 --keep-lock 取锁后又崩掉的窗口只能等租约过期再 --takeover，
    等于把「交锁」和「抢锁」混成一件事。
    """
    path = lock_path(ctx)
    rec = read_lock(path)
    if rec is None:
        print("[无锁] %s 上没有锁 ⇒ 什么都不用做" % path)
        return 0
    if args.holder and rec.get("holder") == args.holder:
        lock_release(ctx, {"path": path, "holder": args.holder,
                           "generation": int(rec.get("generation", 1))})
        print("[已交锁] %s（代次 %s 用尽，下一窗从代次 %s 继续）"
              % (args.holder, rec.get("generation"), int(rec.get("generation", 1)) + 1))
        return 0
    age = time.time() - _lock_ts(rec)
    if age > max(int(rec.get("lease_seconds", LOCK_LEASE_DEFAULT) or LOCK_LEASE_DEFAULT), STALE_GRACE):
        die(7, "[陈旧锁] 持有者=%s 心跳已 %.0fs 无更新 ⇒ unlock 不替别人交锁。"
                "确认它真没在写之后，用任意写命令加 --takeover 接管（代次 +1）"
              % (rec.get("holder"), age))
    die(5, "[他人持有] 当前锁是 %s 的（代次 %s，%.0fs 前心跳）⇒ %s 无权交别人的锁"
           % (rec.get("holder"), rec.get("generation"), age, args.holder or "?"))


LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
EXT_RE = re.compile(r"(?i)^(https?|mailto|ftp):")
ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")


def scan_links(path, root):
    bad = []
    base = os.path.dirname(path)
    infence = False
    for line in read_bytes(path).decode("utf-8", "replace").splitlines():
        if FENCE_RE.match(line):
            infence = not infence
            continue
        if infence:
            continue
        for m in LINK_RE.finditer(line):
            t = m.group(1)
            if EXT_RE.match(t) or t.startswith("#"):
                continue
            if ABS_RE.match(t):
                bad.append("%s → %s（本机绝对路径，公开侧要换占位符）" % (_rel(path, root), t))
                continue
            target = os.path.normpath(os.path.join(base, t.split("#")[0]))
            if not os.path.lexists(target):
                bad.append("%s → %s" % (_rel(path, root), t))
    return bad


def _rel(p, root):
    try:
        return os.path.relpath(p, root).replace("\\", "/")
    except ValueError:
        return p.replace("\\", "/")


def cmd_deps(ctx, args):
    st = load_state(ctx)

    def mutate(_ctx, st2):
        d = st2.setdefault("deps", {})
        if args.record:
            entries = d.setdefault("files", [])
            added = 0
            for spec in args.record:
                parts = spec.split("|")
                rel = parts[0].strip()
                src = parts[1].strip() if len(parts) > 1 else ""
                ver = parts[2].strip() if len(parts) > 2 else ""
                target = os.path.join(ctx.root, rel)
                if not os.path.isfile(target):
                    print("[拒绝登记] 依赖快照不收不存在的件：%s" % rel)
                    return 4
                row = {"path": rel.replace("\\", "/"), "source": src, "version": ver,
                       "sha256": sha256_bytes(read_bytes(target))}
                for i, e in enumerate(entries):
                    if e.get("path") == row["path"]:
                        entries[i] = row
                        break
                else:
                    entries.append(row)
                added += 1
            d["snapshot_at"] = now_str()
            print("[依赖快照] 已登记 %d 件（含 sha256）" % added)
        if args.map:
            for pair in args.map:
                if "=" not in pair:
                    die(2, "[参数错] --map 要写成 上游路径=本地路径，收到：%s" % pair)
                k, v = pair.split("=", 1)
                d.setdefault("path_map", {})[k.strip()] = v.strip()
                print("[路径映射] %s → %s" % (k.strip(), v.strip()))
        if args.check_links:
            files = []
            for spec in args.check_links:
                for one in spec.split(","):
                    one = one.strip()
                    if one:
                        files.append(one)
            bad = []
            for rel in files:
                p = os.path.join(ctx.root, rel)
                if not os.path.isfile(p):
                    print("[拒绝检查] 待检文件不存在：%s" % rel)
                    return 4
                bad += scan_links(p, ctx.root)
            d["link_check"] = "failed" if bad else "passed"
            d["broken_links"] = bad
            if bad:
                print("[依赖链接检查] 断链 %d 处：" % len(bad))
                for b in bad:
                    print("  ✗ %s" % b)
                print("  ⇒ 修链或改写成文字说明，别留指不通的链接")
                return 1
            print("[依赖链接检查] 通过（%d 个文件的相对链接全部指得通）" % len(files))
        return 0
    rc = guarded_write(ctx, args, "deps", mutate)
    if rc == 0 and not (args.record or args.map or args.check_links):
        d = st.get("deps", {})
        print("[依赖快照] %s｜已登记 %d 件｜链接检查=%s｜断链 %d 处"
              % (d.get("snapshot_at") or "未做", len(d.get("files") or []),
                 d.get("link_check", "unchecked"), len(d.get("broken_links") or [])))
    return rc


# ---------------------------------------------------------------- selftest（合成夹具，先红后绿）
def cmd_selftest(args):
    import io
    import shutil
    import tempfile
    results = []

    def chk(name, cond, extra=""):
        results.append((name, bool(cond), extra))

    tmp = tempfile.mkdtemp(prefix="pipeline-state-")
    try:
        ctx = Ctx(tmp)
        st = blank_state("夹具项目", "轻量", "auto")
        write_atomic(ctx.state_path, dump_json(st).encode("utf-8"))
        st = load_state(ctx)
        chk("A01 权威记录可读且 rev=1", st["rev"] == 1)
        chk("A02 轻量档产物清单不含任务板", BOARD_NAME not in TIER_FILES["轻量"])
        made = render_all(ctx, st, apply=True)
        chk("A03 轻量档 render 只产台账", made == [LEDGER_NAME], str(made))
        chk("A04 台账带 AUTO-BEGIN 标记", AUTO_MARK in read_bytes(ctx.abspath(LEDGER_NAME)).decode("utf-8"))
        chk("A05 渲染后 check 通过", cmd_check(ctx, load_state(ctx)) == 0)
        chk("A06 台账含阶段四字段表头", "输入版本" in read_bytes(ctx.abspath(LEDGER_NAME)).decode("utf-8"))
        chk("A07 台账含「已问过」与「外部动作」两节",
            "已问过" in read_bytes(ctx.abspath(LEDGER_NAME)).decode("utf-8") and
            "外部动作账" in read_bytes(ctx.abspath(LEDGER_NAME)).decode("utf-8"))
        # 不动点：同状态两次渲染逐字相同
        r1 = render_ledger(load_state(ctx))
        r2 = render_ledger(json.loads(dump_json(load_state(ctx))))
        chk("A08 台账渲染是权威记录的不动点（两次逐字相同）", r1 == r2)
        # 漂移检测（能红）
        tp = ctx.abspath(LEDGER_NAME)
        write_atomic(tp, read_bytes(tp).decode("utf-8").replace("_（无阻塞）_", "卡#9 等第三方密钥", 1).encode("utf-8"), need_dir=False)
        chk("A09 手改派生物后 check 翻红", cmd_check(ctx, load_state(ctx)) == 1)
        # 派生保护：同名手写件（无 AUTO 标记）绝不覆盖
        hand = "# 手写台账\n\n自己写的，没有生成标记。\n"
        write_atomic(tp, hand.encode("utf-8"), need_dir=False)
        chk("A10 render 对无 AUTO 标记的手写件拒写（退 8）",
            _exit_of(render_all, ctx, load_state(ctx), True, ("ledger",)) == 8)
        chk("A10b 拒写时手写件内容一个字节未变",
            read_bytes(tp).decode("utf-8") == hand)
        # 手写件改回派生件后重新渲染
        write_atomic(tp, render_ledger(load_state(ctx)).encode("utf-8"), need_dir=False)
        chk("A11 重渲染后复绿", cmd_check(ctx, load_state(ctx)) == 0)
        # sync 逐字段并账
        md = ctx.abspath("旧台账.md")
        write_atomic(md, ("# x\n\n- 模式：标准 ｜ 落盘目录：docs/pipeline/\n\n"
                          "## 一、在做\n\n| 卡#9 来源展示 | 子代理 |\n\n"
                          "## 二、待执行\n\n| 卡#7 导出 | 卡#9 | 是 | 用例全绿 |\n\n"
                          "## 三、做完\n\n- [x] 卡#1 评测集搭建（基线 Hit@5 70.6%，09-19）\n\n"
                          "## 四、阻塞区\n\n| 卡#5 密钥 | 等第三方 | 用户 |\n").encode("utf-8"), need_dir=False)
        parsed = parse_md_ledger(md)
        chk("A12 旧 md 解析出 4 张卡", len(parsed["cards"]) == 4, str(sorted(parsed["cards"])))
        chk("A13 旧 md 解析出模式与做完数字",
            parsed["mode"] == "标准" and parsed["cards"]["c1"]["detail"].startswith("基线 Hit@5"))
        cmd_sync(ctx, load_state(ctx), md, apply=True)
        st2 = load_state(ctx)
        chk("A14 sync 吸收 state 里没有的卡", "c9" in st2["cards"] and st2["cards"]["c9"]["state"] == "doing")
        chk("A15 sync 吸收做完项的实测数字", st2["cards"]["c1"].get("metric", "").startswith("基线 Hit@5"))
        chk("A16 sync 把阻塞卡登记进 state", st2["cards"].get("c5", {}).get("state") == "blocked")
        chk("A17 sync 后 check 复绿", cmd_check(ctx, st2) == 0)
        # 冲突不整覆盖
        st3 = load_state(ctx)
        st3["cards"]["c1"]["metric"] = "Hit@5 91.0%"
        bump(st3, "改 c1 指标")
        write_atomic(ctx.state_path, dump_json(st3).encode("utf-8"))
        write_atomic(md, ("# y\n\n## 三、做完\n\n- [x] 卡#1 旧标题（基线 Hit@5 70.6%）\n").encode("utf-8"),
                     need_dir=False)
        cmd_sync(ctx, load_state(ctx), md, apply=True)
        st4 = load_state(ctx)
        chk("A18 旧 md 的较旧值没有覆盖 state 的较新值（旧版整覆盖会在这里丢数据）",
            st4["cards"]["c1"]["metric"] == "Hit@5 91.0%", st4["cards"]["c1"].get("metric"))
        chk("A19 冲突被登记为待人工裁决而不是静默吞掉", bool(st4["sync_conflicts"]), str(st4["sync_conflicts"]))
        chk("A20 裁决清单点名冲突字段", any("c1.metric" in c for c in st4["sync_conflicts"]))
        # 乐观版本校验
        args = _Args(expect_rev=1, holder=None, takeover=False, lease=LOCK_LEASE_DEFAULT, apply=True,
                     keep_lock=False)
        chk("A21 用过期 rev 写入被拒（退 6）",
            guarded_write(ctx, args, "x", lambda c, s: 0) == 6)
        chk("A22 被拒后权威记录 rev 未变", load_state(ctx)["rev"] == st4["rev"])
        args2 = _Args(expect_rev=st4["rev"], holder=None, takeover=False, lease=LOCK_LEASE_DEFAULT,
                      apply=True, keep_lock=False)
        chk("A23 用当前 rev 写入成功", guarded_write(ctx, args2, "x", lambda c, s: 0) == 0)
        # 四字段硬闸
        a_stage = _Args(id="③", state="done", input_version="", criteria="", recovery="",
                        artifact=None, evidence=None, expect_rev=None, holder=None, takeover=False,
                        lease=LOCK_LEASE_DEFAULT, apply=True, keep_lock=False)
        chk("A24 四字段不齐拒绝标阶段 done（退 4）", cmd_stage(ctx, a_stage) == 4)
        chk("A25 被拒后阶段仍是原状态", load_state(ctx)["stages"]["③"]["state"] != "done")
        a_stage2 = _Args(id="③", state="done", input_version="PRD@v1", criteria="PRD 含全局验收",
                         recovery="第 4 节待补", artifact=["docs/pipeline/PRD.md"], evidence=None,
                         expect_rev=None, holder=None, takeover=False, lease=LOCK_LEASE_DEFAULT,
                         apply=True, keep_lock=False)
        chk("A26 产物不存在时仍拒绝标 done（退 4）", cmd_stage(ctx, a_stage2) == 4)
        os.makedirs(os.path.join(tmp, "docs", "pipeline"), exist_ok=True)
        write_atomic(os.path.join(tmp, "docs", "pipeline", "PRD.md"), b"# PRD\n", need_dir=True)
        chk("A27 四字段齐且产物在场 ⇒ 标 done 成功", cmd_stage(ctx, a_stage2) == 0)
        chk("A28 台账里该阶段带恢复点", "第 4 节待补" in read_bytes(ctx.abspath(LEDGER_NAME)).decode("utf-8"))
        # 卡 done 需要实测数字
        a_card = _Args(id="c9", state="done", title=None, executor=None, started=None, acceptance=None,
                       metric=None, date=None, note=None, who=None, parallel_ok=None, depends=None,
                       module=None, expect_rev=None, holder=None, takeover=False,
                       lease=LOCK_LEASE_DEFAULT, apply=True, keep_lock=False)
        chk("A29 无实测数字拒绝标卡 done（退 4）", cmd_card(ctx, a_card) == 4)
        a_card2 = _Args(id="c9", state="done", title=None, executor=None, started=None, acceptance=None,
                        metric="来源数≥1 实测 3", date="10-14", note=None, who=None, parallel_ok=None,
                        depends=None, module=["rag"], expect_rev=None, holder=None, takeover=False,
                        lease=LOCK_LEASE_DEFAULT, apply=True, keep_lock=False)
        chk("A30 带实测数字的卡标 done 成功", cmd_card(ctx, a_card2) == 0)
        chk("A31 台账做完区出现该卡与模块", "c9" in read_bytes(ctx.abspath(LEDGER_NAME)).decode("utf-8")
            and "模块：rag" in read_bytes(ctx.abspath(LEDGER_NAME)).decode("utf-8"))
        # 已答过不再问
        chk("A32 未登记事项 --check 退 1（可以问）",
            cmd_answer(ctx, _Args(check=True, id="落盘目录", answer="", source="", holder=None,
                                 takeover=False, lease=LOCK_LEASE_DEFAULT, apply=True, expect_rev=None,
                                 keep_lock=False)) == 1)
        cmd_answer(ctx, _Args(check=False, id="落盘目录", answer="docs/pipeline/", source="用户已说明",
                              holder=None, takeover=False, lease=LOCK_LEASE_DEFAULT, apply=True,
                              expect_rev=None, keep_lock=False))
        chk("A33 answer 落进权威记录", "落盘目录" in load_state(ctx)["answers"])
        chk("A34 已答事项 --check 退 0 且打印「不要再问」",
            cmd_answer(ctx, _Args(check=True, id="落盘目录", answer="", source="", holder=None,
                                 takeover=False, lease=LOCK_LEASE_DEFAULT, apply=True, expect_rev=None,
                                 keep_lock=False)) == 0)
        # 外部动作幂等
        cmd_action(ctx, _Args(id="push-github", kind="git", idem_key="push-1", evidence="e.txt",
                              done=True, check=False, holder=None, takeover=False,
                              lease=LOCK_LEASE_DEFAULT, apply=True, expect_rev=None, keep_lock=False))
        chk("A35 已生效动作 --check 退 0（续传跳过）",
            cmd_action(ctx, _Args(id="push-github", kind="", idem_key="push-1", evidence="", done=False,
                                  check=True, holder=None, takeover=False, lease=LOCK_LEASE_DEFAULT,
                                  apply=True, expect_rev=None, keep_lock=False)) == 0)
        chk("A36 未做过动作 --check 退 1",
            cmd_action(ctx, _Args(id="发版说明", kind="", idem_key="", evidence="", done=False,
                                  check=True, holder=None, takeover=False, lease=LOCK_LEASE_DEFAULT,
                                  apply=True, expect_rev=None, keep_lock=False)) == 1)
        # 自动选档
        t_small = decide_tier({"has_code": False, "from_zero": False, "files_touched": 1})[0]
        t_mid = decide_tier({"new_feature": True, "files_touched": 6, "modules_touched": 2, "cards": 4})[0]
        t_big = decide_tier({"from_zero": True, "team_agents": 3, "estimated_days": 5, "modules_touched": 6})[0]
        chk("A37 小改动自动判轻量", t_small == "轻量", str(t_small))
        chk("A38 新功能自动判标准", t_mid == "标准", str(t_mid))
        chk("A39 从零+多人自动判企业", t_big == "企业", str(t_big))
        chk("A40 无任何信号 ⇒ 落最低档（宁少步骤也不产空文档堆）", decide_tier({})[0] == "轻量")
        chk("A40b 无信号那一行必须自报「默认值不是判定」（防把没评估当评估过）",
            "默认" in decide_tier({})[2][0] and "未收到任何信号" in decide_tier({})[2][0],
            str(decide_tier({})[2]))
        chk("A40c 有信号时绝不出现「未收到任何信号」这行（防早退分支吞掉真实计分）",
            all("未收到任何信号" not in " ".join(decide_tier(s)[2])
                for s in ({"files_touched": 1}, {"from_zero": True}, {"new_feature": True, "files_touched": 5})))
        chk("A41 轻／标同分打平 ⇒ 返回 None，只在这种置信度不足时问用户（不硬猜）",
            decide_tier({"files_touched": 2, "has_code": True, "new_feature": True})[0] is None)
        chk("A41b 信号不打架时照分定档 ⇒ 标准",
            decide_tier({"files_touched": 5, "new_feature": True})[0] == "标准")
        on_light, off_light = enabled_steps("轻量", {"deploy": True, "eval_set": True, "parallel_cards": 5,
                                                    "team_agents": 3, "compliance": True})
        chk("A42 轻量档不点亮 L2/L3/model_eval/multi_agent",
            not any(n in ("security_L2", "security_L3", "model_eval", "multi_agent") for n, _ in on_light),
            str(on_light))
        on_ent, _ = enabled_steps("企业", {"compliance": True})
        chk("A43 企业档点亮 L3", any(n == "security_L3" for n, _ in on_ent))
        on_mid, off_mid = enabled_steps("标准", {"has_code": True})
        chk("A44 安全 L1 常开：有无代码／改不改档都不给它关掉（铁律 10）",
            all(any(n == "security_L1" for n, _ in enabled_steps(t, {"has_code": c})[0])
                for t in ("轻量", "标准", "企业") for c in (True, False)))
        chk("A44b 关掉 L1 的唯一路径不存在（无信号能把 L1 打进默认不跑侧）",
            all(not any(n == "security_L1" for n, _ in off)
                for off in (enabled_steps(t, s)[1]
                            for t in ("轻量", "标准", "企业")
                            for s in ({}, {"has_code": False}, {"deploy": False}))))
        chk("A45 无对外物 ⇒ 口径门禁记在默认不跑一侧", any(n == "outward_claims" for n, _ in off_mid))
        # 续传：第三阶段中断 ⇒ 从该阶段继续，已完成动作零重放
        st5 = load_state(ctx)
        st5["mode"] = "标准"
        st5["mode_source"] = "user"
        for sid in TIER_STEPS["标准"]:
            st5["stages"][sid]["state"] = "todo"
        for sid in ("①", "②"):
            st5["stages"][sid]["state"] = "done"
            st5["stages"][sid]["input_version"] = "设计纪要@v2"
            st5["stages"][sid]["done_criteria"] = "纪要落盘"
            st5["stages"][sid]["recovery_point"] = "已完成"
            st5["stages"][sid]["artifacts"] = ["docs/pipeline/设计纪要.md"]
        st5["stages"]["③"]["state"] = "doing"
        st5["stages"]["③"]["recovery_point"] = "PRD 第 4 节待补验收标准"
        bump(st5, "夹具：标准档三步中断")
        write_atomic(ctx.state_path, dump_json(st5).encode("utf-8"))
        out = _capture(cmd_resume, ctx, _Args())
        chk("A46 续传指向 ③ 而不是回头重跑 ①②", "从 ③" in out)
        chk("A47 续传声明已 done 的步骤不许重跑", "不许重跑" in out)
        chk("A48 续带给恢复点", "PRD 第 4 节待补验收标准" in out)
        chk("A49 续传列出不许重做的外部动作", "push-github" in out)
        chk("A50 续传列出已问过事项", "落盘目录" in out)
        # 缺件与非法输入
        ctx2 = Ctx(os.path.join(tmp, "空项目"))
        chk("A51 缺权威记录 ⇒ 退 3", _exit_of(load_state, ctx2) == 3)
        os.makedirs(ctx2.dir, exist_ok=True)
        write_atomic(ctx2.state_path, b"{ not json", need_dir=False)
        chk("A52 权威记录坏 ⇒ 退 4（不静默当空状态）", _exit_of(load_state, ctx2) == 4)
        write_atomic(ctx2.state_path, dump_json({"schema": 99, "rev": 1, "stages": {}, "cards": {},
                                                 "answers": {}, "gates": {}, "deps": {}}).encode("utf-8"),
                     need_dir=False)
        chk("A53 schema 比脚本新 ⇒ 退 4（不猜格式）", _exit_of(load_state, ctx2) == 4)
        write_atomic(ctx2.state_path, dump_json({"schema": SCHEMA_VERSION, "rev": "abc", "stages": {},
                                                 "cards": {}, "answers": {}, "gates": {},
                                                 "deps": {}}).encode("utf-8"), need_dir=False)
        chk("A54 rev 非整数 ⇒ 退 4", _exit_of(load_state, ctx2) == 4)
        chk("A55 未知阶段名 ⇒ 退 2", _exit_of(cmd_stage, ctx, _Args(id="⑼")) == 2)
        chk("A56 未知卡状态 ⇒ 退 2", _exit_of(cmd_card, ctx, _Args(id="c9", state="finished")) == 2)
        chk("A57 未知门禁值 ⇒ 退 2", _exit_of(cmd_gate, ctx, _Args(name="g", value="maybe")) == 2)
        # 原子写不留临时件
        junk = [f for f in os.listdir(ctx.dir) if ".tmp-" in f]
        chk("A58 目录里无残留临时件", junk == [], str(junk))
        # 多写者：他人有效持锁 ⇒ 拒；陈旧锁需显式接管，接管推代代次
        tok = lock_try(ctx, "别人窗")
        chk("A59 锁被有效持有时退 5", _exit_of(lock_try, ctx, "我窗") == 5)
        lock_release(ctx, tok)
        tok2 = lock_try(ctx, "我窗")
        chk("A60 释放后可再取（代次 1）", tok2["generation"] == 1)
        lp = lock_path(ctx)
        rec = json.loads(read_bytes(lp).decode("utf-8"))
        rec["heartbeat_at"] = datetime.fromtimestamp(time.time() - 7200).strftime(TS_FMT)
        write_atomic(lp, json.dumps(rec).encode("utf-8"), need_dir=False)
        chk("A61 陈旧锁未点名 takeover ⇒ 退 7", _exit_of(lock_try, ctx, "新窗") == 7)
        tk3 = lock_try(ctx, "新窗", takeover=True)
        chk("A62 显式接管后代次 +1", tk3["generation"] == 2, str(tk3))
        lock_release(ctx, tk3)
        # 同窗复用与交锁入口（缺这两条，--keep-lock 的窗口会把自己锁死）
        ctxL = Ctx(os.path.join(tmp, "锁项目"))
        write_atomic(ctxL.state_path, dump_json(blank_state("锁项目", "标准", "user")).encode("utf-8"))
        tkA = lock_try(ctxL, "窗A")
        chk("A74 首次取锁代次=1", tkA["generation"] == 1, str(tkA))
        outA = _capture(lock_try, ctxL, "窗A")
        chk("A75 同窗再取＝续心跳而不是撞自己（代次仍 1）",
            lock_try(ctxL, "窗A")["generation"] == 1 and "锁沿用" in outA)
        chk("A76 他人取活锁仍退 5（同窗复用没把互斥打开）",
            _exit_of(lock_try, ctxL, "窗B") == 5)
        _ns = _Args(root=ctxL.root, subdir=None, holder="窗B")
        chk("A77 他人交锁退 5（unlock 不替别人交）", _exit_of(cmd_unlock, ctxL, _ns) == 5)
        _ns2 = _Args(root=ctxL.root, subdir=None, holder="窗A")
        chk("A78 本人交锁成功且锁消失",
            cmd_unlock(ctxL, _ns2) == 0 and read_lock(lock_path(ctxL)) is None)
        chk("A79 无锁时 unlock 幂等退 0", cmd_unlock(ctxL, _ns2) == 0)
        # 陈旧判定必须按锁记录里登记的租约算（来者带长租约不许把崩溃窗的锁判成「还在写」）
        ctxS = Ctx(os.path.join(tmp, "短租约锁"))
        write_atomic(ctxS.state_path, dump_json(blank_state("短租约", "标准", "user")).encode("utf-8"))
        lock_try(ctxS, "崩窗", lease=1)
        lps = lock_path(ctxS)
        rec_s = json.loads(read_bytes(lps).decode("utf-8"))
        rec_s["heartbeat_at"] = datetime.fromtimestamp(time.time() - 60).strftime(TS_FMT)
        write_atomic(lps, json.dumps(rec_s).encode("utf-8"), need_dir=False)
        chk("A80 崩溃窗的短租约锁按记录判陈旧 ⇒ 退 7（来者租约 3600s 不得覆盖判据）",
            _exit_of(lock_try, ctxS, "接班窗", False, 3600) == 7)
        chk("A80b 点名接管后代次 +1 且锁易主",
            lock_try(ctxS, "接班窗", takeover=True, lease=3600)["generation"] == 2)
        # 假信号比没信号更危险：不认识的键必须当场点名
        _ma = _Args(decide=True, ask=False, apply=False,
                    signal=["files_touched=1", "has_code=true", "user_says_quick=1"])
        out_ma = _capture(cmd_mode, ctx, _ma)
        chk("A81 不认识的信号键被点名「不参与计分」（防止把写了信号当成已评估）",
            "不参与计分的信号键" in out_ma and "user_says_quick" in out_ma, out_ma.splitlines()[:4])
        _mb = _Args(decide=True, ask=False, apply=False, signal=["files_touched=2", "has_code=true"])
        chk("A82 全是认识的键时不乱点名（A81 不是恒真打印）",
            "不参与计分" not in _capture(cmd_mode, ctx, _mb))
        # v1.28.0 team 外挂声明（⑦ 大团队边界）：输出面声明，不给 skill 建权限体系
        _mt = _Args(decide=True, ask=False, apply=False,
                    signal=["team_agents=3", "files_touched=6", "modules_touched=2"])
        out_mt = _capture(cmd_mode, ctx, _mt)
        chk("A83 team_agents≥2 ⇒ 定档输出含外挂声明（权限/评审流/审计外挂 GitHub，skill 不建权限体系）",
            "外挂声明" in out_mt and "CODEOWNERS" in out_mt,
            str([l for l in out_mt.splitlines() if "外挂" in l][:2]))
        _ms = _Args(decide=True, ask=False, apply=False, signal=["files_touched=6", "modules_touched=2"])
        chk("A84 单人（team_agents 缺省=1）⇒ 输出不含外挂声明（不噪音）",
            "外挂声明" not in _capture(cmd_mode, ctx, _ms))
        # 依赖快照：不存在的件拒登；哈希与路径映射；链接检查能红能绿
        rc = cmd_deps(ctx, _Args(record=["references/不存在.md|上游|v1"], map=None, check_links=None,
                                 holder=None, takeover=False, lease=LOCK_LEASE_DEFAULT, apply=True,
                                 expect_rev=None, keep_lock=False))
        chk("A63 依赖快照拒绝登记不存在的件（退 4）", rc == 4)
        dep = os.path.join(ctx.dir, "depends")
        os.makedirs(dep, exist_ok=True)
        write_atomic(os.path.join(dep, "a.md"), b"# a\n", need_dir=False)
        write_atomic(os.path.join(dep, "b.md"), b"[good](a.md)\n[bad](c.md)\n", need_dir=False)
        rc = cmd_deps(ctx, _Args(record=None, map=None, check_links=["docs/pipeline/depends/b.md"],
                                 holder=None, takeover=False, lease=LOCK_LEASE_DEFAULT, apply=False,
                                 expect_rev=None, keep_lock=False))
        chk("A64 依赖链接检查抓得出断链（退 1）", rc == 1)
        write_atomic(os.path.join(dep, "b.md"), b"[good](a.md)\n", need_dir=False)
        rc = cmd_deps(ctx, _Args(record=["docs/pipeline/depends/a.md|夹具上游|main@abc"],
                                 map=["上游/a.md=docs/pipeline/depends/a.md"],
                                 check_links=["docs/pipeline/depends/b.md"], holder=None, takeover=False,
                                 lease=LOCK_LEASE_DEFAULT, apply=True, expect_rev=None, keep_lock=False))
        d6 = load_state(ctx)["deps"]
        chk("A65 快照登记成功且带 64 位哈希", rc == 0 and len(d6["files"][0]["sha256"]) == 64)
        chk("A66 路径映射写进权威记录", d6["path_map"].get("上游/a.md") == "docs/pipeline/depends/a.md")
        chk("A67 链接检查通过并记进 state", d6["link_check"] == "passed" and not d6["broken_links"])
        # 台账如实列出依赖问题（原先这条写成 `... or True`＝恒真假 PASS，已改成能红的判据）
        st9 = load_state(ctx)
        st9["deps"]["path_map"]["本机件"] = "E:\\somewhere"
        st9["deps"]["broken_links"] = ["docs/pipeline/depends/b.md → 缺 docs/pipeline/depends/z.md"]
        bump(st9, "夹具造依赖问题")
        write_atomic(ctx.state_path, dump_json(st9).encode("utf-8"))
        led = render_ledger(load_state(ctx))
        chk("A68 断链如实进台账（不只躺在 json 里等人查）", "🔴 断链" in led, led[-400:])
        chk("A68b 本机绝对路径在台账里标成「发布前必删」", "🔴 本机绝对路径" in led)
        st10 = load_state(ctx)
        st10["deps"]["broken_links"] = []
        st10["deps"]["path_map"].pop("本机件", None)
        bump(st10, "夹具清问题")
        write_atomic(ctx.state_path, dump_json(st10).encode("utf-8"))
        chk("A68c 红跟着权威记录走：清掉后台账里不再残留（不是写死的字符串）",
            "🔴 断链" not in render_ledger(load_state(ctx)))
        # 标准档才产任务板；无卡不产空看板
        ctx3 = Ctx(os.path.join(tmp, "p3"))
        st7 = blank_state("p3", "标准", "auto")
        write_atomic(ctx3.state_path, dump_json(st7).encode("utf-8"))
        made3 = render_all(ctx3, load_state(ctx3), apply=True)
        chk("A69 标准档无卡 ⇒ 不建空任务板", BOARD_NAME not in made3, str(made3))
        st8 = load_state(ctx3)
        st8["cards"] = {"c1": {"title": "夹具卡", "state": "todo", "acceptance": "跑绿"}}
        bump(st8, "夹具加卡")
        write_atomic(ctx3.state_path, dump_json(st8).encode("utf-8"))
        made4 = render_all(ctx3, load_state(ctx3), apply=True)
        chk("A70 有卡后标准档才生成任务板", BOARD_NAME in made4, str(made4))
        chk("A71 任务板是派生物（含 AUTO 标记）", AUTO_MARK in read_bytes(ctx3.abspath(BOARD_NAME)).decode("utf-8"))
        chk("A72 轻量档永远不建任务板（改档后该文件不再被 render 生成）",
            BOARD_NAME not in render_all(ctx3, blank_state("x", "轻量", "auto"), apply=False))
        rc = cmd_check(ctx3, load_state(ctx3))
        chk("A73 标准档全量对账通过", rc == 0)
        # ---- v1.14.0 B 系列：文档层默认落地 / 产物清单硬闸 / 顺序硬闸（每条能红能绿）----
        def _fill(ctxX, skip=()):
            """把骨架填实（删掉首行 scaffold 标记）；skip 里的件直接删掉——模拟「用户就是不要这一件」。"""
            for f in DOC_L0_FILES:
                p = os.path.join(ctxX.root, f)
                if f in skip:
                    if os.path.lexists(p):
                        os.remove(p)
                    continue
                write_atomic(p, "\n".join(DOC_STUBS[f].split("\n")[1:]).encode("utf-8"), need_dir=True)
            write_atomic(os.path.join(ctxX.root, "docs", "pipeline", "设计纪要.md"),
                         "# 设计纪要（夹具）\n".encode("utf-8"), need_dir=True)

        def _a(sid=None, state=None, **kw):
            base = dict(id=sid, state=state, input_version="x@v1", criteria="c", recovery="r",
                        artifact=None, evidence=None, expect_rev=None, holder=None, takeover=False,
                        lease=LOCK_LEASE_DEFAULT, apply=True, keep_lock=False, skip_auth=None)
            base.update(kw)
            return _Args(**base)

        # B01/B02 init 脚手架：默认搭、显式 none 不搭
        ctxB = Ctx(os.path.join(tmp, "文档层夹具"))
        rc_init = cmd_init(ctxB, _Args(project="文档夹具", mode="标准", mode_source="auto",
                                       docs_tier="L0", force=False, apply=True))
        chk("B01 init 默认搭 L0 骨架六件（默认落地＝阻力最小的路）",
            rc_init == 0 and all(os.path.isfile(os.path.join(ctxB.root, f)) for f in DOC_L0_FILES),
            str([f for f in DOC_L0_FILES if not os.path.isfile(os.path.join(ctxB.root, f))]))
        ctxB0 = Ctx(os.path.join(tmp, "无文档夹具"))
        cmd_init(ctxB0, _Args(project="无文档", mode="标准", mode_source="auto",
                              docs_tier="none", force=False, apply=True))
        chk("B02 --docs-tier none 一件不搭（显式退出从建账开始，且不静默）",
            not any(os.path.isfile(os.path.join(ctxB0.root, f)) for f in DOC_L0_FILES))
        # B03/B04/B05 ① done 硬闸：缺件拒 → 骨架拒 → 填实放行
        # （登记的产物一律用盘上存在的 state.json，确保拒绝只能来自新的清单硬闸——旧闸拦下的不算）
        ctxB2 = Ctx(os.path.join(tmp, "空文档夹具"))
        write_atomic(ctxB2.state_path, dump_json(blank_state("空文档", "标准", "user")).encode("utf-8"))
        chk("B03 文档层一件都没有 ⇒ ① 拒绝标 done（退 4）",
            cmd_stage(ctxB2, _a("①", "done", artifact=["docs/pipeline/state.json"])) == 4)
        chk("B04 六件在盘但仍是骨架（含 scaffold 标记）⇒ ① 仍拒（退 4）",
            cmd_stage(ctxB, _a("①", "done", artifact=["docs/pipeline/state.json"])) == 4)
        _fill(ctxB)
        chk("B05 六件填实＋设计纪要在场 ⇒ ① done 放行",
            cmd_stage(ctxB, _a("①", "done", artifact=["docs/pipeline/设计纪要.md"])) == 0)
        chk("B05b ① done 后 check 复绿（文档层持续在盘才算数）",
            cmd_check(ctxB, load_state(ctxB)) == 0)
        # B06/B07 顺序硬闸：⑥ 开工前 ⑦④⑤ 必须 done
        chk("B06 ④⑤⑦ 未完成 ⇒ ⑥ 拒绝开工（退 4）", cmd_stage(ctxB, _a("⑥", "doing")) == 4)
        for sid in ("④", "⑤", "⑦"):
            cmd_stage(ctxB, _a(sid, "done", artifact=["docs/pipeline/state.json"]))
        chk("B07 ④⑤⑦ done 后 ⑥ 放行", cmd_stage(ctxB, _a("⑥", "doing")) == 0)
        # B08/B09 ③ specs 硬闸（标准档；PRD 先落盘，确保拒绝只能来自 specs glob）
        write_atomic(os.path.join(ctxB.root, "docs", "pipeline", "PRD.md"), "# PRD\n".encode("utf-8"), need_dir=True)
        chk("B08 标准档 ③ 缺 specs/*/spec.md ⇒ 拒（退 4）",
            cmd_stage(ctxB, _a("③", "done", artifact=["docs/pipeline/PRD.md"])) == 4)
        os.makedirs(os.path.join(ctxB.root, "specs", "room"), exist_ok=True)
        write_atomic(os.path.join(ctxB.root, "specs", "room", "spec.md"), "# spec\n".encode("utf-8"), need_dir=False)
        chk("B09 PRD＋specs 在场 ⇒ ③ done 放行",
            cmd_stage(ctxB, _a("③", "done", artifact=["docs/pipeline/PRD.md"])) == 0)
        # B10 显式退出：同一缺件，无退出记录 ⇒ 拒；有退出记录（带用户理由）⇒ 放行
        ctxB3 = Ctx(os.path.join(tmp, "退出夹具"))
        cmd_init(ctxB3, _Args(project="退出", mode="标准", mode_source="user",
                              docs_tier="L0", force=False, apply=True))
        _fill(ctxB3, skip=("docs/human/小白手册.md",))
        chk("B10a 缺件且无退出记录 ⇒ ① 拒（退 4）",
            cmd_stage(ctxB3, _a("①", "done", artifact=["docs/pipeline/设计纪要.md"])) == 4)
        cmd_docs(ctxB3, _Args(show=False, tier=None, opt_out="docs/human/小白手册.md",
                              why="天 2026-10-15 明确不要", apply=True, expect_rev=None, holder=None,
                              takeover=False, lease=LOCK_LEASE_DEFAULT, keep_lock=False))
        chk("B10b 同一缺件＋显式退出（用户理由进权威记录）⇒ ① 放行",
            cmd_stage(ctxB3, _a("①", "done", artifact=["docs/pipeline/设计纪要.md"])) == 0)
        out_docs = _capture(cmd_docs, ctxB3, _Args(show=True, tier=None, opt_out=None, why=None,
                                                   apply=False, expect_rev=None, holder=None,
                                                   takeover=False, lease=LOCK_LEASE_DEFAULT,
                                                   keep_lock=False))
        chk("B11 docs --show 逐件状态＋退出理由可见（不静默跳过）",
            "已退出" in out_docs and "小白手册" in out_docs and "缺失" not in out_docs, out_docs[-300:])
        chk("B11b --opt-out 不带 --why ⇒ 退 2（退出必须留证据）",
            _exit_of(cmd_docs, ctxB3, _Args(show=False, tier=None, opt_out="CONTEXT.md", why=None,
                                            apply=True, expect_rev=None, holder=None, takeover=False,
                                            lease=LOCK_LEASE_DEFAULT, keep_lock=False)) == 2)
        # B12 门禁软闸：有对外物答复却置 na ⇒ 当场点名
        cmd_answer(ctxB, _Args(id="交付背景", answer="面试 demo，需 README＋现场演示", source="用户已说明",
                               check=False, holder=None, takeover=False, lease=LOCK_LEASE_DEFAULT,
                               apply=True, expect_rev=None, keep_lock=False))
        out_g = _capture(cmd_gate, ctxB, _Args(name="outward_claims", value="na", evidence="",
                                               expect_rev=None, holder=None, takeover=False,
                                               lease=LOCK_LEASE_DEFAULT, apply=True, keep_lock=False))
        chk("B12 有对外物答复却置 outward_claims=na ⇒ 当场点名（软闸不静默）",
            "门禁警告" in out_g and "README" in out_g, out_g.splitlines()[:3])
        # B13 轻量档：设计纪要豁免（一句话版），文档层照样要
        ctxB5 = Ctx(os.path.join(tmp, "轻量文档夹具"))
        cmd_init(ctxB5, _Args(project="轻量文档", mode="轻量", mode_source="auto",
                              docs_tier="L0", force=False, apply=True))
        _fill(ctxB5)
        chk("B13 轻量档 ① 不强制设计纪要（索引：一句话版）但文档层六件照样要",
            cmd_stage(ctxB5, _a("①", "done", artifact=["AGENTS.md"])) == 0)
        # B14 ② 位置硬闸：选型记录只有 docs/adr/ 版 ⇒ 拒（Qoder 实测漂移就此堵死）
        ctxB6 = Ctx(os.path.join(tmp, "选型夹具"))
        write_atomic(ctxB6.state_path, dump_json(blank_state("选型", "标准", "user")).encode("utf-8"))
        os.makedirs(os.path.join(ctxB6.root, "docs", "adr"), exist_ok=True)
        write_atomic(os.path.join(ctxB6.root, "docs", "adr", "0001.md"), "# ADR\n".encode("utf-8"), need_dir=False)
        chk("B14 选型记录只有 docs/adr/ 版 ⇒ ② 拒（退 4，位置漂移堵死）",
            cmd_stage(ctxB6, _a("②", "done", artifact=["docs/adr/0001.md"])) == 4)
        write_atomic(os.path.join(ctxB6.root, "docs", "pipeline", "技术选型记录.md"),
                     "# 选型\n".encode("utf-8"), need_dir=True)
        chk("B14b docs/pipeline/技术选型记录.md 在场 ⇒ ② 放行",
            cmd_stage(ctxB6, _a("②", "done", artifact=["docs/adr/0001.md"])) == 0)
        # B15 ⑤ 模块卡硬闸：卡头标了模块，MODULE.md 就必须在盘上
        cmd_card(ctxB, _Args(id="c1", state="todo", title="夹具卡", module=["backend"], acceptance="x",
                             metric=None, executor=None, started=None, date=None, note=None, who=None,
                             parallel_ok=None, depends=None, expect_rev=None, holder=None,
                             takeover=False, lease=LOCK_LEASE_DEFAULT, apply=True, keep_lock=False))
        chk("B15 卡头标了 backend 却没有 backend/MODULE.md ⇒ ⑤ 重标 done 被拒（退 4）",
            cmd_stage(ctxB, _a("⑤", "done", artifact=["docs/pipeline/state.json"])) == 4)
        os.makedirs(os.path.join(ctxB.root, "backend"), exist_ok=True)
        write_atomic(os.path.join(ctxB.root, "backend", "MODULE.md"), "# MODULE\n".encode("utf-8"), need_dir=False)
        chk("B15b 模块卡补齐 ⇒ ⑤ 放行",
            cmd_stage(ctxB, _a("⑤", "done", artifact=["docs/pipeline/state.json"])) == 0)
        # B16 done 之后删文件照样判红（默认落地是持续状态，不是一次性动作）
        os.remove(os.path.join(ctxB.root, "CONTEXT.md"))
        chk("B16 ① done 后删掉 CONTEXT.md ⇒ check 判红（退 1）",
            cmd_check(ctxB, load_state(ctxB)) == 1)
        write_atomic(os.path.join(ctxB.root, "CONTEXT.md"), "# 术语\n".encode("utf-8"), need_dir=False)
        chk("B16b 补回来后复绿", cmd_check(ctxB, load_state(ctxB)) == 0)
        # B17 老项目（schema 2 无 docs 段）平滑升级：不炸、按 L0 默认清单要求
        st_old = blank_state("老项目", "标准", "user")
        st_old.pop("docs")
        ctxB7 = Ctx(os.path.join(tmp, "老项目夹具"))
        write_atomic(ctxB7.state_path, dump_json(st_old).encode("utf-8"))
        st_back = load_state(ctxB7)
        chk("B17 旧 state.json（无 docs 段）读入不炸且默认 L0",
            (st_back.get("docs") or {}).get("tier") == "L0")
        # B18 parser 声明的每个子命令都必须进 dispatch（docs 加了 parser 忘注册的坑，实测犯过：
        #     跑起来 KeyError，而 selftest 直调 cmd_docs 绕过 main，断言全绿没抓到）
        import inspect
        import re as _re
        declared = set(_re.findall(r'sub\.add_parser\("([a-z_]+)"', inspect.getsource(build_parser)))
        dispatched = set(_re.findall(r'"([a-z_]+)":\s*lambda', inspect.getsource(command_table)))
        chk("B18 parser 声明的子命令与 dispatch 表一一对应（selftest 由 main 直处理算唯一例外）",
            declared - dispatched == {"selftest"} and not (dispatched - declared) and "docs" in dispatched,
            "只在 parser：%s｜只在 dispatch：%s" % (sorted(declared - dispatched), sorted(dispatched - declared)))
        # ---- v1.16.0 B19~B23：三份大计划产物骨架（init 预设 + 骨架检测放开到全部清单产物）----
        ctxB8 = Ctx(os.path.join(tmp, "计划骨架夹具"))
        cmd_init(ctxB8, _Args(project="计划骨架", mode="标准", mode_source="user",
                              docs_tier="L0", force=False, apply=True))
        chk("B19 标准档 init 搭三份大计划产物骨架（设计纪要/选型/PRD，含 scaffold 标记）",
            all(os.path.isfile(os.path.join(ctxB8.root, f))
                and SCAFFOLD_MARK in read_bytes(os.path.join(ctxB8.root, f)).decode("utf-8", "replace")
                for f in PLAN_STUB_FILES))
        ctxB9 = Ctx(os.path.join(tmp, "轻量骨架夹具"))
        cmd_init(ctxB9, _Args(project="轻量骨架", mode="轻量", mode_source="user",
                              docs_tier="L0", force=False, apply=True))
        chk("B19b 轻量档 init 不搭三份（①一句话版/③可省——宁少不空）",
            not any(os.path.lexists(os.path.join(ctxB9.root, f)) for f in PLAN_STUB_FILES))
        # 棕地不覆盖：设计纪要已有填实版，--force 重 init 后内容不被骨架替换
        _keep = read_bytes(os.path.join(ctxB8.root, "docs", "pipeline", "设计纪要.md"))
        write_atomic(os.path.join(ctxB8.root, "docs", "pipeline", "设计纪要.md"),
                     "# 设计纪要（棕地填实版，无标记）\n".encode("utf-8"), need_dir=True)
        cmd_init(ctxB8, _Args(project="计划骨架", mode="标准", mode_source="user",
                              docs_tier="L0", force=True, apply=True))
        chk("B20 棕地已有件不覆盖（--force 重 init 后填实版原样保留）",
            SCAFFOLD_MARK not in read_bytes(os.path.join(ctxB8.root, "docs", "pipeline", "设计纪要.md")).decode("utf-8", "replace"))
        # 骨架检测放开：六件先全部填实（_fill），再单独把设计纪要换回骨架版——
        # 此时拒绝只能来自设计纪要的标记（六件无标记），断言才真正测到「检测覆盖非六件产物」。
        # （首版没先 _fill，B21 的拒其实混入六件骨架、B21b 填实设计纪要后仍被六件骨架拦——夹具顺序错，非机制错。）
        _fill(ctxB8)
        write_atomic(os.path.join(ctxB8.root, "docs", "pipeline", "设计纪要.md"),
                     PLAN_STUBS["docs/pipeline/设计纪要.md"].encode("utf-8"), need_dir=True)
        chk("B21 设计纪要仍是骨架（含标记，且它不属于文档层六件）⇒ ① 拒（退 4）——骨架检测覆盖清单内全部产物",
            cmd_stage(ctxB8, _a("①", "done", artifact=["docs/pipeline/state.json"])) == 4)
        write_atomic(os.path.join(ctxB8.root, "docs", "pipeline", "设计纪要.md"),
                     "# 设计纪要（填实）\n".encode("utf-8"), need_dir=True)
        chk("B21b 设计纪要填实 ⇒ ① 放行", cmd_stage(ctxB8, _a("①", "done", artifact=["docs/pipeline/设计纪要.md"])) == 0)
        chk("B22 选型记录仍是骨架（init 搭的、没动过）⇒ ② 拒（退 4）",
            cmd_stage(ctxB8, _a("②", "done", artifact=["docs/pipeline/技术选型记录.md"])) == 4)
        write_atomic(os.path.join(ctxB8.root, "docs", "pipeline", "技术选型记录.md"),
                     "# 技术选型记录（填实）\n".encode("utf-8"), need_dir=True)
        chk("B22b 选型记录填实 ⇒ ② 放行", cmd_stage(ctxB8, _a("②", "done", artifact=["docs/pipeline/技术选型记录.md"])) == 0)
        chk("B23 PRD 仍是骨架 ⇒ ③ 拒（退 4）",
            cmd_stage(ctxB8, _a("③", "done", artifact=["docs/pipeline/PRD.md"])) == 4)
        write_atomic(os.path.join(ctxB8.root, "docs", "pipeline", "PRD.md"),
                     "# PRD（填实）\n".encode("utf-8"), need_dir=True)
        os.makedirs(os.path.join(ctxB8.root, "specs", "room"), exist_ok=True)
        write_atomic(os.path.join(ctxB8.root, "specs", "room", "spec.md"), "# spec\n".encode("utf-8"), need_dir=False)
        chk("B23b PRD 填实＋specs 在场 ⇒ ③ 放行", cmd_stage(ctxB8, _a("③", "done", artifact=["docs/pipeline/PRD.md"])) == 0)
        # ---- v1.17.0 B24~B25b：六件骨架加厚 + 结构锚（警不是红）----
        _stub_of = {**DOC_STUBS, **PLAN_STUBS}
        chk("B24 九件骨架自带结构锚（init 生成的骨架文本含全部锚串）",
            all(a in _stub_of[f] for f, anchors in STRUCTURE_ANCHORS.items() for a in anchors),
            str([(f, [a for a in anchors if a not in _stub_of[f]]) for f, anchors in STRUCTURE_ANCHORS.items()
                 if any(a not in _stub_of[f] for a in anchors)]))
        _fill(ctxB8)
        # _fill 只填六件＋简版设计纪要；三份计划产物在 B22b/B23b 里被写成了无锚简版——
        # 此处统一换成「骨架去标记」的含锚填实版，让 B24b 的「无锚警」只可能来自 B25 的删锚操作
        for pf in PLAN_STUB_FILES:
            write_atomic(os.path.join(ctxB8.root, pf),
                         "\n".join(PLAN_STUBS[pf].split("\n")[1:]).encode("utf-8"), need_dir=True)
        out_b24 = _capture(cmd_check, ctxB8, load_state(ctxB8))
        chk("B24b 六件填实（结构完整）⇒ check 无锚警",
            "缺结构锚" not in out_b24, out_b24[-200:])
        # 删锚：AGENTS.md 填实版去掉登记表节 ⇒ check 退 0（警不是红）但点名结构漂移
        _no_anchor = "\n".join(l for l in DOC_STUBS["AGENTS.md"].splitlines()
                               if l != "## 文档登记表（什么时候读什么）")
        write_atomic(os.path.join(ctxB8.root, "AGENTS.md"),
                     _no_anchor.replace(SCAFFOLD_MARK + "\n", "").encode("utf-8"), need_dir=True)
        rc_b25 = cmd_check(ctxB8, load_state(ctxB8))
        out_b25 = _capture(cmd_check, ctxB8, load_state(ctxB8))
        chk("B25 填实后删结构锚 ⇒ check 仍退 0（警不是红，老项目不炸）",
            rc_b25 == 0 and "缺结构锚" in out_b25, "rc=%s" % rc_b25)
        write_atomic(os.path.join(ctxB8.root, "AGENTS.md"),
                     "\n".join(DOC_STUBS["AGENTS.md"].split("\n")[1:]).encode("utf-8"), need_dir=True)
        out_b25b = _capture(cmd_check, ctxB8, load_state(ctxB8))
        chk("B25b 结构恢复 ⇒ 锚警消失", "缺结构锚" not in out_b25b, out_b25b[-200:])
        # ---- v1.18.0 B26~B26d：依赖表铺满 + 单步直调 A 方案（--skip-auth 放行留痕）----
        ctxB11 = Ctx(os.path.join(tmp, "依赖夹具"))
        write_atomic(ctxB11.state_path, dump_json(blank_state("依赖", "标准", "user")).encode("utf-8"))
        chk("B26 ① todo ⇒ ② 拒绝开工（退 4，DAG 依赖铺满）",
            cmd_stage(ctxB11, _a("②", "doing")) == 4)
        rc_b26b = cmd_stage(ctxB11, _a("②", "doing", skip_auth="用户当面点名单步直调②", who="天"))
        st_b26b = load_state(ctxB11)
        chk("B26b ② 带跳步授权 ⇒ 放行且账上留痕（单步直调 A 方案）",
            rc_b26b == 0 and (st_b26b.get("skip_auths") or [{}])[0].get("stage") == "②",
            "rc=%s skip_auths=%s" % (rc_b26b, st_b26b.get("skip_auths")))
        cmd_stage(ctxB11, _a("①", "skipped"))
        chk("B26c ① 显式 skipped ⇒ ② 无授权也放行（豁免通道，跳步必须留痕才合法）",
            cmd_stage(ctxB11, _a("②", "doing")) == 0)
        ctxB12 = Ctx(os.path.join(tmp, "链式夹具"))
        write_atomic(ctxB12.state_path, dump_json(blank_state("链式", "标准", "user")).encode("utf-8"))
        cmd_stage(ctxB12, _a("①", "doing"))
        chk("B26d ① done 但 ② 未完成 ⇒ ③ 拒（链式依赖逐级生效）",
            cmd_stage(ctxB12, _a("③", "doing")) == 4)

        # ---- v1.25.0 修复批新增：B26e~B31（--who 强制/fail-open/status/archive/history.log/门禁保留）----
        ctxV = Ctx(os.path.join(tmp, "修复批夹具"))
        write_atomic(ctxV.state_path, dump_json(blank_state("修复批", "标准", "user")).encode("utf-8"))
        rc_e = None
        try:
            cmd_stage(ctxV, _a("②", "doing", skip_auth="没有授权人的授权"))
        except SystemExit as e2:
            rc_e = e2.code
        chk("B26e --skip-auth 缺 --who ⇒ 退 2（授权链缺 who 无法归因，v1.25.0 强制）", rc_e == 2,
            "rc=%s" % rc_e)
        rc_b = cmd_stage(ctxV, _a("②", "doing", skip_auth="用户当面点名", who="天"))
        st_v = load_state(ctxV)
        chk("B26f --skip-auth 带 --who ⇒ 放行且留痕含 who（B26b 形态升级）",
            rc_b == 0 and (st_v.get("skip_auths") or [{}])[0].get("who") == "天",
            "rc=%s skip_auths=%s" % (rc_b, st_v.get("skip_auths")))
        # B27 fail-open 修复：outward_claims=failed 必须拦（旧版只拦 pending）
        s27 = load_state(ctxV)
        s27["status"] = "已完成"
        s27["gates"]["security_L1"] = "passed"
        s27["gates"]["outward_claims"] = "failed"
        write_atomic(ctxV.state_path, dump_json(s27).encode("utf-8"))
        chk("B27 status=已完成 而 outward_claims=failed ⇒ check 判红（fail-open 已修，v1.25.0）",
            cmd_check(ctxV, load_state(ctxV)) == 1)
        # B28 status --set 已完成 的反查与放行
        rc28a = cmd_status(ctxV, _a(set="已完成"))
        s28 = load_state(ctxV)
        s28["gates"]["outward_claims"] = "na"
        write_atomic(ctxV.state_path, dump_json(s28).encode("utf-8"))
        rc28b = cmd_status(ctxV, _a(set="已完成"))
        chk("B28 status --set 已完成：门禁未过拒(退4)／补过放行写入",
            rc28a == 4 and rc28b == 0 and load_state(ctxV).get("status") == "已完成",
            "rc28a=%s rc28b=%s" % (rc28a, rc28b))
        # B29 history.log 外置审计线
        logp = os.path.join(ctxV.dir, "history.log")
        last = ""
        if os.path.lexists(logp):
            with open(logp, encoding="utf-8") as f:
                last = f.read().strip().splitlines()[-1]
        chk("B29 每次成功写入 ⇒ history.log 追加一行且末行 rev 与权威一致（审计不再 60 条封顶）",
            os.path.lexists(logp) and ('"rev": %d' % load_state(ctxV).get("rev", -1)) in last,
            "log_tail=%s" % last[:120])
        # B30 archive：未完成拒（新夹具）／已完成则改名归档＋status 推进＋活台账消失
        ctxV3 = Ctx(os.path.join(tmp, "归档拒绝夹具"))
        write_atomic(ctxV3.state_path, dump_json(blank_state("归档拒绝", "标准", "user")).encode("utf-8"))
        rc30a = cmd_archive(ctxV3, _a())
        r30_pre = os.path.lexists(ctxV.abspath("台账.md"))
        rc30b = cmd_archive(ctxV, _a()) if r30_pre else 1
        chk("B30 archive：未完成拒(退4)／已完成则改名 台账_归档_* 并置已归档",
            rc30a == 4 and rc30b == 0 and load_state(ctxV).get("status") == "已归档"
            and not os.path.lexists(ctxV.abspath("台账.md")),
            "rc30a=%s rc30b=%s pre=%s" % (rc30a, rc30b, r30_pre))
        # B31 mode --decide --apply 不再打回已 passed 的门禁（门禁重置 bug 修复）
        ctxV2 = Ctx(os.path.join(tmp, "门禁保留夹具"))
        st2 = blank_state("门禁保留", "标准", "auto")
        st2["gates"]["outward_claims"] = "passed"
        write_atomic(ctxV2.state_path, dump_json(st2).encode("utf-8"))
        _args_decide = _a(decide=True, apply=True, signal=["files_touched=9"])
        cmd_mode(ctxV2, _args_decide)
        chk("B31 重跑 mode --decide --apply ⇒ 已 passed 的门禁保留不被打回（门禁重置已修）",
            load_state(ctxV2).get("gates", {}).get("outward_claims") == "passed",
            "gates=%s" % load_state(ctxV2).get("gates"))
    except Exception as exc:
        chk("A99 自测体中途异常（异常即断言失败，绝不出无结论报告）",
            False, "%s: %s" % (type(exc).__name__, exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    npass = sum(1 for _, ok, _ in results if ok)
    for name, ok, extra in results:
        # 🔴 extra 常常是 splitlines() 切片（list）；不转字符串就**正好在有红的时候**崩掉打印，
        #    整份报告消失＝红看不见（本轮变异探针 M09 实测抓到，退码也从 2 变成 traceback）
        suffix = ("  ← " + (extra if isinstance(extra, str) else repr(extra))) if (extra and not ok) else ""
        print("%s %s%s" % ("[PASS]" if ok else "[FAIL]", name, suffix))
    print("断言 %d 条：PASS=%d FAIL=%d" % (len(results), npass, len(results) - npass))
    return 0 if npass == len(results) else 2


class _Args(object):
    """selftest 用的最小参数对象（真实参数由 argparse 提供）。"""
    def __init__(self, **kw):
        base = {"root": ".", "subdir": None, "project": "", "mode": "标准", "mode_source": "auto",
                "decide": False, "set": None, "ask": False, "signal": [], "tier": None, "view": "all",
                "from_md": None, "apply": False, "force": False, "json": False, "expect_rev": None,
                "holder": None, "takeover": False, "lease": LOCK_LEASE_DEFAULT, "keep_lock": False,
                "id": "", "state": None, "input_version": None, "artifact": None, "criteria": None,
                "recovery": None, "evidence": None, "title": None, "executor": None, "started": None,
                "acceptance": None, "metric": None, "date": None, "note": None, "who": None,
                "parallel_ok": None, "depends": None, "module": None, "name": None, "value": None,
                "answer": None, "source": None, "check": False, "kind": None, "idem_key": None,
                "done": False, "record": None, "map": None, "check_links": None, "lenient": False,
                "docs_tier": "L0", "opt_out": None, "why": None, "show": False}
        base.update(kw)
        self.__dict__.update(base)


def _capture(fn, *a):
    """抓 stdout 文本；SystemExit 必须原样上抛，否则退出码断言会恒绿（尺子坏了）。"""
    import io
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        fn(*a)
    finally:
        sys.stdout = old
    return buf.getvalue()


def _exit_of(fn, *a):
    try:
        _capture(fn, *a)
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    finally:
        pass


# ---------------------------------------------------------------- main
def build_parser():
    ap = argparse.ArgumentParser(description="交付流水线单一权威状态工具（零依赖，默认干跑）")
    sub = ap.add_subparsers(dest="command")

    def common(p, lock=True):
        p.add_argument("--root", default=".", help="项目根（状态存 <root>/docs/pipeline/）")
        p.add_argument("--subdir", help="覆盖状态子目录（默认 docs/pipeline）")
        p.add_argument("--apply", action="store_true", help="真写盘（默认干跑）")
        if lock:
            p.add_argument("--expect-rev", type=int, dest="expect_rev", help="乐观版本校验：你读到的 rev")
            p.add_argument("--holder", help="写者名（给则取租约锁）")
            p.add_argument("--takeover", action="store_true", help="显式接管陈旧锁（代次 +1）")
            p.add_argument("--lease", type=int, default=LOCK_LEASE_DEFAULT, help="租约秒数")
            p.add_argument("--keep-lock", dest="keep_lock", action="store_true", help="写完不释放锁")

    p = sub.add_parser("init", help="建权威记录")
    common(p, lock=False)
    p.add_argument("--project", default="")
    p.add_argument("--mode", default="标准", choices=list(TIER_STEPS))
    p.add_argument("--mode-source", dest="mode_source", default="user", choices=["user", "auto", "default"])
    p.add_argument("--docs-tier", dest="docs_tier", default="L0",
                   choices=["L0", "L1", "L2", "none"],
                   help="文档档（v1.14.0 默认 L0：init 即搭文档骨架六件；none＝显式不建）")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("docs", help="文档层档位与显式退出（v1.14.0 默认落地）")
    common(p)
    p.add_argument("--tier", choices=["L0", "L1", "L2", "none"], help="改文档档（默认不改）")
    p.add_argument("--opt-out", dest="opt_out", help="显式退出某一件（必须带 --why）")
    p.add_argument("--why", help="退出理由：用户原话或明确依据")
    p.add_argument("--show", action="store_true", help="看当前清单与逐件状态（在盘/骨架/已退出）")

    p = sub.add_parser("mode", help="选档／改档")
    common(p)
    p.add_argument("--decide", action="store_true", help="按任务范围信号自动选档")
    p.add_argument("--signal", action="append", default=[], metavar="键=值")
    p.add_argument("--set", dest="set", help="用户点名改档（轻量/标准/企业/复杂）")
    p.add_argument("--ask", action="store_true", help="即使能自动定档也交回用户")

    p = sub.add_parser("plan", help="该档要建哪些文件／跑哪些步")
    common(p, lock=False)
    p.add_argument("--tier", choices=list(TIER_STEPS) + ["复杂"])

    p = sub.add_parser("render", help="由权威记录生成派生视图")
    common(p)
    p.add_argument("--view", default="all", choices=["ledger", "board", "all"])

    p = sub.add_parser("check", help="派生物与权威记录对账")
    common(p, lock=False)

    p = sub.add_parser("sync", help="旧台账逐字段并账（永不整覆盖）")
    common(p)
    p.add_argument("--from-md", dest="from_md", required=True, help="旧台账.md 路径")

    p = sub.add_parser("stage", help="阶段四字段与状态")
    common(p)
    p.add_argument("--id", required=True)
    p.add_argument("--state", choices=list(STAGE_STATES))
    p.add_argument("--input-version", dest="input_version")
    p.add_argument("--artifact", action="append")
    p.add_argument("--criteria")
    p.add_argument("--recovery")
    p.add_argument("--evidence", action="append")
    p.add_argument("--skip-auth", dest="skip_auth",
                   help="单步直调授权：用户点名某一步时凭此放行前置闸并留痕（如 \"用户当面点名只跑安检\"）")

    p = sub.add_parser("card", help="任务卡状态")
    common(p)
    p.add_argument("--id", required=True)
    p.add_argument("--state", choices=list(CARD_STATES))
    p.add_argument("--title")
    p.add_argument("--executor")
    p.add_argument("--started")
    p.add_argument("--acceptance")
    p.add_argument("--metric")
    p.add_argument("--date")
    p.add_argument("--note")
    p.add_argument("--who")
    p.add_argument("--parallel-ok", dest="parallel_ok", type=int, choices=[0, 1])
    p.add_argument("--depends", action="append")
    p.add_argument("--module", action="append")

    p = sub.add_parser("gate", help="门禁值")
    common(p)
    p.add_argument("--name", required=True)
    p.add_argument("--value", required=True, choices=list(GATE_VALUES))
    p.add_argument("--evidence", default="")

    p = sub.add_parser("answer", help="已问／已授权登记（复用已有答案）")
    common(p)
    p.add_argument("--id", required=True)
    p.add_argument("--answer")
    p.add_argument("--source", default="用户已说明", choices=["用户已说明", "用户授权", "沿用上次", "推导"])
    p.add_argument("--check", action="store_true", help="查是否已答过（已答退 0）")

    p = sub.add_parser("action", help="外部动作账（已生效的不重做）")
    common(p)
    p.add_argument("--id", required=True)
    p.add_argument("--kind", default="")
    p.add_argument("--idem-key", dest="idem_key", default="")
    p.add_argument("--evidence", default="")
    p.add_argument("--done", action="store_true")
    p.add_argument("--check", action="store_true", help="查是否已做过（已做退 0）")

    p = sub.add_parser("deps", help="依赖快照：哈希／路径映射／链接检查")
    common(p)
    p.add_argument("--record", action="append", metavar="路径|来源|版本")
    p.add_argument("--map", action="append", metavar="上游=本地")
    p.add_argument("--check-links", dest="check_links", action="append", metavar="文件,文件")

    p = sub.add_parser("resume", help="中断恢复：该从哪续＋不许重做的清单")
    common(p, lock=False)

    p = sub.add_parser("show", help="打印权威记录")
    common(p, lock=False)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("status", help="项目状态字段唯一写入通道（v1.25.0；置完成前反查两道门禁）")
    common(p)
    p.add_argument("--set", dest="set", choices=["已完成", "已归档"],
                   help="已完成=反查 security_L1/outward_claims；已归档=要求已完成")

    p = sub.add_parser("archive", help="台账改名归档＋置已归档（v1.25.0；要求 status=已完成）")
    common(p)

    p = sub.add_parser("unlock", help="交回自己持有的锁（只有登记的持锁窗本人能交）")
    p.add_argument("--root", default=".", help="项目根（状态存 <root>/docs/pipeline/）")
    p.add_argument("--subdir", help="覆盖状态子目录（默认 docs/pipeline）")
    p.add_argument("--holder", required=True, help="交锁者（与锁记录不符一律拒）")

    sub.add_parser("selftest", help="合成夹具自证（不碰任何真实项目）")
    return ap


def command_table(ctx, args):
    """子命令分发表。抽成模块级函数是为了让 selftest 能静态核对：
    parser 声明的每个子命令都必须在这里有对应项（v1.14.0 实测犯过——`docs` 加了 parser
    忘注册，跑起来 KeyError，而 selftest 直调 cmd_docs 绕过了 main，断言全绿没抓到）。"""
    return {
        "init": lambda: cmd_init(ctx, args),
        "mode": lambda: cmd_mode(ctx, args),
        "plan": lambda: cmd_plan(ctx, args),
        "render": lambda: cmd_render(ctx, args),
        "check": lambda: cmd_check(ctx, load_state(ctx)),
        "sync": lambda: cmd_sync(ctx, load_state(ctx), args.from_md, apply=args.apply),
        "stage": lambda: cmd_stage(ctx, args),
        "card": lambda: cmd_card(ctx, args),
        "gate": lambda: cmd_gate(ctx, args),
        "docs": lambda: cmd_docs(ctx, args),
        "answer": lambda: cmd_answer(ctx, args),
        "action": lambda: cmd_action(ctx, args),
        "deps": lambda: cmd_deps(ctx, args),
        "resume": lambda: cmd_resume(ctx, args),
        "status": lambda: cmd_status(ctx, args),
        "archive": lambda: cmd_archive(ctx, args),
        "show": lambda: cmd_show(ctx, args),
        "unlock": lambda: cmd_unlock(ctx, args),
    }


def main(argv=None):
    setup_stdout()
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.command:
        ap.print_help()
        return 2
    if args.command == "selftest":
        return cmd_selftest(args)
    ctx = Ctx(args.root, args.subdir)
    return command_table(ctx, args)[args.command]()


if __name__ == "__main__":
    sys.exit(main())
