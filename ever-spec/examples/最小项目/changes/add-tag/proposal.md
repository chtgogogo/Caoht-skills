# 提案 add-tag

## 为什么

笔记多了翻不全：用户要给笔记挂标签归类。改动影响用户可观察行为 ⇒ 开变更包立约（纯内部重构不开包）。

## 改哪些需求

- ADDED R-3（笔记可打标签且重复标签去重）

## 影响面

specs/note 新增 R-3；tests/test_note.py 加 test_add_tag_dedup 一例；store.json 条目新增可选 tags 字段（向后兼容，旧数据无 tags 照常读）。
