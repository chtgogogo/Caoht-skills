# 增量 · add-tag

## ADDED Requirements

#### R-3 | 笔记可打标签且重复标签去重

当用户给一条已有笔记追加标签时，系统应把标签存入该条并保证同一标签只出现一次。

- 状态：生效
- 来源：README · 最小项目功能设定（打标签去重）
- 关键定位：note.py 的 add_tag()
- 验收：tests/test_note.py

#### 场景：重复标签去重

- **WHEN** 给同一条笔记第二次添加标签 urgent
- **THEN** 该条笔记的标签列表仍只有一个 urgent
