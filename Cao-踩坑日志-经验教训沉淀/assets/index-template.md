---
name: 踩坑日志（索引 + 文件夹）
description: 共享踩坑索引；每条坑一个独立文件存 pitfalls/，本文件只留极轻索引 + 指针，省 token
type: log
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

# 踩坑日志（索引 + 文件夹）

> 自我学习账本，**统一单一事实源**。遇到坑 / 错 / bug / 特殊困难及最终解法 → 追加一条到本索引，详情写 `pitfalls/<slug>.md`。

> 为什么这样做：记录越多，若全内联在一个文件里，每次阅读都会全量加载 → 越来越费 token。解法：**每条坑单独一个文件**放 `pitfalls/`；本索引只留「一行标题 + 指针」，平时只读索引（极省）。只有当用户说「看看踩坑记录 / 教训 / 之前怎么解决的」之类的话 → 才去翻索引、命中相关条目、只读那一个 `pitfalls/<slug>.md`。

## 索引格式（极轻，一行一条）
`YYYY-MM-DD | [记录者] | 标签 | 一句话标题 | → pitfalls/<slug>.md`

## 每条坑的单独文件结构（见 assets/entry-template.md）
坑是什么 / 上下文 / 为什么 / 解法 / 能否借鉴 / 来源

---

## 记录区（新 → 旧，最新在上）

（在此追加条目，例如：）
- YYYY-MM-DD | [记录者] | 标签 | 一句话标题 | → pitfalls/YYYY-MM-DD-<slug>.md
