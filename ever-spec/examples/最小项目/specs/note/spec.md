# 主规格 · 快记 note（30 秒样例）

> 本文件是「系统当前承诺什么」的唯一真相源：每条需求 = 一个 `#### R-x` 块 ＋ 一句 EARS ＋ 一行验收。
> 范围与优先级看 README（本项目无 PRD）；进度权威＝changes/ 里 tasks.md 的勾选（降级声明在 README）。

#### R-1 | 新增笔记立即落盘并编号

当用户提交一条新笔记时，系统应把笔记内容与提交时刻写入 store.json 并返回自 1 起的编号。

- 状态：生效
- 关键定位：note.py 的 add_note()
- 验收：tests/test_note.py

#### 场景：普通新增

- **WHEN** 提交内容为「买牛奶」的新笔记
- **THEN** 返回编号 1，且 store.json 出现该条记录

#### R-2 | 清空 200 条笔记快于 500ms

系统应在清空 200 条笔记时于 500ms 内完成写盘并返回清除条数。

- 状态：生效
- 关键定位：note.py 的 clear_notes()
- 验收：tests/test_note.py 测量命令：py bench_note.py
