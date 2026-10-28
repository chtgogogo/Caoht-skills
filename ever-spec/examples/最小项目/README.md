# 最小项目 · EverSpec 30 秒上手

无管线最小树：`specs/` 主规格 ＋ `changes/` 进行中变更 ＋ `tests/` 真测试。没有 `docs/pipeline/`，
照样能立约、能开变更包、能跑尺——这就是 EverSpec 的无管线模式（C06/C07/C11/C17 自动降警不拦人）。

## 30 秒看到红绿

```
py <EverSpec根>/scripts/spec_check.py check --root <本目录>
```

退出码 0（含警）＝这层咬合成立；每条警都点名「哪条断言＋哪个文件＋人话原因」。
预期警＝无管线降级组：C06（无卡接）、C07（勾选无从对账）、C11（无 PRD）、
C15（场景块可选增强）、C17（无追踪矩阵）。

## 降级声明（无管线口径 · 进度权威）

**本项目进度权威 = `changes/add-tag/tasks.md` 的勾选**（没有 state.json，也没有任务板）。
禁两处各写各的：进度状态只改 tasks.md 勾选，别处只准放指针指向这里。

## 三个命令

```
py tests/test_note.py    # 现有测试（覆盖度验证）
py bench_note.py         # R-2 阈值的测量命令（超 500ms 退 1）
py <EverSpec根>/scripts/spec_check.py check --root <本目录>
```

跑测试与压测会在本目录生成 store.json（数据产物，不属规格层）。
