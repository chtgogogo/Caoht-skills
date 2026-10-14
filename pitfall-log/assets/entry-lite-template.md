---
name: 踩坑记录模板（轻量版）
description: 小事用的轻量记录——四段＋来源＋命中记录表。复制到 pitfalls/YYYY-MM-DD-<slug>.md 后把顶部三行换成 root/hits/last_hit/escalated 四行；也可由 scripts/pitfall_log.py add --kind lite 直接生成
type: pitfall-template-lite
---

# 坑：{一句话标题} —— {最直接的后果} 🟠/🟡

> 复制后请把上面这段模板元信息（name / description / type）替换为四行 frontmatter：
> `root: RT-00X` ｜ `hits: 0` ｜ `last_hit: —` ｜ `escalated: false`
> 🔴 不可再生损失**不许用本模板**（工具会拒绝并升为完整复盘）；轻量只给 🟠／🟡

<!-- pitfall-event: {事件ID} | fp: {指纹} -->

## 现象

（一行上下文 + 屏上原文或具体数字——省字数不省证据）

## 根因（机制）

（为什么会坏；归属的根因族写在 frontmatter 的 `root:`，两者别混）

## 能否借鉴（通用规律）

（可迁移的规律 **+ 适用条件**。轻量不等于可以含糊：适用条件空缺时，本条永远进不了升华候选）

## 教训（一句可迁移纪律）

（一句下次能直接照做的命令句）

## 来源

（日期 ｜ 记录者 ｜ 标签）

## 命中记录

（每条命中一行＝一个事件；`hits` 由工具按行数生成，不手填。同一次命中重复提交不重复计数）

| 日期 | 场景 | 解决了什么 |
|---|---|---|
| — | — | — |
